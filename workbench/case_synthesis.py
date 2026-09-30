"""Checkpointed final hypothesis review after source-unit adjudication.

Small, independently validated calls exploit the model without presenting
unbounded case memory as if it were evidence. Failure is a visible unknown.
"""
import httpx
from copy import deepcopy
from contextlib import nullcontext
from .investigation import compact_observation, ranked
from .provider import Provider
from .investigator import consult
from .review_context import fit_metadata_only as fit, serialize, bounded
from .review_validation import errors
from .review_diagnostics import classify
from .review_contracts import model_attempts, model_exhausted


def current(controller,cid,task):
    return [r for r in controller.store.list('case_synthesis',cid)
            if r['task_id']==task['id'] and r['generation']==task.get('retry_generation',0)]


def source_revision(observations):
    from .review_stream import fingerprint
    return fingerprint(sorted((o['id'],o) for o in observations))


def review_revision(dossiers, source_ids):
    """Fingerprint current dossier adoption relevant to a synthesis scope.

    Source bytes alone are insufficient: a later reviewed/refuted dossier can
    change the meaning of an unchanged observation.  Keep this interface
    small so the controller/checkpoint coordinator can use the same freshness
    predicate without importing synthesis internals.
    """
    from .judgment_snapshot import review_revision as revision
    return revision(dossiers,source_ids)


def current_view(rows, *, dossiers=None):
    from .judgment_snapshot import current_synthesis
    return current_synthesis(rows,dossiers=dossiers)


def tick(controller,cid,evidence,task,dossiers,*,dynamic_only=False,incremental=False):
    """One bounded model call per tick; True only after every hypothesis is saved."""
    store=controller.store
    # Callers can hold an older queue snapshot after a follow-up was ingested.
    # Bind the checkpoint to current ledger rows before dispatch, then recheck
    # the same fingerprint on adoption.
    dossiers=[d for d in store.list('dossier',cid) if d.get('task_id')==task['id']
        and d.get('generation',0)==task.get('retry_generation',0)]
    from . import model_availability
    if model_availability.waiting(store,cid):return False
    from .hypothesis_ledger import current_scope
    tasks={t['id']:t for t in store.list('task',cid)}
    active={e['id'] for e in store.list('evidence',cid) if e.get('connected',True)}
    hypotheses=[h for h in store.list('hypothesis',cid) if h.get('evidence_id')==evidence['id'] and
        (h.get('contract') in ('linux-v1','windows-v1') or h.get('hypothesis_kind')=='dynamic' and current_scope(h,tasks,active))]
    if dynamic_only:
        reviewed_refs={i for d in dossiers if d.get('status')=='reviewed'
            for i in d.get('observation_ids',[])+(d.get('finding') or {}).get('observation_ids',[])}
        hypotheses=[h for h in hypotheses if h.get('hypothesis_kind')=='dynamic'
            and reviewed_refs.intersection(h.get('supporting_evidence_ids',[])+h.get('refuting_evidence_ids',[]))]
    hypotheses.sort(key=lambda h:(h.get('hypothesis_kind')!='dynamic',h.get('number',0)))
    all_observations=[o for o in controller.active_observations(cid) if o['evidence_id']==evidence['id']]
    by_id={o['id']:o for o in all_observations}
    done=set()
    from .case_memory import _object
    for previous in current_view(current(controller,cid,task)):
        # Earlier snapshots remain immutable. New or corrected sources in the
        # question's retained object scope reopen only that checkpoint.
        old_scope=previous.get('source_ids')
        if old_scope is None:
            done.add(previous['hypothesis_id']);continue
        rows=[by_id[i] for i in old_scope if i in by_id]
        origins={_object(o) for o in rows}-{None}
        related=[o for o in all_observations if o['id'] in old_scope or _object(o) in origins]
        current_h=next((h for h in hypotheses if h['id']==previous['hypothesis_id']),{})
        if set(current_h.get('supporting_evidence_ids',[])+current_h.get('refuting_evidence_ids',[]))-set(old_scope):continue
        if previous.get('followup_dossier_id'):continue
        review_matches=(previous.get('review_revision') is None
            or review_revision(dossiers,old_scope)==previous.get('review_revision'))
        if source_revision(related)==previous.get('source_revision') and review_matches:
            done.add(previous['hypothesis_id'])
    h=next((h for h in hypotheses if h['id'] not in done),None)
    if h is None:return True
    generation=task.get('retry_generation',0)
    attempts=[r for r in store.list('synthesis_input',cid) if r['task_id']==task['id'] and r['generation']==generation and r['hypothesis_id']==h['id']]
    obs=by_id
    expected_types=h.get('expected_source_types', [])
    relevant={oid for d in dossiers if any(obs.get(i,{}).get('type') in expected_types for i in d['observation_ids']) for oid in d['observation_ids']}
    relevant.update(o['id'] for o in obs.values() if o['type'] in expected_types)
    relevant.update(i for i in h.get('supporting_evidence_ids',[])+h.get('refuting_evidence_ids',[]) if i in obs)
    followups=[store.get(d['id']) for d in dossiers if d.get('origin_hypothesis_id')==h['id']]
    # A terminal input failure is an explicit analysis gap, not an active
    # dependency that can ever finish. Preserve its sources and unreviewed
    # count in the synthesis instead of waiting indefinitely for it.
    if any(d['status'] not in ('reviewed','model_failed','deferred','input_projection_blocked') for d in followups):return False
    relevant.update(i for d in followups for i in d.get('observation_ids',[]) if i in obs)
    followup_jobs=[j for j in store.list('investigation_job',cid) if j.get('task_id')==task['id']
        and j.get('generation',0)==generation and set(j.get('dossier_ids',[]))&{d['id'] for d in followups}]
    relevant.update(i for j in followup_jobs for i in j.get('observation_ids',[]) if i in obs)
    from . import objection_ledger
    related_dossiers=[d['id'] for d in dossiers if set(d['observation_ids'])&relevant]
    obligations=objection_ledger.current(store,cid,task,related_dossiers+[h['id']])
    mandatory={i for o in obligations for i in o['observation_ids'] if i in obs}
    relevant.update(mandatory)
    from .evidence_selection import order, audit
    configs=store.list('config');strategy=(configs[-1]['provider'] if configs else {}).get('investigation_strategy','guided')
    candidates=[o for o in obs.values() if o['id'] in relevant]
    if strategy=='guided':ordered,reasons=order(candidates,[h])
    else:ordered,reasons=ranked(candidates),{}
    # The source ledger owns the full scoped set. Pages, not a top-16 sample,
    # determine how much of this set can be assessed in one model call.
    selected=[compact_observation(obs[i],preserve_content=True) for i in sorted(mandatory)]
    selected += [compact_observation(o,preserve_content=True) for o in ordered if o['id'] not in mandatory]
    ids=[o['id'] for o in selected]
    findings=[d['finding'] for d in dossiers if d.get('finding') and d['status']=='reviewed' and set(d['finding']['observation_ids'])&set(ids)]
    pack={'target_os':store.get(cid).get('target_os','linux'),'case_question':store.get(cid).get('question',''),
        'incremental_checkpoint':bool(incremental),
        'hypothesis_kind':h.get('hypothesis_kind','coverage_domain'),
        'previous_scenario_assessment_untrusted':h.get('scenario_assessment'),
        'observations':selected,'required_dossiers':[{'id':h['id'],'title':h['text'],'observation_ids':ids}],
        'allowed_observation_ids':ids,'allowed_observation_ids_by_dossier':{h['id']:ids},
        'open_objections':[{**o,'origin_dossier_id':o['dossier_id'],'dossier_id':h['id']}
                           for o in objection_ledger.view(obligations)],
        'prior_findings_untrusted':[{k:bounded(v,400,8) for k,v in f.items() if k in
            ('dossier_id','title','judgment','reason','observation_ids','stages','alternatives','remaining_checks')}
            for f in findings[:8]],'prior_findings_total':len(findings),
        'scope':{'relevant_observations':len(relevant),'presented_observations':len(ids),
                 'unreviewed_units':sum(d['status']!='reviewed' for d in dossiers),
                 'terminal_followup_review_gaps':[{'dossier_id':d['id'],'status':d['status']}
                     for d in followups if d['status'] in ('model_failed','input_projection_blocked')]},
        'selection_audit':audit(candidates,selected,reasons,[h],strategy=strategy),
        'strongest_alternatives':h.get('competing_explanations',[]),
        'unavailable_materials':h.get('unavailable_materials',[]),
        'executed_checks':[],'deferred_checks':[],
        'final_pass':False,'selection_is_partial':len(relevant)>len(ids)}
    from . import question_engine
    from .test_admission import catalog
    pack['tool_capabilities']=catalog(pack['target_os'])
    pack['question_context']=question_engine.view(question_engine.refresh(controller,cid,evidence,task))
    from .test_context_projection import attach as attach_test_context, fit_presented, source_run_id
    test_source_run=source_run_id(obs.values())
    attach_test_context(pack,store,cid,task,evidence,test_source_run)
    from .semantic_contract import source_facts
    pack['literal_fact_candidates']=source_facts([obs[i] for i in ids])
    overall_scope=deepcopy(pack['scope'])
    if attempts and attempts[-1].get('id'):
        receipts=[r for r in store.list('receipt',cid) if r.get('input_record_id')==attempts[-1]['id']]
        if receipts and receipts[-1].get('validation_errors'):
            from .review_diagnostics import repair_feedback
            last=receipts[-1]
            pack['validation_feedback']=repair_feedback(ValueError(last.get('error','')),
                last['validation_errors'],last['id'])
    current_feedback=deepcopy(pack.get('validation_feedback'))
    finding=None;status='reviewed';metadata={};issues=[];output={};stream_meta=None;request_attempt=None
    from . import review_stream
    source_ids=list(ids)
    origins={_object(obs[i]) for i in source_ids}-{None}
    source_rows=[o for o in all_observations if o['id'] in source_ids or _object(o) in origins]
    dependency=source_revision(source_rows)
    def sources_current():
        current_task=store.get(task['id']);current_evidence=store.get(evidence['id'])
        latest=[o for o in controller.active_observations(cid) if o.get('evidence_id')==evidence['id']
                and (o['id'] in source_ids or _object(o) in origins)]
        return (current_task.get('retry_generation',0)==generation and not current_task.get('superseded')
            and current_task.get('status') in (None,'running','queued')
            and current_evidence.get('connected',True) and current_evidence['signature']==evidence['signature']
            and source_revision(latest)==dependency
            and review_revision([d for d in store.list('dossier',cid) if d.get('task_id')==task['id']
                and d.get('generation',0)==generation],source_ids)==dossier_revision)
    dossier_revision=review_revision(dossiers,source_ids)
    checkpoint_revision=review_stream.fingerprint([dependency,dossier_revision,[(j['id'],j.get('status'),j.get('result_scope')) for j in followup_jobs]])
    checkpoints=[c for c in store.list('synthesis_checkpoint',cid) if c.get('task_id')==task['id']
        and c.get('generation')==generation and c['hypothesis_id']==h['id'] and c.get('checkpoint_revision')==checkpoint_revision]
    checkpoint=checkpoints[-1] if checkpoints else store.add('synthesis_checkpoint',cid,
        task_id=task['id'],generation=generation,hypothesis_id=h['id'],round=0,attempts=0,
        source_revision=dependency,checkpoint_revision=checkpoint_revision,source_ids=source_ids,status='pending')
    pack['final_pass']=model_exhausted(model_attempts(store,cid,task)+3,task.get('repair_generation')==generation)
    input_error=None
    from .request_compiler import request_spec,compile_spec,REVIEW_QUESTIONS
    provider_config=store.list('config')[-1]['provider']
    review_question=REVIEW_QUESTIONS['synthesis']
    spec=request_spec(provider_config,review_question,'synthesis')
    input_maximum=checkpoint.get('review_input_maximum',36000)
    try:
        if checkpoint.get('review_stream_id'):
            pack,stream_meta=review_stream.prepare(store,cid,checkpoint['review_stream_id'])
        else:
            canonical=deepcopy(pack)
            try:fit_presented(pack,fit,store,cid,task,evidence,test_source_run,input_maximum,request_spec=spec)
            except ValueError:
                stream=review_stream.start(store,cid,task,checkpoint,canonical,maximum=input_maximum,request_spec=spec)
                pack,stream_meta=review_stream.prepare(store,cid,stream['id'])
        if current_feedback:
            pack['validation_feedback']=current_feedback
            fit_presented(pack,fit,store,cid,task,evidence,test_source_run,request_spec=spec)
        if stream_meta:
            fit_presented(pack,fit,store,cid,task,evidence,test_source_run,input_maximum,request_spec=spec)
        validation_pack=review_stream.resolved(pack)
        ids=[o['id'] for o in validation_pack['observations']]
        compiled_request=compile_spec(spec,pack).assert_fits()
    except ValueError as ex:input_error=str(ex)
    exhausted=model_exhausted(model_attempts(store,cid,task),task.get('repair_generation')==generation)
    if not ids or checkpoint['attempts']>=2 or exhausted or input_error:
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
                from .evidence_spans import manifest
                reservation=store.add('synthesis_input',cid,task_id=task['id'],generation=generation,hypothesis_id=h['id'],pack=pack,
                    evidence_presentation=manifest(pack),activity_target=h['text'][:300],compiled_request=compiled_request.identity)
                store.update(checkpoint['id'],attempts=checkpoint['attempts']+1)
            store.update(cid,investigation_stage=f"사건 전체 가설 종합 {len(done)+1}/{len(hypotheses)}")
            from .request_lifecycle import RequestAttempt
            request_attempt=RequestAttempt(store,cid,task,role='synthesis',input_record=reservation,
                owner_records=[h],evidence_id=evidence['id'],reservation_id=reservation['id'],
                logical_work_id=checkpoint['id']+':'+review_stream.fingerprint(stream_meta))
            output=None
            try:
                output,metadata=request_attempt.consult(consult,provider_config,
                    review_question,pack,role='synthesis',provider_factory=Provider,compiled_request=compiled_request)
                model_availability.recovered(store,cid,metadata.get('transport_identity'))
                request_attempt.emit('validating',validation_stage='synthesis_source_and_scope')
                presented={o['id']:o for o in validation_pack['observations']}
                issues=errors(output,[h['id']],ids,{h['id']:ids},presented,
                    require_literals=task.get('review_policy')=='autonomous-v1',canonical_observations=obs)
                supporting=set(output.get('supporting_evidence_ids',[]));refuting=set(output.get('refuting_evidence_ids',[]))
                if (supporting|refuting)-set(ids):issues.append({'code':'synthesis_citation_scope'})
                if supporting&refuting:issues.append({'code':'synthesis_conflicting_evidence_roles'})
                from .incident_status import assessment_errors
                issues += assessment_errors(output,presented)
                from .scenarios import accepted as accept_scenario
                try:accept_scenario(output.get('scenario_assessment'),supporting,refuting)
                except ValueError:issues.append({'code':'scenario_assessment_invalid'})
                if any(f['judgment']!='미확인' for f in output['findings']) and not supporting:
                    issues.append({'code':'synthesis_missing_support'})
                if pack.get('final_pass') and output.get('next_checks'):issues.append({'code':'synthesis_assessment_budget_reserved'})
                intermediate=bool(stream_meta and stream_meta['phase']!='synthesis')
                issues += objection_ledger.errors(output,pack.get('open_objections',[]),{h['id']:ids},intermediate=intermediate)
                foreign={o['id'] for o in pack.get('open_objections',[]) if o.get('origin_dossier_id',h['id'])!=h['id']}
                if any(a['objection_id'] in foreign and a['outcome']=='resolved' for a in output.get('objection_assessments',[])):
                    issues.append({'code':'synthesis_cannot_resolve_foreign_objection'})
                from .review_focus import project
                try:
                    selected_pack=project(pack,output)
                    if intermediate:
                        issues+=review_stream.focus_budget_errors(store,stream_meta,pack,output,selected_pack)
                except ValueError as ex:issues.append({'code':'excerpt_selection_invalid','detail':str(ex)})
                if issues:raise ValueError('final synthesis validation rejected')
                if task.get('review_policy')=='autonomous-v1' and not intermediate:
                    from .presentation_claims import bind
                    output['findings']=[bind(f,presented) for f in output['findings']]
                finding=output['findings'][0]
            except (ValueError,httpx.TransportError) as ex:
                request_attempt.fail(ex,outcome='service_unavailable' if model_availability.unavailable(ex) else 'failed')
                if model_availability.unavailable(ex):
                    with store.tx():
                        model_availability.defer(store,cid,task,ex,input_record_id=reservation['id'])
                        store.update(checkpoint['id'],attempts=checkpoint['attempts'])
                    return False
                store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='synthesis_error',
                    input_record_id=reservation['id'],failure_category=classify(ex,issues),
                    validation_errors=issues or [{'code':classify(ex,issues),'detail':str(ex)[:2000]}],error=str(ex),
                    rejected_output=output,raw_output=getattr(ex,'raw_output',None),
                    model_metadata={**getattr(ex,'metadata',{}), **{k:v for k,v in metadata.items() if k!='output'}})
                if classify(ex,issues)=='input_context_pressure':
                    from .review_diagnostics import smaller_input_budget
                    if stream_meta:store.update(stream_meta['stream_id'],status='superseded',reason='measured_model_context_pressure')
                    store.update(checkpoint['id'],review_input_maximum=smaller_input_budget(input_maximum,getattr(ex,'metadata',{})),
                        review_stream_id=None,attempts=0)
                return False
            guard(controller,cid,task)
            if store.get(task['id']).get('retry_generation',0)!=generation:
                request_attempt.emit('rejected',failure_category='stale_task_generation')
                request_attempt.finish('stale_scope')
                return False
            metadata['input_record_id']=reservation['id']
            if stream_meta and stream_meta['phase']!='synthesis':
                with store.tx():
                    if not sources_current():
                        store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='synthesis_error',
                            failure_category='stale_scope',error='분할 검토 중 원문 범위 변경. 채택 거부.',rejected_output=output,**metadata)
                        request_attempt.emit('rejected',failure_category='stale_synthesis_scope')
                        request_attempt.finish('stale_scope')
                        return False
                    receipt=store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],
                        receipt_type='case_synthesis_page',generation=generation,**metadata)
                    discovered=objection_ledger.capture(store,cid,task,output,receipt['id'],manifest(selected_pack))
                    review_stream.accept_page(store,checkpoint,stream_meta,output,receipt['id'],ids,discovered,presented_pack=pack)
                    request_attempt.accept([receipt],phase='page_accepted',scope='intermediate_page_not_final_judgment')
                return False
        except BaseException as ex:
            if request_attempt is not None and not request_attempt.ended:
                request_attempt.fail(ex,outcome='controller_exception')
            raise
        finally:controller.model_lock.release()
    with (request_attempt.finalizing() if request_attempt is not None else nullcontext()), store.tx():
        from .runtime_contract import guard
        guard(controller,cid,task)
        if (not sources_current()
            or any(r['hypothesis_id']==h['id'] and r.get('checkpoint_revision')==checkpoint_revision for r in current(controller,cid,task))):
            store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='synthesis_error',
                failure_category='stale_scope',error='종합 중 증거·작업 범위 변경. 결과 채택 거부.',rejected_output=finding,**metadata)
            if status=='reviewed':
                request_attempt.emit('rejected',failure_category='stale_synthesis_scope')
                request_attempt.finish('stale_scope')
            return False
        if status=='reviewed':
            assessment=store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],
                receipt_type='synthesis_assessment',generation=generation,**metadata)
            from .evidence_spans import manifest
            objection_ledger.capture(store,cid,task,output,assessment['id'],manifest(pack))
            objection_ledger.resolve(store,output,assessment['id'])
        obligations=objection_ledger.current(store,cid,task,related_dossiers+[h['id']])
        if obligations:
            finding={**finding,'open_objections':objection_ledger.view(obligations),
                'publication_status':'qualified_open_objections',
                'publication_limit':f'관련 단서의 미해결 반론 {len(obligations)}건. 사건 종합은 제한적 게시이며 해결된 결론이 아닙니다.'}
            if status=='reviewed':status='objections_open'
        from .claim_scope import qualify as qualify_scope
        finding=qualify_scope(finding,obs)
        followup_id=admit_checks(controller,cid,evidence,task,h,output.get('next_checks',[]),source_ids,followups,
            source_record_id=reservation['id']) if status in ('reviewed','objections_open') else None
        record=store.add('case_synthesis',cid,task_id=task['id'],evidence_id=evidence['id'],generation=generation,
            hypothesis_id=h['id'],number=h['number'],question=h['text'],status=status,finding=finding,
            supporting_evidence_ids=output.get('supporting_evidence_ids',[]),refuting_evidence_ids=output.get('refuting_evidence_ids',[]),
            incident_assessment=output.get('incident_assessment') if status=='reviewed' else None,
            scope=overall_scope,selection_is_partial=pack['selection_is_partial'],metadata=metadata,
            source_ids=source_ids,source_revision=dependency,review_revision=dossier_revision,
            checkpoint_revision=checkpoint_revision,incremental=bool(incremental),
            followup_dossier_id=followup_id)
        if status in ('reviewed','objections_open'):
            from .explanation_proposals import adopt as adopt_explanations
            adopt_explanations(store,cid,task,evidence,output,record['id'],ids,
                pack.get('question_context',{}).get('questions',[]))
        if h.get('hypothesis_kind') == 'dynamic':
            from .hypothesis_ledger import apply as apply_hypothesis
            finding_judgment = finding.get('judgment', '미확인')
            action = 'hold' if obligations else 'refute' if finding.get('timeline_role') == '반증됨' and output.get('refuting_evidence_ids') else 'hold' if finding_judgment == '미확인' else 'reinforce' if finding_judgment == '유력' else 'update'
            applied = apply_hypothesis(store, cid, evidence['id'], {
                'action': action, 'hypothesis_card_id': h.get('hypothesis_card_id',''),
                # A source fact answers part of a question; it must not replace
                # the investigation question itself (and silently change scope).
                'title': h.get('title') or h.get('text',''),
                'card_summary': finding.get('card_summary',''), 'judgment': finding_judgment, 'reasoning': finding.get('reason',''),
                'supporting_evidence_ids': output.get('supporting_evidence_ids', []), 'refuting_evidence_ids': output.get('refuting_evidence_ids', []),
                'remaining_checks': finding.get('remaining_checks', []), 'change_reason': finding.get('reason',''), 'basis': 'positive_evidence',
                'scenario_assessment':output.get('scenario_assessment') if status=='reviewed' else None},
                set(ids), task_id=task['id'], generation=generation, source_plan_id=record['id'], proposal_index=0, final=True)
            if applied is None:
                raise ValueError('dynamic hypothesis synthesis ledger rejected')
        store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='case_synthesis',record_id=record['id'],**metadata)
        from .question_engine import checkpoint as save_question_checkpoint
        save_question_checkpoint(store,cid,task,h['id'],finding,record['id'],pending=bool(followup_id))
        if stream_meta:review_stream.complete(store,stream_meta,record['id'])
        if status in ('reviewed','objections_open'):
            request_attempt.accept([record,assessment],scope='question_scoped_synthesis')
    return False


def admit_checks(controller,cid,evidence,task,h,calls,source_ids,previous,*,source_record_id=None):
    """Return to source review through the existing worker/check scheduler.

    No second executor and no unlimited synthesis/tool loop. A repeat logical
    test stays a documented gap; different ranges/conditions remain eligible.
    """
    from .review_contracts import contract,contracts,attach,review_exhausted,tools_exhausted
    from .retrieval import fingerprint_scope
    from .dossiers import digest
    from . import question_engine
    store=controller.store
    if not calls or review_exhausted(store,cid,task,4):return None
    jobs=[j for j in store.list('investigation_job',cid) if j.get('task_id')==task['id']
        and j.get('generation',0)==task.get('retry_generation',0)]
    run=next((o['fields'].get('run_id') for o in reversed(controller.active_observations(cid))
        if o.get('evidence_id')==evidence['id'] and o['type'] in ('linux_environment','windows_environment')),None)
    if not run:return None
    previous_ids={d['id'] for d in previous}
    fresh=[];original_intents={}
    from .test_admission import reusable,bind_reuse,execution_fingerprint
    questions=question_engine.case_memory.project(store,cid,task)
    question=next((q for q in questions if h['id'] in q['source_ids']),{'question_key':h['id'],'version':len(previous)})
    for call in calls:
        if store.get(cid).get('target_os')=='windows' and call['tool'] not in ('search','read_source','correlate'):continue
        if call.get('question_id') and call['question_id']!=question.get('id'):continue
        intent=question_engine.reserve(store,cid,task,question,call,evidence,run,source_record_id=source_record_id)
        if not intent['admission']['eligible']:continue
        fingerprint=digest([task['id'],task.get('retry_generation',0),run,evidence['signature'],fingerprint_scope(call)])
        fingerprint=execution_fingerprint(jobs,fingerprint)
        prior=reusable(jobs,fingerprint,call,source_run=run,evidence_id=evidence['id'])
        condition=contract(call)['contract_id']
        if prior and any(c['dossier_id'] in previous_ids and c['contract_id']==condition for c in contracts(prior)):continue
        if not prior and tools_exhausted(len(jobs)+len(fresh),len(jobs)+len(fresh),False):continue
        fresh.append((call,fingerprint,prior))
        original_intents[contract(call)['contract_id']]=intent
    if not fresh:return None
    dossier=store.add('dossier',cid,task_id=task['id'],evidence_id=evidence['id'],generation=task.get('retry_generation',0),
        title=h.get('title',h['text'])+' · 추가 판별',baseline=False,observation_ids=source_ids,
        total_records=len(source_ids),group_key='question:'+h['id']+':'+digest(fresh),
        review_priority=0,review_family='question',status='pending',finding=None,origin_hypothesis_id=h['id'])
    admitted=[]
    for call,fingerprint,prior in fresh:
        original_intent=original_intents[contract(call)['contract_id']]
        binding_source=source_record_id
        if call.get('test_design',{}).get('version')==2:
            from .test_contract_v2 import bind_followup_owner,bind_existing_result
            call,binding=bind_followup_owner(store,cid,task,evidence,run,call,original_intent,dossier,source_record_id)
            binding_source=binding['id']
        else:call={**call,'hypothesis_id':dossier['id']}
        intent=question_engine.reserve(store,cid,task,question,call,evidence,run,source_record_id=binding_source)
        if not intent['admission']['eligible']:continue
        if prior and prior['status']=='ingested' and call.get('test_design',{}).get('version')==2 and contract(call) not in contracts(prior):
            parents=contracts(prior)
            if not parents:continue
            parent=next((p for p in parents if p['dossier_id']==h['id']),parents[0])
            call,binding=bind_existing_result(store,cid,task,evidence,run,call,intent,prior,binding_source,parent_contract=parent)
            intent=question_engine.reserve(store,cid,task,question,call,evidence,run,source_record_id=binding['id'])
            if not intent['admission']['eligible']:continue
        if prior:
            bind_reuse(store,cid,prior,intent)
            job=store.update(prior['id'],contracts=attach(prior,call),
                dossier_ids=list(dict.fromkeys(prior.get('dossier_ids',[])+[dossier['id']])),
                test_intent_ids=list(dict.fromkeys(prior.get('test_intent_ids',[])+[intent['id']])))
            if job['status']=='ingested':question_engine.finish_intents(store,cid,job)
        else:
            job=store.add('investigation_job',cid,task_id=task['id'],evidence_id=evidence['id'],
                generation=task.get('retry_generation',0),fingerprint=fingerprint,source_run=run,request=call,
                contracts=[contract(call)],dossier_ids=[dossier['id']],purpose='question_followup',
                review_family='question',status='admitted',test_intent_ids=[intent['id']])
        admitted.append(job['id'])
    if not admitted and task.get('test_contract_policy')=='purpose-outcomes-v2':
        store.update(dossier['id'],status='deferred',deferred_reason='internal_reference_error',
            error='추가 판별 검사의 정확 계약 또는 기존 결과 계보를 승인하지 못했습니다.')
        return None
    store.add('dossier_batch',cid,task_id=task['id'],evidence_id=evidence['id'],generation=task.get('retry_generation',0),
        dossier_ids=[dossier['id']],status='await_checks',round=-1,attempts=0,output=None,job_ids=admitted,deferred_checks=[])
    return dossier['id']
