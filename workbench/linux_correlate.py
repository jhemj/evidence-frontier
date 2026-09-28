"""Deterministic links with explicit provenance; no PID/time-only attribution."""
import json
from collections import defaultdict

_UNSUPPORTED_SCOPE = ('query', 'account', 'time_from', 'time_to', 'cursor', 'source_offset',
                      'byte_offset', 'byte_length')


def _path_matches(candidate, requested):
    """Correlation path selectors are exact-or-subtree, never basename matches."""
    if not requested:
        return True
    if not isinstance(candidate, str):
        return False
    candidate = candidate.rstrip('/') or '/'
    requested = requested.rstrip('/') or '/'
    if requested == '/':
        return candidate.startswith('/')
    return candidate == requested or (requested != '/' and candidate.startswith(requested + '/'))


def _scope_error(request):
    if request is None:
        return None
    unsupported = []
    for name in _UNSUPPORTED_SCOPE:
        value = getattr(request, name, None)
        if name == 'source_offset': bad = value not in (None, '')
        elif name == 'byte_length': bad = value not in (None, 8192)
        else: bad = value not in (None, '', 0)
        if bad: unsupported.append(name)
    return ('Unsupported correlate scope filter(s): ' + ', '.join(unsupported)) if unsupported else None


def _linked_provenance(event):
    """Project a child record without dropping its temporal/identity basis."""
    fields = event.get('fields', {})
    out = {'source_location': event.get('source_location'), 'type': event.get('type'),
           'timestamp': event.get('timestamp'), 'source_sha256': fields.get('source_sha256')}
    for key in ('time_basis', 'time_record', 'path', 'partition_offset', 'inode',
                'source_offset', 'byte_offset', 'byte_length', 'line', 'source_row',
                'locator_basis', 'audit_id', 'id', 'event_id', 'raw_timestamp',
                'original_timestamp'):
        if key in fields:
            out[key] = fields[key]
    if 'id' in event:
        out['event_id'] = event['id']
    return out


def correlate(run, request=None):
    error = _scope_error(request)
    if error:
        return {'tool': 'correlate', 'status': 'unsupported', 'complete': False,
                'observations': [], 'error': error}
    path_filter = getattr(request, 'path', '') if request else ''
    partition_filter = getattr(request, 'partition_offset', None) if request else None
    inode_filter = getattr(request, 'inode', None) if request else None
    limit = getattr(request, 'limit', None) if request else None
    configs=[]; calls=defaultdict(list); audits=defaultdict(list); sources={}
    manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    for source in manifest['sources']:
        sources[(source.get('partition_offset'),source.get('path'))]=source
    with (run/'events.ndjson').open(encoding='utf-8') as stream:
        for line in stream:
            event=json.loads(line); f=event['fields']; typ=event['type']
            if typ=='linux_persistence': configs.append(event)
            if typ in ('linux_cron_call','linux_command'):
                for ref in f.get('referenced_paths',[]):
                    if ref.get('absolute') and len(calls[(f['partition_offset'],ref['absolute'])])<10:
                        calls[(f['partition_offset'],ref['absolute'])].append(event)
            if typ=='linux_audit' and f.get('audit_id'):
                # Native audit identifier includes epoch and serial; scope it by
                # image volume and log source. Never group by a recycled PID.
                key=(f['partition_offset'],f['path'],f['audit_id'])
                if len(audits)<5000 or key in audits:
                    if len(audits[key])<40: audits[key].append(event)
    observations=[]
    for event in configs:
        f=event['fields']
        for ref in f.get('referenced_paths',[]):
            path=ref.get('absolute')
            if not path or path in ('/dev/null','/bin/sh','/bin/bash'):continue
            partition = f.get('partition_offset')
            key=(partition,path); target=sources.get(key); linked=calls.get(key,[])
            # An inode selector is a strict file identity selector.  Unknown
            # inode metadata cannot satisfy it, even when the path matches.
            target_inode = target.get('inode') if target else None
            if partition_filter is not None and partition != partition_filter: continue
            source_path, source_inode = f.get('path'), f.get('inode')
            source_match = _path_matches(source_path, path_filter) and (inode_filter is None or source_inode == inode_filter)
            target_match = _path_matches(path, path_filter) and (inode_filter is None or target_inode == inode_filter)
            if not (source_match or target_match): continue
            facts={'configuration_observed':True, 'target_preserved_now':bool(target and target.get('sha256')),
                   'lookup_scope':'collected_manifest_only',
                   'target_lookup':'matched' if target else 'not_in_collected_manifest',
                   'cron_invocation_records':sum(e['type']=='linux_cron_call' for e in linked),
                   'command_records':sum(e['type']=='linux_command' for e in linked),
                   'program_execution_confirmed':False, 'objective_success_confirmed':False}
            fields={**f, 'target_path':path, 'stage':'지속성 설정·참조 파일·호출 기록 대조', 'facts':facts,
                    'target_source_sha256':target.get('sha256') if target else None,
                    'linked_records_scope':'보존 자료에서 대상별 최대 10개 연결 표본. 고유 실행 횟수나 전체 호출 횟수가 아님.',
                    'linked_records':[_linked_provenance(e) for e in linked],
                    'interpretation_limit':'경로 연결과 수집된 manifest 내 대상만 확인. not_in_collected_manifest는 실제 파일 부재·삭제를 뜻하지 않으며, 현재 파일과 과거 실행 파일의 동일성·실제 실행·목적 달성·승인 여부 및 수집 범위 밖 기록은 미확인.'}
            observations.append({'type':'linux_persistence_link','source_location':event['source_location'], 'timestamp':event['timestamp'],'fields':fields})
    for (_,_,native_id),events in audits.items():
        first=events[0]; fields={**first['fields'], 'audit_id':native_id, 'stage':'동일 native audit 사건 묶음',
            'records':[{'source_location':e['source_location'],'excerpt':e['fields']['excerpt']} for e in events],
            'interpretation_limit':'동일 audit epoch:serial과 원본 파일 범위에서 묶음. 다른 부팅·호스트·PID의 동일 행위로 확장하지 않음.'}
        audit_path=fields.get('path')
        if partition_filter is not None and fields.get('partition_offset') != partition_filter: continue
        if not _path_matches(audit_path, path_filter): continue
        if inode_filter is not None and fields.get('inode') != inode_filter: continue
        observations.append({'type':'linux_audit_group','source_location':first['source_location'],'timestamp':first['timestamp'],'fields':fields})
    total = len(observations)
    return {'tool':'deterministic-linux-correlation-1','status':'partial','complete':False,
            'scope':'보존된 설정·명령·cron·audit 레코드와 동일 볼륨의 원문 원장',
            'applied_scope':{'path':path_filter,'partition_offset':partition_filter,'inode':inode_filter},
            'observations':observations[:min(1000, limit) if isinstance(limit, int) else 1000],
            'truncated':total > (min(1000, limit) if isinstance(limit, int) else 1000),
            'omitted_records':max(0,total-(min(1000,limit) if isinstance(limit,int) else 1000)),
            'relations_found':total, 'error':'행위 단계 사이의 미확인 연결은 승격하지 않음; 전체 원본의 부재 판단 아님'}
