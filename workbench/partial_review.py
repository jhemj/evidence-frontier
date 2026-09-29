"""Adopt independent validated facts while isolating a failed sibling.

Only final, nonpaged source findings without tool or objection dependencies are
eligible. Global/schema/selection errors fail closed. This is not citation
repair and never converts an invalid finding into a valid one.
"""
from copy import deepcopy


def isolate(store,cid,task,batch,output,issues,presented,context,diagnostic_id):
    if not output or batch.get('round',0)<1 or len(batch['dossier_ids'])<2 or batch.get('job_ids'):
        return False
    if context.get('review_stream') or not issues or any(not x.get('dossier_id') for x in issues):return False
    wanted=set(batch['dossier_ids']);bad={x['dossier_id'] for x in issues}
    if not bad<wanted:return False
    if len(output.get('findings',[]))!=len(wanted) or {f['dossier_id'] for f in output['findings']}!=wanted:return False
    if output.get('check_assessments') or output.get('objection_assessments'):return False
    blocked={x['hypothesis_id'] for x in output.get('next_checks',[])}
    from .objection_ledger import current
    blocked.update(x['dossier_id'] for x in current(store,cid,task,batch['dossier_ids']))
    valid=[f for f in output['findings'] if f['dossier_id'] not in bad|blocked
        and not f.get('counterevidence_ids') and not f.get('open_objections')]
    if not valid:return False
    from .presentation_claims import bind
    from .card_evolution import assessment_revision
    from .dossiers import split_context
    from .judgment import bound_absence
    from .claim_scope import qualify
    findings=[qualify(bound_absence(bind(f,presented) if task.get('review_policy')=='autonomous-v1' else deepcopy(f)),presented) for f in valid]
    accepted={f['dossier_id'] for f in findings}
    refs=list(dict.fromkeys(i for f in findings for i in f.get('observation_ids',[])))
    receipt=store.add('receipt',cid,task_id=task['id'],evidence_id=task.get('evidence_id'),
        receipt_type='dossier_model_partial',input_record_id=context['input_record_id'],
        batch_id=batch['id'],generation=task.get('retry_generation',0),
        diagnostic_id=diagnostic_id,accepted_dossier_ids=sorted(accepted),valid_assessed_ids=refs,
        included_ids=context.get('included_ids',[]),model_request_attempted=False,
        scope='Independent validated subset of original model response; no new inference or tool execution.')
    for f in findings:
        dossier=store.get(f['dossier_id'])
        store.update(dossier['id'],status='reviewed',finding=f,receipt_id=receipt['id'],
            assessment_history=assessment_revision(dossier,f,receipt['id'],published=True),
            partial_adoption_from=diagnostic_id)
    store.update(batch['id'],status='split',termination='independent_claim_isolation',
        accepted_dossier_ids=sorted(accepted),output={'findings':findings,'check_assessments':[]},receipt_id=receipt['id'])
    for did in batch['dossier_ids']:
        if did in accepted:continue
        child=split_context(store,batch,did)
        store.add('dossier_batch',cid,task_id=task['id'],evidence_id=task.get('evidence_id'),
            generation=task.get('retry_generation',0),status='pending',attempts=batch['attempts']+1,
            parent_batch_id=batch['id'],validation_feedback={'errors':[x for x in issues if x.get('dossier_id')==did],
                'instruction':'Repair only this rejected finding. Independent accepted sibling facts remain in their original source-bound ledger.'},**child)
    return True
