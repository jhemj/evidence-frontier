"""Windows follow-ups operate on retained records, never Linux image paths or a shell."""
import hashlib
import json
import ntpath
from pathlib import Path
from collections import defaultdict
from .evidence_semantics import windows_path


_UNSUPPORTED_CORRELATE_SCOPE = ('query', 'account', 'time_from', 'time_to', 'cursor',
                                'partition_offset', 'inode', 'source_offset',
                                'byte_offset', 'byte_length')


def _path_matches(candidate, requested):
    if not requested:
        return True
    candidate = windows_path(candidate)
    requested = windows_path(requested).rstrip('\\/')
    return bool(candidate and requested and
                (candidate == requested or candidate.startswith(requested + '\\')))


def correlate(run, image, manifest, request=None):
    if request is not None:
        unsupported = []
        for name in _UNSUPPORTED_CORRELATE_SCOPE:
            value = getattr(request, name, None)
            if name in ('source_offset','partition_offset','inode'): bad = value not in (None, '')
            elif name == 'byte_length': bad = value not in (None, 8192)
            else: bad = value not in (None, '', 0)
            if bad: unsupported.append(name)
        if unsupported:
            return {'tool': 'correlate', 'status': 'unsupported', 'complete': False,
                    'observations': [],
                    'error': 'Unsupported correlate scope filter(s): ' + ', '.join(unsupported)}
    path_filter = getattr(request, 'path', '') if request else ''
    limit = getattr(request, 'limit', None) if request else None
    paths, tasks = defaultdict(dict), defaultdict(dict)
    with (run / 'events.ndjson').open(encoding='utf-8') as stream:
        for line in stream:
            event = json.loads(line); f = event['fields']; path = windows_path(f.get('path'))
            if path and ('\\' in path or '/' in path):
                scope = (f.get('os_instance'), ntpath.basename(path))
                paths[scope][path] = event['source_location']
            if event['type'] == 'windows_task' and f.get('task_identity'):
                tasks[(f.get('os_instance'), f['task_identity'])][f['version_sha256']] = {
                    'source_location': event['source_location'], 'path': path,
                    'task_uri': f.get('task_uri'), 'os_instance': f.get('os_instance')}
    records = []
    for (instance, basename), variants in paths.items():
        matched = [path for path in variants if _path_matches(path, path_filter)]
        if len(variants) < 2 or not matched:
            continue
        records.append({'type': 'windows_counterevidence', 'timestamp': None,
            'source_location': f'{image.name}:basename-collision:{basename}',
            'fields': {'path': basename, 'os_instance': instance, 'distinct_full_paths': variants,
                       'scope_matched_paths': matched,
                       'interpretation_limit': 'Same basename is not same file. Full collision context is retained; scope-matched paths are not proof that the other comparison paths are the requested target.'}})
    for (task_instance, identity), versions in tasks.items():
        selected = {version: details for version, details in versions.items()
                    if (_path_matches(details.get('path'), path_filter) or
                        _path_matches(details.get('task_uri'), path_filter))}
        if not selected:
            continue
        # Keep the established compact output (version -> source locator),
        # while applying path scope against provenance retained above.
        versions = {version: details['source_location'] for version, details in selected.items()}
        records.append({'type': 'windows_correlation', 'timestamp': None,
            'source_location': f'{image.name}:task-identity:{identity}',
            'fields': {'task_identity': identity, 'os_instance':task_instance, 'versions': versions,
                       'interpretation_limit': 'OS-scoped URI/version grouping only; no execution or author attribution.'}})
    data = json.dumps(records, ensure_ascii=False, sort_keys=True).encode()
    digest = hashlib.sha256(data).hexdigest()
    relative = f'followup/{digest}.json'
    (run / 'followup').mkdir(exist_ok=True)
    (run / relative).write_bytes(data)
    for row in records:
        row['fields'].update(artifact_path=f'{run.name}/{relative}', source_sha256=digest,
                             source_complete=True, locator_basis='derived correlation JSON; inspect referenced source locators')
    cap = min(100, limit) if isinstance(limit, int) else 100
    return {'tool': 'correlate', 'observations': records[:cap], 'status': 'partial' if len(records)>cap else 'covered',
            'complete': len(records)<=cap, 'omitted_records': max(0,len(records)-cap),
            'applied_scope':{'path':path_filter},
            'scope': 'Full-path collisions and task version grouping over retained normalized records only.'}


def execute_tool(evidence_root, analysis_root, body):
    from .worker import safe_path
    from .retrieval import search, read_source
    try:
        image = safe_path(evidence_root, body.evidence_path)
        run = Path(analysis_root) / body.run_id
        manifest = json.loads((run / 'manifest.json').read_bytes())
        if manifest.get('platform') != 'windows' or manifest.get('image_path') != str(image.resolve()):
            raise ValueError('Windows run/source binding mismatch')
        hashes = json.loads((run / 'SHA256SUMS.json').read_bytes())
        for name in ('manifest.json','events.ndjson','filesystem_inventory.ndjson'):
            with (run / name).open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=hashes[name]:
                    raise ValueError('Retained Windows source hash mismatch')
        if body.request.tool == 'correlate':
            return correlate(run, image, manifest, body.request)
        if body.request.tool not in ('search', 'read_source'):
            return {'tool': body.request.tool, 'status': 'unsupported', 'complete': False, 'observations': [],
                    'error': 'Windows follow-up supports retained search/read_source/correlate only. Raw extraction/static/archive plugins are not implemented.'}
        result = (search if body.request.tool == 'search' else read_source)(run, image, manifest, body.request)
        for event in result['observations']:
            if event['type'].startswith('linux_'):
                event['type'] = 'windows_source_excerpt'
            event['fields']['interpretation_limit'] = 'Retained normalized source only; not an independent parser or proof of execution/success.'
        return result
    except Exception as ex:
        return {'tool': body.request.tool, 'status': 'failed', 'complete': False, 'observations': [], 'error': str(ex)}
