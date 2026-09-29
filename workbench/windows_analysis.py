"""Windows record semantics and bounded correlations; never run evidence commands."""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import re
from .evidence_semantics import time_record, windows_path, activity_context, wow64_path

VERSION = 'windows-hunt-1'


def normalize_event(raw, path, ordinal=1, os_instance='unknown'):
    if not isinstance(raw, dict):
        raise ValueError('Windows event must be a mapping')
    fields = dict(raw.get('fields', raw))
    ts = raw.get('timestamp') or fields.get('ts_utc') or fields.get('ts') or fields.get('TimeCreated_SystemTime')
    timing = time_record(ts, 'EVTX UTC if encoded in source; naive values remain unresolved')
    eid = fields.get('event_id', fields.get('EventID'))
    eid = int(eid) if eid is not None else None
    channel = str(fields.get('channel') or fields.get('Channel') or path)
    provider = str(fields.get('provider') or fields.get('Provider_Name') or '')
    record_id = fields.get('event_record_id', fields.get('EventRecordID', ordinal))
    process_id = fields.get('execution_process_id', fields.get('Execution_ProcessID'))
    kind = 'windows_event'
    ps = 'powershell' in channel.casefold() or 'powershell' in provider.casefold()
    if ps and eid == 400:
        kind = 'windows_powershell_start'
    elif ps and eid == 4104:
        kind = 'windows_scriptblock'
    elif ('security' in channel.casefold() and eid == 4688) or ('sysmon' in provider.casefold() and eid == 1):
        kind = 'windows_process'
    fields.update(channel=channel, event_id=eid, event_record_id=str(record_id),
                  process_id=str(process_id) if process_id is not None else None,
                  os_instance=fields.get('os_instance') or os_instance, path=fields.get('path') or str(fields.get('Image') or fields.get('NewProcessName') or path),
                  time_record=timing, time_basis=timing['timezone_basis'], source_path=path,
                  artifact_semantics='engine/process start is not command completion; script content is not successful execution')
    fields['activity_context'] = activity_context(fields)
    if fields.get('literal_path'):
        fields['path_resolution'] = wow64_path(fields['literal_path'], fields.get('caller_bits'), fields.get('os_bits'), fields.get('redirection_enabled'), fields.get('windows_directory','C:\\Windows'))
    return {'type': kind, 'timestamp': timing['normalized_utc'],
            'source_location': f'{os_instance}:{path}:record:{record_id}', 'fields': fields}


def normalize_srum(raw, table, path, ordinal=1, os_instance='unknown'):
    if not isinstance(raw, dict):
        raise ValueError('SRUM record must be a mapping')
    expected = {'AutoIncId', 'TimeStamp', 'AppId', 'UserId', 'InterfaceLuid', 'L2ProfileId',
                'L2ProfileFlags', 'BytesSent', 'BytesRecvd'}
    unknown = sorted(set(raw) - expected) if table == 'network_data' else []
    timestamp = time_record(raw.get('TimeStamp', raw.get('ts')), 'SRUM source time')
    fields = {'path': str(raw.get('AppId', raw.get('app')) or ''), 'user': str(raw.get('UserId', raw.get('user')) or ''),
              'table': table, 'raw_fields': raw, 'os_instance': os_instance,
              'parser_degraded': bool(unknown), 'unmapped_fields': unknown,
              'time_record': timestamp, 'time_basis': timestamp['timezone_basis'],
              'interpretation_limit': 'Resource-accounting rows are not process counts, remote destinations, or proof of exfiltration.'}
    return {'type': 'windows_srum_application' if table == 'application' else 'windows_srum_network',
            'timestamp': timestamp['normalized_utc'], 'source_location': f'{os_instance}:{path}:{table}:row:{ordinal}', 'fields': fields}


def profile_channel(events, source, total_reported=None, first_reported=None, last_reported=None, errors=()):
    times = sorted(e['timestamp'] for e in events if e.get('timestamp'))
    return {'source': source, 'unit': 'records', 'parsed_records': len(events),
            'source_records_reported': total_reported, 'first_observed': times[0] if times else None,
            'last_observed': times[-1] if times else None, 'retention_first_reported': first_reported,
            'retention_last_reported': last_reported, 'errors': list(errors),
            'retention_continuity': 'not established', 'rollover': 'not established',
            'absence_is_refutation': False, 'status': 'partial' if errors else 'scope_recorded'}


def powershell_sessions(starts, failures, result_start=None, result_end=None, batch_window=10):
    """Count starts separately from provisional time clusters. PID is not a global identity."""
    valid, rejected, seen = [], [], set()
    for n, row in enumerate(starts):
        try:
            dt = datetime.fromisoformat(str(row['ts_utc']).replace('Z', '+00:00'))
            if dt.tzinfo is None:
                raise ValueError('naive event timestamp')
            key = (row.get('os_instance', 'unknown'), row.get('boot_id'), row.get('channel', 'Windows PowerShell'),
                   str(row['event_record_id']), row['ts_utc'])
            if key in seen:
                continue
            seen.add(key); valid.append((dt, row))
        except (KeyError, TypeError, ValueError) as ex:
            rejected.append({'ordinal': n + 1, 'error': str(ex)})
    valid.sort(key=lambda r: r[0])
    groups, current = [], {}
    for dt, row in valid:
        scope = (row.get('os_instance', 'unknown'), row.get('boot_id'))
        group = current.get(scope)
        if group is None or (dt - group['start']).total_seconds() > batch_window:
            group = {'start': dt, 'rows': [], 'scope': scope}; groups.append(group); current[scope] = group
        group['rows'].append(row)
    network = [(dt, r) for dt, r in valid if r.get('stage') == 'masqueraded_powershell'
               and str(r.get('behavior', '')).endswith('_iwr_iex')]
    lower = datetime.fromisoformat(result_start.replace('Z', '+00:00')) if result_start else None
    upper = datetime.fromisoformat(result_end.replace('Z', '+00:00')) if result_end else None
    matches = defaultdict(list); unmatched = []
    for error in failures:
        try:
            et = datetime.fromisoformat(error['ts_utc'].replace('Z', '+00:00'))
            possible = [(i, dt, row) for i, (dt, row) in enumerate(network)
                if row.get('execution_process_id') is not None
                and str(row['execution_process_id']) == str(error.get('execution_process_id'))
                and row.get('behavior') == error.get('behavior')
                and row.get('os_instance', 'unknown') == error.get('os_instance', 'unknown')
                and row.get('boot_id') == error.get('boot_id')
                and 0 <= (et - dt).total_seconds() <= 900]
            if len(possible) == 1 and error.get('normalized_error') == 'remote_server_connection_failed':
                matches[possible[0][0]].append((error['event_record_id'], et))
            else:
                unmatched.append(error.get('event_record_id'))
        except (KeyError, TypeError, ValueError):
            unmatched.append(error.get('event_record_id'))
    outcomes = []
    for i, (dt, row) in enumerate(network):
        covered = lower is not None and upper is not None and (lower <= dt <= upper or
                   any(lower <= et <= upper for _, et in matches[i]))
        outcomes.append({'start_record_id': row['event_record_id'], 'timestamp': row['ts_utc'],
            'state': 'failed' if matches[i] else 'indeterminate', 'in_retained_result_window': covered,
            'failure_record_ids': [rid for rid, _ in matches[i]],
            'coverage_basis': 'start or matching result retained; an attempt may begin before the first result event',
            'correlation_basis': 'same OS/boot when known, PID, action and bounded time; not actor identity'})
    counts = Counter(r['state'] for r in outcomes if r['in_retained_result_window'])
    return {'engine_starts': len(valid), 'batch_candidates': len(groups),
        'batch_basis': f'OS/boot-scoped {batch_window}s time clusters; task identity and actor not proven by proximity',
        'stage_counts': dict(Counter(r.get('stage', 'unknown') for _, r in valid)),
        'network_actions': len(network), 'retained_network_actions': sum(r['in_retained_result_window'] for r in outcomes),
        'out_of_window_actions': sum(not r['in_retained_result_window'] for r in outcomes),
        'retained_failures': counts['failed'], 'retained_indeterminate': counts['indeterminate'],
        'confirmed_successes': 0, 'success_basis': 'No positive success records supplied; not evidence of global absence',
        'outcomes': outcomes, 'unmatched_failure_records': unmatched, 'rejected_records': rejected}


def task_identity(os_instance, uri, actions, source_locator):
    identity = hashlib.sha256(json.dumps([os_instance, windows_path(uri)]).encode()).hexdigest()
    version = hashlib.sha256(json.dumps(actions, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {'task_identity': identity, 'task_uri': uri, 'os_instance': os_instance,
            'version_sha256': version, 'source_locator': source_locator,
            'limit': 'Same URI groups versions; file presence does not prove registration, execution or common author.'}


def ioc_checks(observations, maximum=8):
    """Source-linked, literal full-path requery in retained data, never IOC verdicts."""
    seen=set();checks=[]
    for o in observations:
        f=o['fields'];candidates=[]
        if o['type']=='windows_file':candidates.append(f.get('path'))
        if o['type']=='windows_task':candidates.extend(a.get('Command') for a in f.get('actions',[]))
        for path in candidates:
            if not isinstance(path,str) or not re.match(r'^[a-zA-Z]:[\\/]',path) or len(path)>200:continue
            key=windows_path(path)
            if key in seen:continue
            seen.add(key)
            checks.append({'tool':'search','query':path,'limit':20,
                'reason':'Observed full-path candidate from '+o['id']+'; requery retained sources, not independent corroboration or maliciousness.'})
            if len(checks)>=maximum:return checks
    return checks
