"""Evidence-indexed session leads; candidate linkage is never actor attribution."""
from collections import defaultdict
import hashlib
import json


def project(observations):
    groups=defaultdict(list)
    for o in observations:
        f=o['fields']
        if o['type'] not in {'linux_authentication','linux_session','linux_login_record','linux_command','linux_cron_call','linux_audit','linux_audit_group'}:continue
        # Without recorded identity there is no safe shared session grouping.
        if f.get('session_id'):
            basis='recorded_session';identifier=str(f['session_id'])
        elif f.get('audit_id'):
            basis='native_audit';identifier=str(f['audit_id'])
        elif f.get('user') and o.get('timestamp'):
            basis='account_day_candidate';identifier=f['user']+':'+o['timestamp'][:10]
        else:continue
        scope=(o.get('evidence_id'),f.get('partition_offset'),f.get('boot_id'),basis,identifier,
               f.get('path') if basis!='account_day_candidate' else None)
        groups[scope].append(o)
    rows=[]
    from .evidence_semantics import observation_time, compare_times, exact_utc_ns
    for scope,items in sorted(groups.items(),key=lambda x:str(x[0])):
        def order(o):
            ns=exact_utc_ns(o.get('timestamp'))
            return (0,ns,o['id']) if ns is not None else (1,o.get('timestamp') or '',o['id'])
        items.sort(key=order)
        # Chronology preserves original timestamps/time basis; ordering across
        # unknown clocks is expressly not a causal or identity relationship.
        rows.append({'id':hashlib.sha256(json.dumps(scope).encode()).hexdigest()[:20],
            'evidence_id':scope[0],'partition_offset':scope[1],'boot_id':scope[2],
            'link_basis':scope[3],'key':scope[4],'attribution':'unconfirmed',
            'observation_ids':[o['id'] for o in items],
            'adjacent_time_associations':[compare_times(a,b) for a,b in zip(items,items[1:])],
            'records':[{'observation_id':o['id'],'timestamp':o.get('timestamp'),
                'time_semantics':observation_time(o),
                'time_basis':o['fields'].get('time_basis','unknown'),'source_location':o['source_location'],
                'user':o['fields'].get('user'),'target_user':o['fields'].get('target_user'),
                'command':o['fields'].get('command'),'type':o['type']} for o in items],
            'limitation':'일치하는 기록 키에 따른 조사 연결 후보. 재부팅·PID 재사용·시간 오차·계정 공유를 배제하지 못하며 동일 행위자/실행 성공의 증거가 아님.',
            'alternative':'승인된 운영·점검·예약작업 또는 같은 계정의 별도 접속'})
    return rows
