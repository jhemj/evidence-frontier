"""Hard-bound final model input while keeping each available source identifiable."""
import json


def serialize(value):
    """Use the same lossless representation for budget checks and model input."""
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def bounded(value, text=800, items=6):
    if isinstance(value,str):return value[:text]
    if isinstance(value,list):return [bounded(x,text,items) for x in value[:items]]
    if isinstance(value,dict):return {k:bounded(v,text,items) for k,v in value.items()}
    return value


def fit(pack, maximum=36000):
    size=lambda:len(serialize(pack))
    if size()<=maximum:return
    pack['context_budget_compacted']=True
    if pack.get('previous_assessment'):
        old=pack['previous_assessment']
        pack['previous_assessment']={'summary':old.get('summary','')[:500],
            'findings':[{k:bounded(v,400,8) for k,v in f.items() if k in ('dossier_id','title','judgment','reason','observation_ids','stages')}
                for f in old.get('findings',[])]}
    deferred=pack.get('deferred_checks',[])
    pack['deferred_checks_total']=len(deferred)
    pack['deferred_checks']=bounded(deferred[:8],400,8)
    for check in pack.get('executed_checks',[]):
        check['scope']=bounded(check.get('scope',{}),600,8)
    # IDs, source location, source_origin, context requests and citation allowlists
    # remain intact. Shorten content only, never replace a source with another.
    for length in (2000,1000,500,250):
        if size()<=maximum:return
        for observation in pack['observations']:
            fields=observation['fields']
            shortened=bounded(fields,length,5)
            if shortened!=fields:
                shortened['context_compacted']=True
                if shortened.get('excerpt')!=fields.get('excerpt'):shortened['excerpt_truncated']=True
            observation['fields']=shortened
    if size()>maximum:raise ValueError('최종 근거 입력 예산을 초과했습니다. 원문/인용 범위를 축소해 재검토해야 합니다.')
