"""An explainable projection of immutable facts and successive AI assessments."""
from collections import Counter
from datetime import datetime, timezone


def instant(value):
    try:
        parsed=datetime.fromisoformat(value)
        if parsed.tzinfo is None:return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (TypeError,ValueError):return None


def project(data):
    active={e['id'] for e in data['evidence'] if e.get('connected',True)}
    obs={o['id']:o for o in data['observation'] if o['evidence_id'] in active}
    def origin(o):
        f=o['fields']
        return (o['evidence_id'],f.get('partition_offset'),f.get('path'),f.get('inode'),f.get('image_file_byte_offset',f.get('byte_offset')))
    record_times={origin(o):o for o in obs.values() if instant(o.get('timestamp')) and o['type'].startswith('linux_')}
    def record_time(o):
        if o['type']=='filesystem_time':return None
        if o['type']=='linux_detection' and not o.get('timestamp'):
            return record_times.get(origin(o),{}).get('timestamp')
        return o.get('timestamp')
    tasks={t['id']:t for t in data['task']}
    dossiers={d['id']:d for d in data.get('dossier',[]) if d['evidence_id'] in active and d['task_id'] in tasks
        and d.get('generation',0)==tasks[d['task_id']].get('retry_generation',0)}
    assessments={};history=[]
    for r in data.get('receipt',[]):
        if r.get('receipt_type')!='dossier_model':continue
        for f in r.get('output',{}).get('findings',[]):
            did=f.get('dossier_id')
            if did not in dossiers:continue
            old=assessments.get(did)
            if old and (old.get('timeline_role','핵심'),old['judgment'],old['reason'])!=(f.get('timeline_role','핵심'),f['judgment'],f['reason']):
                history.append({'id':r['id']+did,'at':r['created_at'],'title':f['title'],'before':old['judgment']+' · '+old.get('timeline_role','핵심'),
                    'after':f['judgment']+' · '+f.get('timeline_role','핵심'),'reason':f['reason'],'observation_ids':f['observation_ids']})
            assessments[did]=f
    findings=[]
    for did,d in dossiers.items():
        f=d.get('finding') or assessments.get(did)
        if f:findings.append((did,{**f,'review_status':d['status']},d['status']!='reviewed'))
        elif not d['baseline']:
            reason={'model_failed':'AI 검토에 실패했습니다. 원문 단서와 실패 이력을 보존합니다.',
                    'deferred':'검토 한도로 아직 판단하지 못한 단서입니다. 원문은 보존합니다.'}.get(d['status'],'원문에서 발견한 단서입니다. 정황과 경쟁 설명을 검토하고 있습니다.')
            if d.get('deferred_reason')=='outside_repair_scope':reason='이번 실패 단서 복구 대상이 아닙니다. 이전 판단이 있으면 누적 검토 이력에서 확인할 수 있습니다.'
            findings.append((did,{'title':d['title'],'judgment':None,'timeline_role':'핵심','review_status':d['status'],'reason':reason,'observation_ids':d['observation_ids']},True))
    if not dossiers:
        for j in data.get('judgments',[]):
            findings.extend((j['id']+'-'+str(i),f,False) for i,f in enumerate(j['findings']))
    referenced={oid for _,f,_ in findings for oid in f['observation_ids']}
    referenced.update(oid for d in dossiers.values() for oid in d.get('all_observation_ids',[]))
    raw_seen=set()
    for o in obs.values():
        if o['type']=='linux_detection' and o['id'] not in referenced:
            key=(origin(o),o['fields'].get('rule_id'))
            if key in raw_seen:continue
            raw_seen.add(key)
            findings.append((o['id'],{'title':o['fields']['title'],'judgment':None,'timeline_role':'핵심','reason':o['fields'].get('interpretation_limit',''), 'observation_ids':[o['id']]},True))
    cards=[]
    for fid,f,provisional in findings:
        sources=[obs[oid] for oid in f['observation_ids'] if oid in obs]
        times=sorted({instant(record_time(o)) for o in sources}-{None})
        cards.append({'id':fid,'title':f['title'],'judgment':f['judgment'],'role':f.get('timeline_role','핵심'),
            'stages':f.get('stages',[]),'reason':f['reason'],'provisional':provisional,'review_status':f.get('review_status','pending' if provisional else 'reviewed'),'start':times[0] if times else None,'end':times[-1] if times else None,
            'time_basis':'연결된 근거의 기록 시각 범위 · 행위 지속 시간이나 인과관계 확정 아님' if times else '행위 시각을 확인할 근거가 없음',
            'sources':[{'id':o['id'],'type':o['type'],'path':o['fields'].get('path',o['source_location']),
                'timestamp':record_time(o),'time_basis':o['fields'].get('time_basis') or ('동일 원문 위치의 파싱된 기록 시각' if record_time(o) and not o.get('timestamp') else None),
                'excerpt':str(o['fields'].get('command') or o['fields'].get('excerpt') or o['fields'].get('stage',''))[:360]} for o in sources[:8]]})
    unique={ (origin(o),o['type'],o['fields'].get('source_sha256')):o for o in obs.values() if o['type']!='filesystem_time'}
    times=[instant(record_time(o)) for o in unique.values()];dated=sorted(t for t in times if t)
    bins=[]
    if dated:
        begin=datetime.fromisoformat(dated[0]).timestamp();end=datetime.fromisoformat(dated[-1]).timestamp();span=max(1,end-begin)
        counts=Counter(min(59,int((datetime.fromisoformat(t).timestamp()-begin)/span*60)) for t in dated)
        bins=[counts[i] for i in range(60)]
    return {'cards':cards,'history':history[-100:],'history_total':len(history),'bins':bins,'review_progress':data.get('review_progress'),
        'start':dated[0] if dated else None,'end':dated[-1] if dated else None,
        'dated_groups':len(dated),'undated_groups':len(times)-len(dated),'coverage_map':next((o['fields'].get('coverage_map') for o in obs.values() if o['type']=='linux_environment' and o['fields'].get('coverage_map')),None),'scope':'화면에 색인된 관측 묶음. 전체 원문 타임라인은 보고서에 포함.'}
