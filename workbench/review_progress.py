"""Historical review activity is not a fresh validation of an old judgment."""


def project(data):
    active={e['id'] for e in data['evidence'] if e.get('connected',True)}
    tasks={t['id']:t for t in data['task'] if not t.get('superseded')}
    observations={o['id']:o['evidence_id'] for o in data['observation'] if o['evidence_id'] in active}
    receipts={r['id']:r for r in data.get('receipt',[])}
    rows=[d for d in data.get('dossier',[]) if d['evidence_id'] in active and d['task_id'] in tasks]
    current=[d for d in rows if d.get('generation',0)==tasks[d['task_id']].get('retry_generation',0)]
    keys={(d['evidence_id'],d['task_id'],d.get('group_key',d['id'])) for d in current}
    prior={}
    for d in rows:
        key=(d['evidence_id'],d['task_id'],d.get('group_key',d['id']))
        f=d.get('finding')
        if key not in keys or d['status']!='reviewed' or not f:continue
        if d.get('generation',0)>tasks[d['task_id']].get('retry_generation',0):continue
        if any(observations.get(oid)!=d['evidence_id'] for oid in f['observation_ids']):continue
        old=prior.get(key)
        if old is None or d.get('generation',0)>old.get('generation',0):prior[key]=d
    historical=[]
    for d in current:
        key=(d['evidence_id'],d['task_id'],d.get('group_key',d['id']))
        previous=prior.get(key)
        if d['status']=='reviewed' or previous is None:continue
        historical.append({'current_dossier_id':d['id'],'dossier_id':previous['id'],
            'receipt_id':previous.get('receipt_id'),'generation':previous.get('generation',0),
            'reviewed_at':receipts.get(previous.get('receipt_id'),{}).get('created_at'),
            'finding':previous['finding'],'freshness':'historical_not_revalidated'})
    recovery=any(t.get('repair_generation')==t.get('retry_generation',0) for t in tasks.values())
    return {'total':len(keys),'ever_reviewed':len(prior),'never_reviewed':len(keys-prior.keys()),
        'current_reviewed':sum(d['status']=='reviewed' and (d['evidence_id'],d['task_id'],d.get('group_key',d['id'])) in prior for d in current),
        'historical_only':len(historical),'historical_assessments':historical,
        'scope':('이번에는 실패 단서만 제한 복구합니다. 다른 단서의 이전 판단은 이번 재검증 결과가 아닙니다. ' if recovery else '')+
            '현재 선택된 단서의 검토 이력입니다. 원문 전수검토율이 아니며, 이전 판단은 AI 해석·정상성 미검증 상태입니다.'}
