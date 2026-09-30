"""Adopt source-bound leads, never promote an imagined explanation to a fact."""
from .case_memory import _digest


def adopt(store,cid,task,evidence,output,source_record_id,allowed_ids,questions):
    supplied={q['id']:q for q in questions}
    root=next((q for q in questions if q.get('source_kind')=='case_question'),None)
    rows=[]
    for proposal in output.get('explanation_proposals',[]):
        q=supplied.get(proposal.get('question_id')) if proposal.get('question_id') else root
        refs=proposal.get('trigger_observation_ids',[])
        if not q or not refs or not set(refs)<=set(allowed_ids):
            store.add('receipt',cid,task_id=task['id'],receipt_type='explanation_proposal_rejected',
                source_record_id=source_record_id,failure_category='proposal_scope')
            continue
        key=_digest([cid,evidence['id'],q.get('business_question_id') or q['id'],
            ' '.join(proposal['explanation'].split()),' '.join(proposal['discriminating_question'].split()),sorted(set(refs))])
        generation=task.get('retry_generation',0)
        prior=next((r for r in store.list('hypothesis_proposal',cid) if r.get('proposal_key')==key
            and r.get('task_id')==task['id'] and r.get('generation',0)==generation),None)
        if prior:
            rows.append(prior);continue
        rows.append(store.add('hypothesis_proposal',cid,proposal_key=key,task_id=task['id'],
            evidence_id=evidence['id'],generation=generation,business_source_id=key,
            parent_question_id=q.get('business_question_id'),source_question_id=q['id'],
            source_record_id=source_record_id,source_kind='hypothesis_candidate',
            question=proposal['discriminating_question'],explanation=proposal['explanation'],
            next_discriminator=proposal['next_discriminator'],observation_ids=list(dict.fromkeys(refs)),
            triggering_evidence_ids=list(dict.fromkeys(refs)),supporting_evidence_ids=[],
            refuting_evidence_ids=[],status='candidate',judgment='미확인',
            scope_note='Investigation candidate. Triggering evidence is not support; no tool or verdict authority.'))
    return rows
