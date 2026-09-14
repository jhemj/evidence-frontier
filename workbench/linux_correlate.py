"""Deterministic links with explicit provenance; no PID/time-only attribution."""
import json
from collections import defaultdict

def correlate(run):
    configs=[]; calls=defaultdict(list); audits=defaultdict(list); sources={}
    manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
    for source in manifest['sources']: sources[(source['partition_offset'],source['path'])]=source
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
            key=(f['partition_offset'],path); target=sources.get(key); linked=calls.get(key,[])
            facts={'configuration_observed':True, 'target_preserved_now':bool(target and target.get('sha256')),
                   'cron_invocation_records':sum(e['type']=='linux_cron_call' for e in linked),
                   'command_records':sum(e['type']=='linux_command' for e in linked),
                   'program_execution_confirmed':False, 'objective_success_confirmed':False}
            fields={**f, 'target_path':path, 'stage':'지속성 설정·참조 파일·호출 기록 대조', 'facts':facts,
                    'target_source_sha256':target.get('sha256') if target else None,
                    'linked_records':[{'source_location':e['source_location'], 'timestamp':e['timestamp'],
                                       'type':e['type'], 'source_sha256':e['fields']['source_sha256']} for e in linked],
                    'interpretation_limit':'경로 연결만 확인. 현재 파일과 과거 실행 파일의 동일성·실제 실행·목적 달성·승인 여부는 미확인. 검색 수집 범위 밖 기록은 제외.'}
            observations.append({'type':'linux_persistence_link','source_location':event['source_location'], 'timestamp':event['timestamp'],'fields':fields})
    for (_,_,native_id),events in audits.items():
        first=events[0]; fields={**first['fields'], 'audit_id':native_id, 'stage':'동일 native audit 사건 묶음',
            'records':[{'source_location':e['source_location'],'excerpt':e['fields']['excerpt']} for e in events],
            'interpretation_limit':'동일 audit epoch:serial과 원본 파일 범위에서 묶음. 다른 부팅·호스트·PID의 동일 행위로 확장하지 않음.'}
        observations.append({'type':'linux_audit_group','source_location':first['source_location'],'timestamp':first['timestamp'],'fields':fields})
    return {'tool':'deterministic-linux-correlation-1','status':'partial','complete':False,
            'scope':'보존된 설정·명령·cron·audit 레코드와 동일 볼륨의 원문 원장',
            'observations':observations[:1000], 'truncated':len(observations)>1000,
            'relations_found':len(observations), 'error':'행위 단계 사이의 미확인 연결은 승격하지 않음; 전체 원본의 부재 판단 아님'}
