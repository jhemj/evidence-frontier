"""Canonical judgment references shared by reports and passive display.

No provider, controller, mutable Store or scheduling side effects. Presentation
versions may differ; these hashes name the same retained judgment and sources.
"""
import hashlib
import json


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def review_revision(dossiers,source_ids):
    wanted=set(source_ids);rows=[]
    for d in dossiers:
        f=d.get('finding') or {}
        ids=set(d.get('observation_ids',[])+f.get('observation_ids',[])+f.get('counterevidence_ids',[]))
        ids.update(i for o in f.get('open_objections',[]) for i in o.get('observation_ids',[]))
        if ids&wanted:rows.append((d.get('id'),d.get('status'),d.get('revision',0),f))
    return digest(sorted(rows,key=lambda r:str(r[0])))


def current_synthesis(rows,*,dossiers=None):
    latest={}
    for row in rows:latest[(row.get('task_id'),row.get('generation'),row['hypothesis_id'])]=row
    result=[]
    for row in latest.values():
        if dossiers is not None and row.get('review_revision'):
            related=[d for d in dossiers if d.get('task_id')==row['task_id'] and d.get('generation',0)==row.get('generation',0)]
            if review_revision(related,row.get('source_ids',[]))!=row['review_revision']:
                row={**row,'status':'review_changed','incident_assessment':None,'finding':{**row['finding'],
                    'judgment':'미확인','title':'재검토 대기 · '+row['finding']['title'],
                    'reason':'관련 단서의 검토 결과가 바뀌어 이전 종합을 재검토해야 합니다. 이전 해석: '+row['finding']['reason']}}
        result.append(row)
    return result


def claim_version(row,finding,observations,stage=None):
    refs=set(finding.get('observation_ids',[])+finding.get('counterevidence_ids',[]))
    refs.update(i for s in finding.get('stages',[]) for i in s.get('observation_ids',[]))
    refs.update(a['observation_id'] for a in finding.get('fact_assertions',[]))
    return digest({'owner':row['id'],'status':row.get('status'),'finding':finding,
        'falsification':row.get('falsification'),'stage':stage,
        'sources':{i:(observations[i].get('_source_version') or digest(observations[i]))
            if i in observations else None for i in sorted(refs)}})


def manifest(document):
    observations={o['id']:o for o in document['observations']};versions={}
    rows=document.get('claims',[])+document.get('dossiers',[])+document.get('case_synthesis',[])
    for row in rows:
        if row.get('status') not in ('approved','reviewed'):continue
        f=row.get('finding') or row
        versions['claim:'+row['id']]=claim_version(row,f,observations)
        for i,_ in enumerate(f.get('stages',[])):
            versions['claim:'+row['id']+':stage:'+str(i)]=claim_version(row,f,observations,i)
    return versions
