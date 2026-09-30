"""Durable questions and logical tests, separate from physical worker jobs.

Mutable current state points to immutable decision revisions. Dependency
fingerprints bind retained source content, object scope and contrary context;
an unrelated observation does not invalidate every case decision.
"""
import hashlib
import json
from .store import now


def _digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':')).encode()).hexdigest()


def _generation(task):
    return task.get('retry_generation',task.get('generation',0))


def _object(observation):
    fields=observation.get('fields',{})
    if not fields.get('path'):return None
    return tuple([observation.get('evidence_id')]+[fields.get(k) for k in
        ('partition_offset','os_instance','volume_id','snapshot_id','path')])


def semantic_question(item, text, kind):
    """Definition and evidence roles, never an assessment of the question itself."""
    return {'definition': text, 'kind': kind,
        **{k:item[k] for k in ('competing_explanations','conditions','basis',
            'observation_ids','supporting_evidence_ids','refuting_evidence_ids',
            'counterevidence_ids','required_observation_ids') if k in item}}


def business_question(store, cid, evidence, item, kind, text):
    """Stable business identity; task/generation envelopes remain separate.

    Do not merge unrelated questions by textual similarity. The case-wide root
    is shared across evidence; explicit source IDs identify other definitions.
    Existing context/decision records are not rewritten or retroactively merged.
    """
    source=item.get('business_source_id') or item.get('id') or item.get('hypothesis_id') or item.get('claim_id')
    identity=_digest([cid,kind,cid if kind=='case_question' else source or [evidence['id'],text]])
    old=next((q for q in store.list('business_question',cid) if q['identity']==identity),None)
    explicit=item.get('question') or (item.get('scenario_assessment') or {}).get('comparison_question')
    # A new assessment's title is not a redefinition of the business question.
    definition=explicit or (old or {}).get('question') or text
    if kind in ('case_question','claim','objection'):definition=explicit or text
    revision=_digest({'definition':definition,'source_kind':kind})
    root=next((q for q in store.list('business_question',cid) if q['source_kind']=='case_question'),None)
    fields={'identity':identity,'question':definition,'source_kind':kind,
        'definition_revision':revision,'source_ids':[source] if source else [],
        'parent_question_id':None if kind=='case_question' else item.get('parent_question_id') or (root or {}).get('id')}
    if not old:return store.add('business_question',cid,version=1,**fields)
    if old.get('definition_revision')!=revision:
        return store.update(old['id'],version=old['version']+1,**fields)
    if old.get('parent_question_id')!=fields['parent_question_id']:
        return store.update(old['id'],parent_question_id=fields['parent_question_id'])
    return old


def sync(store,cid,task,evidence,observations=(),hypotheses=(),objections=(),claims=()):
    tid=task['id'];eid=evidence['id'];generation=_generation(task)
    by_id={o['id']:o for o in observations}
    # A cached digest may not cover newly supplied time/provenance data.
    hashes={i:_digest(o) for i,o in by_id.items()}
    corpus_revision=_digest([eid,evidence.get('signature'),evidence.get('connected',True),sorted(hashes.items())])
    prior={q['question_key']:q for q in project(store,cid,task) if q['evidence_id']==eid}
    rows=[];live_keys=set()
    for item in list(hypotheses)+list(claims)+list(objections):
        text=item.get('text') or item.get('question') or item.get('title') or item.get('statement')
        if not text:continue
        kind=item.get('source_kind') or item.get('kind') or ('objection' if 'statement' in item else 'hypothesis')
        source=item.get('id') or item.get('hypothesis_id') or item.get('claim_id')
        business=business_question(store,cid,evidence,item,kind,text)
        text=business['question']
        refs=sorted(set(item.get('observation_ids',[])+item.get('supporting_evidence_ids',[])+item.get('refuting_evidence_ids',[])))
        key=_digest([tid,eid,generation,kind,source or [text,refs]])
        live_keys.add(key)
        old=prior.get(key)
        identities={_object(by_id[i]) for i in refs if i in by_id}-{None}
        identities.update(tuple(i) for i in (old or {}).get('object_identities',[]))
        related=sorted(i for i,o in by_id.items() if i in refs or _object(o) in identities)
        dependency=_digest({'sources':[(i,hashes.get(i,'unavailable')) for i in sorted(set(refs+related))],
            'question':semantic_question(item,text,kind),'evidence_connected':evidence.get('connected',True),'signature':evidence.get('signature'),
            'corpus':corpus_revision if kind=='case_question' else None,
            'objections':[{'id':o.get('id'),'statement':o.get('statement'),
                'observation_ids':o.get('observation_ids',[]),'status':o.get('status')}
                for o in objections if set(o.get('observation_ids',[]))&set(refs+related)]})
        changed=bool(old and old['dependency_revision']!=dependency)
        if old and not changed:
            rows.append(old);continue
        values={'question_key':key,'question':str(text),'source_kind':kind,'source_ids':[source] if source else [],
            'business_question_id':business['id'],'definition_revision':business['definition_revision'],
            'parent_question_id':business.get('parent_question_id'),
            'observation_ids':refs,'related_observation_ids':related,'object_identities':[list(i) for i in sorted(identities,key=str)],
            'status':'reopened' if old else 'open','state':'reopened' if old else 'open',
            'task_id':tid,'evidence_id':eid,'generation':generation,'corpus_revision':corpus_revision,
            'dependency_revision':dependency,'version':old['version']+1 if old else 1,'updated_at':now()}
        if old:
            revision=store.add('decision_revision',cid,question_id=old['id'],question_version=old['version'],
                status='reopened',reason='질문·관련 원문·반론·시각 또는 증거 연결 범위 변경',
                previous_dependency_revision=old['dependency_revision'],dependency_revision=dependency,
                previous_decision_id=old.get('decision_id'),observation_ids=refs)
            values['decision_id']=revision['id']
            record=store.update(old['id'],**values)
        else:record=store.add('case_question',cid,**values)
        rows.append(record)
    for key,old in prior.items():
        if key not in live_keys and old.get('status')!='superseded':
            store.update(old['id'],status='superseded',state='superseded')
    return {'corpus_revision':corpus_revision,'questions':rows}


def project(store,cid,task=None):
    rows=store.list('case_question',cid)
    if task:rows=[q for q in rows if q['task_id']==task['id'] and q['generation']==_generation(task)]
    return [q for q in rows if q.get('status')!='superseded']


def assess(store,cid,question_id,status,reason,observation_ids=(),receipt_id=None,dependency_revision=None):
    question=store.get(question_id,'case_question')
    if question['case_id']!=cid or question.get('status')=='superseded':raise ValueError('wrong question scope')
    if dependency_revision is not None and dependency_revision!=question['dependency_revision']:
        raise ValueError('stale question dependency revision')
    if status not in ('open','held','scoped_answered'):raise ValueError('unknown question disposition')
    if status=='scoped_answered' and not observation_ids:raise ValueError('scoped answer requires positive source references')
    decision=store.add('decision_revision',cid,question_id=question_id,question_version=question['version'],
        status=status,reason=reason,observation_ids=list(observation_ids),receipt_id=receipt_id,
        dependency_revision=question['dependency_revision'],previous_decision_id=question.get('decision_id'))
    store.update(question_id,status=status,state=status,decision_id=decision['id'],decision_revision=decision['id'])
    return decision


def reserve(store,cid,question,tool,scope,conditions=None,question_version=None):
    key=question.get('question_key') if isinstance(question,dict) else question
    intent={'question_key':key,'question_id':question.get('id') if isinstance(question,dict) else None,
        'business_question_id':question.get('business_question_id') if isinstance(question,dict) else None,
        'tool':tool,'scope':scope,'conditions':conditions or {},
        'question_version':question_version if question_version is not None else question.get('version') if isinstance(question,dict) else None}
    digest=_digest(intent)
    existing=next((r for r in store.list('test_intent',cid) if r.get('intent_key')==digest),None)
    return existing or store.add('test_intent',cid,**intent,intent_key=digest,intent_digest=digest,status='reserved')


def complete(store,cid,intent_id,result,complete=False):
    row=store.get(intent_id,'test_intent')
    if row['case_id']!=cid:raise ValueError('wrong test scope')
    if row.get('result_scope')==result and row.get('result_complete')==bool(complete):return row
    return store.update(intent_id,status='complete' if complete else 'partial',result_scope=result,
        completed_at=now(),result_complete=bool(complete),assessment_status='unassessed')
