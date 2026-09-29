"""Read a declared safe evidence bundle without extracting paths or trusting verdicts."""
import hashlib
import json
from pathlib import PurePosixPath
import re
import stat
from zipfile import ZipFile
from .windows_analysis import normalize_event, normalize_srum, powershell_sessions, profile_channel

MAX_MEMBER = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024


def read_bundle(path):
    with ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > 256 or sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ValueError('Safe bundle size/member budget exceeded')
        names = set()
        for item in infos:
            name = PurePosixPath(item.filename)
            if (item.filename in names or name.is_absolute() or '..' in name.parts or '\\' in item.filename
                    or ':' in item.filename or item.is_dir() or stat.S_ISLNK(item.external_attr >> 16)
                    or name.suffix.lower() not in ('.json', '.csv', '.txt', '.log')
                    or item.file_size > MAX_MEMBER or item.flag_bits & 1):
                raise ValueError('Unsafe, duplicate or unsupported bundle member')
            names.add(item.filename)
        if not {'BUNDLE_SUMMARY.json', 'MANIFEST_SHA256.txt'}.issubset(names):
            raise ValueError('Only manifest-bound safe evidence bundles are supported')
        members = {i.filename: archive.read(i) for i in infos}
    expected = {}
    for line in members['MANIFEST_SHA256.txt'].decode('utf-8-sig').splitlines():
        match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?(.+)', line)
        if not match or match[2] in expected:
            raise ValueError('Invalid/duplicate bundle manifest entry')
        expected[match[2]] = match[1].lower()
    if set(expected) != names - {'MANIFEST_SHA256.txt'}:
        raise ValueError('Bundle manifest does not cover every member')
    for name, digest in expected.items():
        if hashlib.sha256(members[name]).hexdigest() != digest:
            raise ValueError('Bundle member hash mismatch: ' + name)
        if members[name].startswith((b'MZ', b'\x7fELF')):
            raise ValueError('Executable bytes rejected from safe bundle')
    summary = json.loads(members['BUNDLE_SUMMARY.json'])
    if summary.get('schema') != 'acas-is.safe-evidence-bundle/v1' or summary.get('executable_payloads_included') is not False:
        raise ValueError('Unsupported evidence bundle contract')
    return members, expected, summary


def inspect_bundle(path):
    members, hashes, summary = read_bundle(path)
    observations, coverage, diagnostics = [], [], []
    documents = {}
    for name, data in members.items():
        if not name.endswith('.json'):
            continue
        try:
            documents[name] = json.loads(data)
        except (ValueError, UnicodeError) as ex:
            diagnostics.append({'source': name, 'error': str(ex), 'status': 'failed'})

    def add(event, member, pointer):
        event['fields'].update(imported_normalized=True, raw_source_reverified=False,
            source_member=member, json_pointer=pointer, source_sha256=hashes[member],
            locator_basis='ZIP member JSON pointer; not original EVTX/NTFS byte coordinates',
            original_source_location=event['source_location'])
        event['source_location'] = f'{path.name}!{member}#{pointer}'
        observations.append(event)

    ps_name = 'evidence/powershell_ioc_summary.json'
    ps = documents.get(ps_name, {})
    classic, operational = ps.get('classic_log', {}), ps.get('operational_log', {})
    starts = classic.get('start_events', [])
    failures = operational.get('download_errors', [])
    counts = powershell_sessions(starts, failures, operational.get('first_utc'), operational.get('last_utc'))
    for index, row in enumerate(starts):
        try:
            add(normalize_event(row, 'Windows PowerShell.evtx', index + 1, summary.get('case_id', 'unknown')),
                ps_name, f'/classic_log/start_events/{index}')
        except (TypeError, ValueError) as ex:
            diagnostics.append({'source': ps_name, 'ordinal': index, 'error': str(ex), 'status': 'failed'})
    for index, row in enumerate(failures):
        try:
            event = normalize_event({**row, 'event_id': 4100}, 'Microsoft-Windows-PowerShell/Operational',
                                    index + 1, summary.get('case_id', 'unknown'))
            event['type'] = 'windows_network'
            event['fields']['network_state'] = ('failed' if row.get('normalized_error') == 'remote_server_connection_failed'
                                                else 'indeterminate')
            add(event, ps_name, f'/operational_log/download_errors/{index}')
        except (TypeError, ValueError) as ex:
            diagnostics.append({'source': ps_name, 'ordinal': index, 'error': str(ex), 'status': 'failed'})
    coverage.extend([
        profile_channel([o for o in observations if o['type'] == 'windows_powershell_start'], 'Windows PowerShell',
                        classic.get('total_records'), classic.get('first_start_utc'), classic.get('last_start_utc'), classic.get('errors', [])),
        profile_channel([o for o in observations if o['type'] == 'windows_network'], 'PowerShell Operational',
                        operational.get('total_records'), operational.get('first_utc'), operational.get('last_utc'), operational.get('errors', []))])
    srum_name = 'evidence/srum_ioc_summary.json'
    srum = documents.get(srum_name, {})
    for index, row in enumerate(srum.get('application', {}).get('rows', [])):
        try:
            add(normalize_srum(row, 'application', 'SRUDB.dat', index + 1, summary.get('case_id', 'unknown')),
                srum_name, f'/application/rows/{index}')
        except (TypeError, ValueError) as ex:
            diagnostics.append({'source': srum_name, 'ordinal': index, 'error': str(ex), 'status': 'failed'})
    if srum.get('network_data_export', {}).get('error'):
        coverage.append({'source': 'SRUM network_data', 'status': 'partial', 'unit': 'records',
            'error': srum['network_data_export']['error'], 'fallback': srum.get('network_data_raw_fallback', {}),
            'absence_is_refutation': False, 'raw_source_reverified': False})
    # Imported analyst findings remain claims, not new independent observations.
    findings = documents.get('incident/case_findings.json', {})
    for index, finding in enumerate(findings.get('findings', [])):
        add({'type': 'windows_prior_interpretation', 'timestamp': None,
             'source_location': f'imported finding:{finding.get("finding_id")}',
             'fields': {'path': '', 'finding': finding, 'interpretation_limit': 'Prior analyst conclusion; revalidate against sources, not independent proof.'}},
            'incident/case_findings.json', f'/findings/{index}')
    artifacts_name = 'evidence/windows_execution_ioc_summary.json'
    for index, artifact in enumerate(documents.get(artifacts_name, {}).get('artifacts', [])):
        add({'type': 'windows_file', 'timestamp': None, 'source_location': artifact.get('path', ''),
             'fields': {**artifact, 'interpretation_limit': 'Filesystem timestamps/hash/reputation are not path-specific execution proof.'}},
            artifacts_name, f'/artifacts/{index}')
    return {'observations': observations, 'source_coverage': coverage, 'diagnostics': diagnostics,
            'session_summary': counts, 'members': members, 'member_hashes': hashes,
            'bundle_summary': summary, 'source_kind': 'imported_normalized_bundle',
            'raw_source_reverified': False, 'verified_member_count': len(hashes)}
