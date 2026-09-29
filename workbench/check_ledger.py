"""One executable scope can serve several distinct, auditable logical tests."""
import hashlib
import json
from .retrieval import fingerprint_scope
from .review_contracts import contracts, contract


def key(request, owner):
    scope=[owner.get('evidence_id'),owner.get('task_id'),owner.get('generation',0),fingerprint_scope(request)]
    return hashlib.sha256(json.dumps(scope,sort_keys=True).encode()).hexdigest()


def project(jobs, batches):
    rows={}; evaluated={}
    for batch in batches:
        for a in (batch.get('output') or {}).get('check_assessments',[])+batch.get('unassessed_checks',[]):
            identity=(a['check_id'],a.get('dossier_id',''),a.get('contract_id',''))
            previous=evaluated.get(identity)
            if previous is None or a.get('evaluation_status')!='unassessed':evaluated[identity]=a
    for job in jobs:
        row=rows.setdefault(key(job['request'],job),{'request':job['request'],'job_ids':[],'contracts':[],
            'execution_status':job['status'],'material_class':'retained_or_image','reasons':[]})
        row['job_ids'].append(job['id'])
        for c in contracts(job):
            assessment=evaluated.get((job['id'],c['dossier_id'],c['contract_id']))
            row['contracts'].append({**c,'job_id':job['id'],'assessment':assessment,
                'evaluation_status':'assessed' if assessment and assessment.get('evaluation_status')!='unassessed' else 'unassessed'})
    for batch in batches:
        for deferred in batch.get('deferred_checks',[]):
            call=deferred['request'];identity=key(call,batch)
            row=rows.setdefault(identity,{'request':call,'job_ids':[],'contracts':[],
                'execution_status':'not_executed','material_class':'image_extractable','reasons':[]})
            c=contract(call)
            if not any(x['dossier_id']==c['dossier_id'] and x['contract_id']==c['contract_id'] for x in row['contracts']):
                row['contracts'].append({**c,'assessment':None,'evaluation_status':'unassessed'})
            if deferred['reason'] not in row['reasons']:row['reasons'].append(deferred['reason'])
    checks=[dict(scope_key=k,**v) for k,v in sorted(rows.items())]
    return {'checks':checks,'unique_scopes':len(checks),
        'not_executed':sum(not c['job_ids'] for c in checks),
        'unassessed_contracts':sum(x['evaluation_status']=='unassessed' for c in checks for x in c['contracts']),
        'scope':'물리적 검사 중복 제거. 같은 결과라도 가설별 판별조건은 별도 평가.'}
