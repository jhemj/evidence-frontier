"""Checkpointed final hypothesis review after source-unit adjudication.

Small, independently validated calls exploit the model without presenting
unbounded case memory as if it were evidence. Failure is a visible unknown.
"""
import httpx
from .investigation import compact_observation, ranked
from .provider import Provider
from .investigator import consult
from .review_context import fit, serialize, bounded
from .review_validation import errors
from .review_diagnostics import classify
from .review_contracts import model_attempts, model_exhausted


def current(controller,cid,task):
    return [r for r in controller.store.list('case_synthesis',cid)
            if r['task_id']==task['id'] and r['generation']==task.get('retry_generation',0)]


def tick(controller,cid,evidence,task,dossiers):
    """One bounded model call per tick; True only after every hypothesis is saved."""
    store=controller.store
    from .hypothesis_ledger import current_scope
    tasks={t['id']:t for t in store.list('task',cid)}
    active={e['id'] for e in store.list('evidence',cid) if e.get('connected',True)}
    hypotheses=[h for h in store.list('hypothesis',cid) if h.get('evidence_id')==evidence['id'] and
        (h.get('contract') in ('linux-v1','windows-v1') or h.get('hypothesis_kind')=='dynamic' and current_scope(h,tasks,active))]
    hypotheses.sort(key=lambda h:(h.get('hypothesis_kind')!='dynamic',h.get('number',0)))
    done={r['hypothesis_id'] for r in current(controller,cid,task)}
    h=next((h for h in hypotheses if h['id'] not in done),None)
    if h is None:return True
    generation=task.get('retry_generation',0)
    attempts=[r for r in store.list('synthesis_input',cid) if r['task_id']==task['id'] and r['generation']==generation and r['hypothesis_id']==h['id']]
    obs={o['id']:o for o in controller.active_observations(cid) if o['evidence_id']==evidence['id']}
    expected_types=h.get('expected_source_types', [])
    relevant={oid for d in dossiers if any(obs.get(i,{}).get('type') in expected_types for i in d['observation_ids']) for oid in d['observation_ids']}
    relevant.update(o['id'] for o in obs.values() if o['type'] in expected_types)
    relevant.update(i for i in h.get('supporting_evidence_ids',[])+h.get('refuting_evidence_ids',[]) if i in obs)
    from .evidence_selection import order, audit
    configs=store.list('config');strategy=(configs[-1]['provider'] if configs else {}).get('investigation_strategy','guided')
    candidates=[o for o in obs.values() if o['id'] in relevant]
    if strategy=='guided':ordered,reasons=order(candidates,[h])
    else:ordered,reasons=ranked(candidates),{}
    selected=[]
    for o in ordered:
        compact=compact_observation(o)
        if len(serialize(selected+[compact]))>20000 or len(selected)>=16:continue
        selected.append(compact)
    ids=[o['id'] for o in selected]
    findings=[d['finding'] for d in dossiers if d.get('finding') and d['status']=='reviewed' and set(d['finding']['observation_ids'])&set(ids)]
    pack={'target_os':store.get(cid).get('target_os','linux'),'observations':selected,'required_dossiers':[{'id':h['id'],'title':h['text'],'observation_ids':ids}],
        'allowed_observation_ids':ids,'allowed_observation_ids_by_dossier':{h['id']:ids},
        'prior_findings_untrusted':[{k:bounded(v,400,8) for k,v in f.items() if k in
            ('dossier_id','title','judgment','reason','observation_ids','stages','alternatives','remaining_checks')}
            for f in findings[:8]],'prior_findings_total':len(findings),
        'scope':{'relevant_observations':len(relevant),'presented_observations':len(ids),
                 'unreviewed_units':sum(d['status']!='reviewed' for d in dossiers)},
        'selection_audit':audit(candidates,selected,reasons,[h],strategy=strategy),
        'strongest_alternatives':h.get('competing_explanations',[]),
        'unavailable_materials':h.get('unavailable_materials',[]),
        'final_pass':True,'selection_is_partial':len(relevant)>len(ids)}
    from .semantic_contract import source_facts
    pack['literal_fact_candidates']=source_facts([obs[i] for i in ids])
    if attempts and attempts[-1].get('id'):
        receipts=[r for r in store.list('receipt',cid) if r.get('input_record_id')==attempts[-1]['id']]
        if receipts and receipts[-1].get('validation_errors'):pack['validation_feedback']=receipts[-1]['validation_errors']
    finding=None;status='reviewed';metadata={};issues=[];output={}
    input_error=None
    try:fit(pack)
    except ValueError as ex:input_error=str(ex)
    exhausted=model_exhausted(model_attempts(store,cid,task),task.get('repair_generation')==generation)
    if not ids or len(attempts)>=2 or exhausted or input_error:
        status='source_unavailable' if not ids else 'input_budget' if input_error else 'budget_exhausted' if exhausted else 'model_failed'
        if input_error:metadata={'failure_category':'input_budget','error':input_error}
        finding={'dossier_id':h['id'],'title':h['text'],'judgment':'미확인','reason':'자료 부족 또는 최종 종합 검증 실패. 기존 단서별 판단은 그대로 보존합니다.',
                 'observation_ids':[],'stages':[],'alternatives':h.get('competing_explanations',[]),
                 'remaining_checks':h.get('unavailable_materials',[]),'timeline_role':'참고'}
    else:
        if not controller.model_lock.acquire(blocking=False):return False
        try:
            from .runtime_contract import guard
            guard(controller,cid,task)
            with store.tx():
                if model_exhausted(model_attempts(store,cid,task),task.get('repair_generation')==generation):return False
                reservation=store.add('synthesis_input',cid,task_id=task['id'],generation=generation,hypothesis_id=h['id'],pack=pack,activity_target=h['text'][:300])
            store.update(cid,investigation_stage=f"사건 전체 가설 종합 {len(done)+1}/{len(hypotheses)}")
            output=None
            try:
                output,metadata=consult(store.list('config')[-1]['provider'],
                    '개별 검토 뒤 최종 사건 가설을 종합하세요. 가설을 사실로 전제하지 말고 남은 공백을 명시하세요.',pack,role='synthesis',provider_factory=Provider)
                issues=errors(output,[h['id']],ids,{h['id']:ids},obs,require_literals=task.get('review_policy')=='autonomous-v1')
                supporting=set(output.get('supporting_evidence_ids',[]));refuting=set(output.get('refuting_evidence_ids',[]))
                if (supporting|refuting)-set(ids):issues.append({'code':'synthesis_citation_scope'})
                if supporting&refuting:issues.append({'code':'synthesis_conflicting_evidence_roles'})
                if any(f['judgment']!='미확인' for f in output['findings']) and not supporting:
                    issues.append({'code':'synthesis_missing_support'})
                if output.get('next_checks'):issues.append({'code':'synthesis_requires_remaining_checks_not_tools'})
                if issues:raise ValueError('final synthesis validation rejected')
                finding=output['findings'][0]
            except (ValueError,httpx.TransportError) as ex:
                store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='synthesis_error',
                    input_record_id=reservation['id'],failure_category=classify(ex,issues),
                    validation_errors=issues or [{'code':classify(ex,issues),'detail':str(ex)[:2000]}],error=str(ex),
                    rejected_output=output,raw_output=getattr(ex,'raw_output',None))
                return False
            guard(controller,cid,task)
            if store.get(task['id']).get('retry_generation',0)!=generation:return False
            metadata['input_record_id']=reservation['id']
        finally:controller.model_lock.release()
    with store.tx():
        from .runtime_contract import guard
        guard(controller,cid,task)
        current_task=store.get(task['id']);current_evidence=store.get(evidence['id'])
        if (current_task.get('retry_generation',0)!=generation or current_task.get('superseded')
            or current_task.get('status') not in (None,'running','queued')
            or not current_evidence.get('connected',True) or current_evidence['signature']!=evidence['signature']
            or h['id'] in {r['hypothesis_id'] for r in current(controller,cid,task)}):
            store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='synthesis_error',
                failure_category='stale_scope',error='종합 중 증거·작업 범위 변경. 결과 채택 거부.',rejected_output=finding,**metadata)
            return False
        record=store.add('case_synthesis',cid,task_id=task['id'],evidence_id=evidence['id'],generation=generation,
            hypothesis_id=h['id'],number=h['number'],question=h['text'],status=status,finding=finding,
            supporting_evidence_ids=output.get('supporting_evidence_ids',[]),refuting_evidence_ids=output.get('refuting_evidence_ids',[]),
            scope=pack['scope'],selection_is_partial=pack['selection_is_partial'],metadata=metadata)
        if h.get('hypothesis_kind') == 'dynamic':
            from .hypothesis_ledger import apply as apply_hypothesis
            finding_judgment = finding.get('judgment', '미확인')
            action = 'refute' if finding.get('timeline_role') == '반증됨' and output.get('refuting_evidence_ids') else 'hold' if finding_judgment == '미확인' else 'reinforce' if finding_judgment == '유력' else 'update'
            applied = apply_hypothesis(store, cid, evidence['id'], {
                'action': action, 'hypothesis_card_id': h.get('hypothesis_card_id',''), 'title': finding.get('title', h.get('title', h.get('text',''))),
                'card_summary': finding.get('card_summary',''), 'judgment': finding_judgment, 'reasoning': finding.get('reason',''),
                'supporting_evidence_ids': output.get('supporting_evidence_ids', []), 'refuting_evidence_ids': output.get('refuting_evidence_ids', []),
                'remaining_checks': finding.get('remaining_checks', []), 'change_reason': finding.get('reason',''), 'basis': 'positive_evidence'},
                set(ids), task_id=task['id'], generation=generation, source_plan_id=record['id'], proposal_index=0, final=True)
            if applied is None:
                raise ValueError('dynamic hypothesis synthesis ledger rejected')
        store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='case_synthesis',record_id=record['id'],**metadata)
    return False
