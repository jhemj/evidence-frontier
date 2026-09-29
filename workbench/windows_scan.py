"""Bounded read-only Windows collection and normalized-bundle ingestion.

No mounted image, evidence execution, payload restoration or network access.
Retained files are normalized JSON, with original-source and derived hashes distinct.
"""
import hashlib
import io
import json
import logging
from pathlib import Path
import sys
import uuid
from xml.etree import ElementTree
from .parser_contract import consume
from .windows_analysis import VERSION, normalize_event, normalize_srum, task_identity, profile_channel

MAX_SOURCE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_RECORDS = 50000
MAX_INDEX = 5000
CHANNELS = ('Windows PowerShell.evtx', 'Microsoft-Windows-PowerShell%4Operational.evtx',
            'Security.evtx', 'System.evtx', 'Microsoft-Windows-TaskScheduler%4Operational.evtx',
            'Microsoft-Windows-Windows Defender%4Operational.evtx', 'Microsoft-Windows-Sysmon%4Operational.evtx')
UNSUPPORTED = ['deleted/unallocated recovery', 'BitLocker key entry', 'VSS/Windows.old cross-instance reconstruction',
               'USN version/wrap recovery', 'AV quarantine payload decoding', 'TaskCache/deleted task reconstruction',
               'native Prefetch/Amcache/Shimcache/browser artifact adapters',
               'memory, packet capture and remote server acquisition']


def _json(value):
    return json.dumps(value, ensure_ascii=False, default=str, sort_keys=True)


def parse_task(data, path, os_instance):
    root = ElementTree.fromstring(data)
    local = lambda el: el.tag.rsplit('}', 1)[-1]
    uri = next((el.text for el in root.iter() if local(el) == 'URI' and el.text), None)
    # No URI: do not merge unrelated task files by basename.
    uri = uri or path
    actions = [{local(c): c.text or '' for c in el} for el in root.iter() if local(el) == 'Exec']
    identity = task_identity(os_instance, uri, actions, path)
    identity.update(actions_sha256=identity['version_sha256'], version_sha256=hashlib.sha256(data).hexdigest(),
                    version_basis='entire retained Task XML including triggers/principal/settings')
    return {'type': 'windows_task', 'timestamp': None, 'source_location': path,
            'fields': {'path': path, **identity, 'actions': actions,
                       'interpretation_limit': 'Task configuration only. Encoded arguments are not executed or decoded here.'}}


class Collector:
    def __init__(self, image, output_root):
        from .disk_budget import require_space
        require_space([(output_root,4*MAX_TOTAL)])
        self.image = Path(image)
        self.run = Path(output_root) / ('RUN-' + uuid.uuid4().hex)
        (self.run / 'objects').mkdir(parents=True)
        self.sources, self.coverage, self.index, self.inventory = [], [], [], []
        self.records = self.bytes_read = 0
        self.environment = {'platform': 'windows', 'source_kind': 'native_read_only', 'raw_source_reverified': True}
        self.event_stream = (self.run / 'events.ndjson').open('w', encoding='utf-8')

    def blob(self, data, path, complete=True, original_hash=None):
        digest = hashlib.sha256(data).hexdigest()
        relative = 'objects/' + digest + '.json'
        destination = self.run / relative
        if not destination.exists():
            from .disk_budget import require_space
            require_space([(destination,len(data))])
            destination.write_bytes(data)
        source = {'path': path, 'relative_path': relative, 'sha256': digest, 'complete': complete,
                  'status': 'normalized', 'partition_offset': None, 'inode': None,
                  'source_offset': 0, 'locator_basis': 'retained normalized JSON bytes; not original artifact coordinates',
                  'hash_scope': 'retained normalized artifact', 'original_source_sha256': original_hash}
        self.sources.append(source)
        return source

    def add(self, event, source, ordinal):
        fields = event['fields']
        fields.update(artifact_path=f'{self.run.name}/{source["relative_path"]}',
                      source_sha256=source['sha256'], source_complete=source['complete'],
                      original_source_sha256=source.get('original_source_sha256'),
                      hash_scope=source['hash_scope'],
                      raw_source_reverified=self.environment['raw_source_reverified'],
                      locator_basis=source['locator_basis'], source_row=ordinal,
                      time_basis=fields.get('time_basis', 'not established'), os_instance=fields.get('os_instance', self.image.name))
        self.event_stream.write(_json(event) + '\n')
        self.records += 1
        if len(self.index) < MAX_INDEX:
            self.index.append(event)

    def source(self, path, records, parse, original_hash=None):
        failed = []
        def retain(data, ordinal):
            source = self.blob(data, f'{path}:rejected:{ordinal}', False, original_hash)
            failed.append(source)
            return f'{self.run.name}/{source["relative_path"]}'
        result = consume(records, parse, max(0, MAX_RECORDS - self.records), retain)
        rows = result.pop('rows')
        payload = ''.join(_json(row) + '\n' for row in rows).encode()
        source = self.blob(payload, path, result['complete'], original_hash)
        for ordinal, event in enumerate(rows, 1):
            self.add(event, source, ordinal)
        cov = {'source': path, 'unit': 'records', 'files_discovered': 1, 'files_read': 1,
               'retained_artifacts': 1 + len(failed), **result}
        cov.update(profile_channel(rows, path, errors=result['failures']))
        cov['status'] = result['status']; cov['complete'] = result['complete']
        self.coverage.append(cov)

    def read(self, node, path):
        size = node.stat().st_size
        self.inventory.append({'path': path, 'size': size, 'partition_offset': None, 'inode': None})
        if size > MAX_SOURCE or self.bytes_read + size > MAX_TOTAL:
            raise ValueError('Source/total byte budget reached; no absence conclusion')
        with node.open('rb') as stream:
            data = stream.read(MAX_SOURCE + 1)
        self.bytes_read += len(data)
        if len(data) != size:
            raise ValueError('Short/oversized artifact read')
        return data

    def gap(self, path, error, status='unsupported'):
        self.coverage.append({'source': path, 'status': status, 'complete': False,
                              'error': str(error)[:1000], 'absence_is_refutation': False})

    def evtx(self, node, path, os_instance):
        from dissect.eventlog.evtx import Evtx
        data = self.read(node, path)
        collector = self
        class ParseWarnings(logging.Handler):
            def emit(self, record):
                # The underlying parser sometimes skips damaged chunks internally.
                collector.gap(path, record.getMessage(), 'partial')
        handler = ParseWarnings(level=logging.WARNING)
        logger = logging.getLogger('dissect.eventlog')
        logger.addHandler(handler)
        try:
            self.source(path, Evtx(io.BytesIO(data)),
                        lambda raw, n: normalize_event(raw, path, n, os_instance), hashlib.sha256(data).hexdigest())
        finally:
            logger.removeHandler(handler)

    def finish(self):
        self.event_stream.close()
        for area in UNSUPPORTED:
            self.gap(area, 'Not implemented in this bounded Windows adapter')
        env = {**self.environment, 'run_id': self.run.name, 'events': self.records,
               'source_coverage': self.coverage, 'index_omitted_records': max(0, self.records - len(self.index)),
               'unsupported': UNSUPPORTED, 'safe_artifact_policy': 'no execution / no egress / no decoded executable files'}
        (self.run / 'filesystem_inventory.ndjson').write_text(''.join(_json(row) + '\n' for row in self.inventory), encoding='utf-8')
        manifest = {'version': VERSION, 'platform': 'windows', 'image': self.image.name, 'image_path': str(self.image.resolve()), 'run_id': self.run.name,
                    'sources': self.sources, 'environment': env, 'source_coverage': self.coverage,
                    'scopes': [], 'limitations': UNSUPPORTED, 'event_count': self.records}
        (self.run / 'manifest.json').write_text(_json(manifest), encoding='utf-8')
        hashes = {name: hashlib.sha256((self.run / name).read_bytes()).hexdigest()
                  for name in ('manifest.json', 'events.ndjson', 'filesystem_inventory.ndjson')}
        (self.run / 'SHA256SUMS.json').write_text(_json(hashes), encoding='utf-8')
        environment = {'type': 'windows_environment', 'timestamp': None, 'source_location': self.image.name,
            'fields': {**env, 'path': self.image.name, 'source_sha256': hashes['manifest.json'],
                       'artifact_path': f'{self.run.name}/manifest.json', 'locator_basis': 'collector manifest'}}
        result = {'status': 'partial', 'complete': False, 'truncated': self.records > len(self.index),
                  'tool': 'frontier-windows-readonly', 'version': VERSION, 'run_id': self.run.name,
                  'observations': [environment] + self.index, 'source_coverage': self.coverage,
                  'error': 'Bounded Windows support; see per-source coverage and unsupported areas. Not a complete-host examination.'}
        (self.run / 'result.json').write_text(_json(result), encoding='utf-8')
        return result


def scan(image, output_root):
    collector = Collector(image, output_root)
    try:
        suffix = Path(image).suffix.lower()
        if suffix == '.zip':
            from .windows_bundle import inspect_bundle
            bundle = inspect_bundle(Path(image))
            collector.environment.update(source_kind=bundle['source_kind'], raw_source_reverified=False,
                                         session_summary=bundle['session_summary'], verified_member_count=bundle['verified_member_count'])
            by_member = {}
            for name, data in bundle['members'].items():
                # These bytes have been allowlisted and SHA-256 verified in memory.
                by_member[name] = collector.blob(data, name)
            for n, event in enumerate(bundle['observations'], 1):
                collector.add(event, by_member[event['fields']['source_member']], n)
            collector.coverage.extend(bundle['source_coverage'] + bundle['diagnostics'])
        elif suffix in ('.ndjson', '.jsonl'):
            data = collector.read(Path(image), Path(image).name)
            collector.environment.update(source_kind='imported_normalized', raw_source_reverified=False)
            def rows():
                with io.StringIO(data.decode('utf-8-sig')) as stream:
                    for line in stream:
                        if line.strip():
                            yield line
            collector.source(Path(image).name, rows(), lambda raw, n: normalize_event(json.loads(raw), Path(image).name, n), hashlib.sha256(data).hexdigest())
        elif suffix == '.evtx':
            collector.evtx(Path(image), Path(image).name, Path(image).name)
        else:
            native(collector)
        return collector.finish()
    except Exception:
        collector.event_stream.close()
        raise


def native(c):
    from .image_target import open_target
    with open_target(c.image) as target:
        return _native(c, target)


def _native(c, target):
    if str(target.os).casefold() != 'windows':
        raise ValueError('Selected Windows investigation but target OS is not Windows. Create a case with the correct OS.')
    for key in ('hostname', 'os', 'version', 'architecture'):
        try:
            c.environment[key] = str(getattr(target, key))
        except Exception:
            c.environment[key] = None
    instance = c.image.name + ':current-windows'
    for filename in CHANNELS:
        path = 'sysvol/Windows/System32/winevt/Logs/' + filename
        try:
            c.evtx(target.fs.path(path), path, instance)
        except Exception as ex:
            c.gap(path, ex, 'partial' if isinstance(ex, ValueError) else 'unavailable')
    for directory in ('sysvol/Windows/System32/Tasks', 'sysvol/Windows/System32/Tasks_Migrated'):
        try:
            for n, node in enumerate(target.fs.path(directory).rglob('*')):
                if n >= 2048:
                    c.gap(directory, 'Task enumeration budget reached', 'partial'); break
                if not node.is_file():
                    continue
                path = str(node)
                try:
                    data = c.read(node, path)
                    if len(data) > 4 * 1024 * 1024:
                        raise ValueError('Task XML size budget exceeded')
                    c.source(path, [data], lambda raw, ordinal: parse_task(raw, path, instance), hashlib.sha256(data).hexdigest())
                except Exception as ex:
                    c.gap(path, ex, 'partial')
        except Exception as ex:
            c.gap(directory, ex, 'unavailable')
    for suffix in ('Paths', 'Processes', 'Extensions'):
        keypath = 'HKLM\\SOFTWARE\\Microsoft\\Windows Defender\\Exclusions\\' + suffix
        try:
            key = target.registry.key(keypath)
            values = ({'name': value.name, 'value': value.value} for value in key.values())
            c.source(keypath, values, lambda raw, n: {'type': 'windows_registry', 'timestamp': None,
                'source_location': f'{keypath}:value:{raw["name"]}',
                'fields': {'path': keypath, 'os_instance': instance, **raw,
                           'interpretation_limit': 'Current value; key last-write is not individual value creation time or actor attribution.'}})
        except Exception as ex:
            c.gap(keypath, ex, 'unavailable')
    path = 'sysvol/Windows/System32/sru/SRUDB.dat'
    try:
        from dissect.database.ese.tools.sru import SRU
        data = c.read(target.fs.path(path), path)
        sru = SRU(io.BytesIO(data))
        for table_name in ('application', 'network_data', 'network_connectivity'):
            try:
                table = sru.get_table(table_name=table_name)
                if table is None:
                    c.gap(path + ':' + table_name, 'Table not present', 'unavailable'); continue
                # Read by actual columns instead of constructing a fixed descriptor.
                # A new WakeCount column remains in raw_fields and marks degradation.
                def parse(entry, n):
                    raw = {col.name: entry[col.name] for col in table.columns}
                    return normalize_srum(raw, table_name, path, n, instance)
                c.source(path + ':' + table_name, sru.get_table_entries(table=table), parse, hashlib.sha256(data).hexdigest())
            except Exception as ex:
                c.gap(path + ':' + table_name, ex, 'partial')
    except Exception as ex:
        c.gap(path, ex, 'unavailable')


if __name__ == '__main__':
    result = scan(sys.argv[1], sys.argv[2])
    print(json.dumps({'run_id': result['run_id']}))
