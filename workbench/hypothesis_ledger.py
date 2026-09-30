"""Source-bound evolving interpretations. Sources are never rewritten here."""
import hashlib
from copy import deepcopy
from contextlib import nullcontext
from .store import now
from .card_evolution import lifecycle


def current_scope(row, tasks, active):
    task=tasks.get(row.get('task_id'))
    return bool(task and not task.get('superseded') and row.get('evidence_id') in active
        and task.get('evidence_id')==row.get('evidence_id')
        and row.get('generation',0)==task.get('retry_generation',0))


def apply(store, case_id, evidence_id, assessment, valid_ids, *, task_id='', generation=0,
          source_plan_id='', proposal_index=0, final=False):
    """Caller provides the accepted input/plan and source allowlist, never the LLM.

    Recheck scope inside the transaction. A replay is a no-op, not a revision.
    """
    with store.lock, (nullcontext() if store.db.in_transaction else store.tx()):
        tasks={t['id']:t for t in store.list('task',case_id)}
        active={e['id'] for e in store.list('evidence',case_id) if e.get('connected',True)}
        scope={'task_id':task_id,'evidence_id':evidence_id,'generation':generation}
        if not current_scope(scope,tasks,active):return None
        sources={r['id']:r for kind in ('investigation_plan','synthesis_input','case_synthesis') for r in store.list(kind,case_id)}
        source=sources.get(source_plan_id)
        if not source or source.get('task_id')!=task_id or source.get('generation',0)!=generation:return None
        observations={o['id']:o for o in store.list('observation',case_id) if o.get('evidence_id')==evidence_id}
        support=list(dict.fromkeys(assessment.get('supporting_evidence_ids',[])))
        refute=list(dict.fromkeys(assessment.get('refuting_evidence_ids',[])))
        refs=support+refute
        if set(refs)-set(valid_ids) or set(refs)-set(observations) or set(support)&set(refute):return None
        action=assessment.get('action','update');basis=assessment.get('basis','positive_evidence')
        if action not in {'create','update','reinforce','refute','hold'}:return None
        if action=='refute' and (not refute or basis=='absence'):return None
        if action=='reinforce' and (not support or basis=='absence'):return None
        if action in {'create','update'} and not refs:return None
        from .scenarios import accepted, dependency, project, record_rank_changes
        try:scenario=accepted(assessment.get('scenario_assessment'),support,refute,basis)
        except ValueError:return None
        hid=assessment.get('hypothesis_card_id') or assessment.get('hypothesis_id','')
        rows=[h for h in store.list('hypothesis',case_id) if h.get('hypothesis_kind')=='dynamic']
        origin=f'{case_id}:{evidence_id}:{task_id}:{generation}:{source_plan_id}:{proposal_index}'
        for existing in rows:
            if current_scope(existing,tasks,active) and any(r.get('origin_key')==origin for r in existing.get('revision_history',[])):
                return existing
        from .explanation_links import validate_proposals, record as record_links
        manifest=source.get('accepted_claim_refs',(source.get('pack') or {}).get('accepted_claim_refs',[]))
        links,link_errors=validate_proposals(store,case_id,assessment.get('explanation_links',[]),manifest,
            task_id=task_id,evidence_id=evidence_id,generation=generation,valid_ids=valid_ids,
            hypothesis_source_ids=refs)
        row=next((h for h in rows if hid in (h['id'],h.get('hypothesis_card_id')) and h.get('evidence_id')==evidence_id),None) if hid else None
        if action=='create':
            if hid:return None
        elif not row or not current_scope(row,tasks,active):return None
        before=project({kind:store.list(kind,case_id) for kind in ('evidence','task','observation','hypothesis','dossier')})
        title=(assessment.get('title') or (row or {}).get('title','')).strip()
        if not title:return None
        summary=assessment.get('card_summary') or (row or {}).get('card_summary','')
        reason=assessment.get('reasoning','')
        judgment=assessment.get('judgment','미확인')
        judgment='확인' if judgment=='확정' else judgment
        if action in ('hold','refute') or not support:judgment='미확인'
        elif not final and judgment=='확인':judgment='유력'
        state=lifecycle({'judgment':judgment,'supporting_evidence_ids':support,'refuting_evidence_ids':refute},
            action=action,basis=basis,final=final)
        from .scenarios import FIT
        previous_fit=((row or {}).get('scenario_assessment') or {}).get('evidence_fit')
        if scenario and previous_fit in FIT and FIT[scenario['evidence_fit']]<FIT[previous_fit] and state in ('investigating','strengthened'):
            state='weakened'
        previous=(row or {}).get('revision_history',[])
        old_support=set((row or {}).get('supporting_evidence_ids',[]));old_refute=set((row or {}).get('refuting_evidence_ids',[]))
        old=old_support|old_refute;new=set(refs)
        rev={'revision':len(previous)+1,'at':now(),'origin_key':origin,'source_plan_id':source_plan_id,
            'proposal_index':proposal_index,**scope,'action':action,'basis':basis,'final':final,'lifecycle':state,
            'judgment':judgment,'title':title,'card_summary':summary,'reasoning':reason,
            'scenario_assessment':scenario,
            'change_reason':assessment.get('change_reason') or reason,'observation_ids':refs,
            'supporting_evidence_ids':support,'refuting_evidence_ids':refute,
            'previous_supporting_evidence_ids':sorted(old_support),'previous_refuting_evidence_ids':sorted(old_refute),
            'added_observation_ids':sorted(new-old),'removed_observation_ids':sorted(old-new),
            'change_type':'first_assessment' if not row else 'sources_added' if new-old else 'reinterpretation'}
        fields={**scope,'source_plan_id':source_plan_id,'title':title,'text':title,'card_summary':summary,
            'status':'reviewed' if final else 'reviewed_with_gaps','judgment':judgment,'lifecycle':state,
            'observation_ids':refs,'supporting_evidence_ids':support,'refuting_evidence_ids':refute,
            'remaining_checks':assessment.get('remaining_checks',[]),'reasoning':reason,
            'scenario_assessment':scenario,
            'scenario_source_revision':dependency(list(observations.values()),refs) if scenario else None,
            'scenario_review_revision':source.get('review_revision') if scenario else None,
            'scenario_review_source_ids':source.get('source_ids',[]) if scenario else [],
            'revision':rev['revision'],'revision_history':deepcopy(previous)+[rev]}
        if row:result=store.update(row['id'],**fields)
        else:
            number=max((h.get('number',0) for h in store.list('hypothesis',case_id) if h.get('evidence_id')==evidence_id),default=0)+1
            result=store.add('hypothesis',case_id,contract='dynamic-v1',hypothesis_kind='dynamic',number=number,
                hypothesis_card_id='HYP-CARD-'+hashlib.sha256(origin.encode()).hexdigest()[:24],origin_key=origin,
                origin_task_id=task_id,origin_generation=generation,**fields)
        record_links(store,case_id,result,links,source_plan_id=source_plan_id)
        if link_errors:
            store.add('receipt',case_id,task_id=task_id,evidence_id=evidence_id,generation=generation,
                receipt_type='explanation_link_rejected',source_plan_id=source_plan_id,
                hypothesis_id=result['id'],hypothesis_revision=result['revision'],
                failure_category='explanation_reference',validation_errors=link_errors,
                error='설명 관계의 원문·주장 버전·대상 범위를 확인할 수 없어 해당 연결만 반영하지 않았습니다.')
        record_rank_changes(store,case_id,before,assessment.get('change_reason') or reason)
        return store.get(result['id'])
