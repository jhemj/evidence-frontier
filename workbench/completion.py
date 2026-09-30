"""Separate execution termination, collection coverage, and analyst judgments."""


def presentations(store,cid,tasks):
    current=lambda r:r.get('task_id') in tasks and r.get('generation',0)==tasks[r['task_id']].get('retry_generation',0)
    receipts=store.list('receipt',cid)
    by_input={r.get('input_record_id'):r for r in receipts
              if r.get('receipt_type') in ('dossier_model','dossier_page_model','case_synthesis','case_synthesis_page','dossier_model_partial')}
    rows=[]
    for r in store.list('review_input',cid):
        if not current(r): continue
        receipt=by_input.get(r.get('id'))
        if receipt and ((receipt.get('task_id') is not None and receipt.get('task_id') != r.get('task_id')) or
                        ('generation' in receipt and receipt.get('generation') is not None and receipt.get('generation') != r.get('generation')) or
                        (r.get('batch_id') and receipt.get('batch_id') != r.get('batch_id'))):
            receipt=None
        partial=receipt and receipt.get('receipt_type')=='dossier_model_partial'
        rows.append({'included_ids':r.get('included_ids',[]), 'prepared':True,
                     'transmitted':True if receipt else None, 'valid_assessed':bool(receipt) and not partial,
                     'scope_manifest_present':bool(r.get('evidence_presentation') or r.get('pack',{}).get('evidence_presentation')),
                     'evidence_presentation':r.get('evidence_presentation') or r.get('pack',{}).get('evidence_presentation')})
        if partial:
            rows.append({**rows[-1],'included_ids':receipt['valid_assessed_ids'],'valid_assessed':True})
    rows += [{'included_ids':r.get('valid_ids',[]), 'prepared':True,
             'transmitted':True, 'valid_assessed':False, 'scope_manifest_present':False}
             for r in store.list('investigation_plan',cid) if current(r)]
    for r in store.list('synthesis_input',cid):
        if not current(r): continue
        receipt=by_input.get(r.get('id'))
        if receipt and ((receipt.get('task_id') is not None and receipt.get('task_id') != r.get('task_id')) or
                        ('generation' in receipt and receipt.get('generation') is not None and receipt.get('generation') != r.get('generation'))):
            receipt=None
        rows.append({'included_ids':[o['id'] for o in r.get('pack',{}).get('observations',[])],
                     'prepared':True, 'transmitted':True if receipt else None, 'valid_assessed':bool(receipt),
                     'scope_manifest_present':bool(r.get('evidence_presentation') or r.get('pack',{}).get('evidence_presentation')),
                     'evidence_presentation':r.get('evidence_presentation') or r.get('pack',{}).get('evidence_presentation')})
    return rows


def project(document, review_inputs):
    observations=document['observations'];ids={o['id'] for o in observations}
    units=document['dossiers'];prepared=set();transmitted=set();assessed=set();legacy_unknown=False
    unknown=set();span_ids=set();transmitted_spans=set();assessed_spans=set();partial_spans=set();manifest_count=0
    for r in review_inputs:
        selected=set(r.get('included_ids',[]))&ids
        if r.get('prepared'): prepared.update(selected)
        if r.get('transmitted'): transmitted.update(selected)
        elif r.get('transmitted') is None: unknown.update(selected)
        if r.get('valid_assessed'): assessed.update(selected)
        manifest=r.get('evidence_presentation') or r.get('pack',{}).get('evidence_presentation')
        if r.get('transmitted') and not r.get('scope_manifest_present'): legacy_unknown=True
        if manifest:
            manifest_count+=1
            for span in manifest.get('spans',[]):
                if span.get('observation_id') not in selected or not span.get('span_id'):continue
                span_ids.add(span['span_id'])
                if r.get('transmitted'):transmitted_spans.add(span['span_id'])
                if r.get('valid_assessed'):assessed_spans.add(span['span_id'])
                if span.get('extent') != 'complete_field' or span.get('truncated'):partial_spans.add(span['span_id'])
    presented=transmitted
    judged={oid for d in units if d['status']=='reviewed' for oid in (d.get('finding') or {}).get('observation_ids',[]) if oid in ids}
    sources=[];windows_sources=[]
    for o in observations:
        if o['type']=='windows_environment':
            windows_sources.extend({'evidence_id':o['evidence_id'],**row} for row in o['fields'].get('source_coverage',[]))
        if o['type']!='linux_environment':continue
        for family,counts in o['fields'].get('coverage_map',{}).get('families',{}).items():
            sources.append({'evidence_id':o['evidence_id'],'family':family,**counts})
    statuses=[t.get('status') for t in document['coverage'] if t.get('action')!='investigation_report']
    case_status=document.get('case',{}).get('status')
    # Queued work remains queued after a budget stop. Coverage is not the
    # execution lifecycle and must not keep a stopped case 'running'.
    terminated=(case_status in ('complete','completed','quiescent','resource_limit','paused','failed','error')
                if case_status is not None else bool(statuses) and not any(s in ('queued','running') for s in statuses))
    unreviewed=sum(d['status']!='reviewed' for d in units)
    question_refs={i for q in document.get('case_questions',[]) if q.get('status') in ('open','reopened','held')
                   for i in q.get('observation_ids',[])}
    obligations=[d for d in units if d['status']!='deferred'
        or question_refs.intersection(d.get('observation_ids',[]))]
    unfinished_obligations=sum(d['status']!='reviewed' for d in obligations)
    open_objections={o['id'] for d in units for o in (d.get('finding') or {}).get('open_objections',[])}
    open_objections.update(o['id'] for o in document.get('objections',[]) if o['status']=='open')
    ledger=document['check_ledger']
    gaps=not statuses or any(s not in ('covered','covered_zero') for s in statuses) or unfinished_obligations or open_objections or ledger['not_executed'] or ledger['unassessed_contracts']
    gaps=gaps or any(s['status']!='reviewed' or s.get('selection_is_partial') for s in document.get('case_synthesis',[]))
    gaps=gaps or any(not s.get('complete',False) for s in windows_sources)
    frontier=document.get('investigation_frontier',{})
    questions=document.get('case_questions',[])
    open_questions=sum(q.get('status') in ('open','reopened','held') for q in questions)
    gaps=gaps or open_questions
    # New projections distinguish an admitted investigation obligation from a
    # literal candidate. Legacy frontiers without the contract stay qualified.
    gaps=gaps or frontier.get('unfinished_obligations',
        frontier.get('open_leads',0) or frontier.get('deferred_discovery',0))
    intents=document.get('test_intents',[])
    feasible=[i for i in intents if i.get('admission',{}).get('eligible') is True
        and i.get('assessment_status')!='assessed']
    blocked=[i for i in intents if i.get('admission',{}).get('eligible') is False]
    gaps=gaps or bool(feasible)
    stop_reason=(document.get('execution_closure') or {}).get('reason') or document.get('case',{}).get('end_reason')
    execution_exit=('pause_requested' if case_status=='pause_requested' else
        'model_service' if case_status=='paused' and stop_reason in ('model_service_circuit','model_configuration') else
        'user_paused' if case_status=='paused' and stop_reason=='user_paused' else
        'paused' if case_status=='paused' else
        'budget' if case_status=='resource_limit' else
        'error' if case_status in ('failed','error') or any(s=='failed' for s in statuses) and terminated else
        'normal' if terminated else 'running')
    return {'execution_terminated':terminated,'analysis_complete_in_supported_scope':terminated and not gaps,
        'execution_exit':execution_exit,'execution_stop_reason':stop_reason,
        'supported_scope_review':'complete' if terminated and not gaps else 'partial' if ids or units else 'unknown',
        'feasible_unfinished_tests':[i['id'] for i in feasible],
        'capability_or_input_blocked_tests':[{'id':i['id'],'reason':i['admission']['reason'],
            'question_id':i.get('question_id')} for i in blocked],
        'question_assessments':[{'question_id':q['id'],'state':q.get('status','unknown'),
            'decision_id':q.get('decision_id'),'dependency_revision':q.get('dependency_revision'),
            'scope':'Question disposition, not whole-incident clearance.'} for q in questions],
        'label':'실행 종료 · 분석 공백 있음' if terminated and gaps else '지원 범위 처리 완료' if terminated else '진행 중 스냅샷',
        'collection_denominators':sources,'windows_source_coverage':windows_sources,'indexed_observations':len(ids),
        'ai_prepared_observations':len(prepared),'ai_transmitted_observations':len(transmitted),
        'ai_valid_assessed_observations':len(assessed),'ai_presented_observations':len(presented),
        'presentation_scope':{'legacy_scope_unknown':legacy_unknown,
                              'transmission_unknown_observations':len(unknown),
                              'manifest_count':manifest_count,'exact_span_ids':len(span_ids),
                              'prepared_spans':len(span_ids),'transmitted_spans':len(transmitted_spans),
                              'valid_assessed_spans':len(assessed_spans),
                              'partial_or_truncated_spans':len(partial_spans),
                              'exact_span_ids_basis':'prepared, not evaluated or whole-file byte coverage',
                              'scope_basis':'prepared/transmitted/valid-assessed lifecycle; source spans only when evidence_presentation manifest is present'},
        'judgment_cited_observations':len(judged),'review_units':len(units),'unreviewed_units':unreviewed,
        'review_obligations':len(obligations),'unfinished_review_obligations':unfinished_obligations,
        'inventory_not_reviewed':sum(d['status']=='deferred' and d not in obligations for d in units),
        'inventory_limit':'Unreviewed candidates remain unknown; they are not automatically obligations or harmless findings.',
        'untriaged_deferred_units':sum(d.get('status')=='deferred' for d in units),
        'reviewed_context_units':sum(d.get('status')=='reviewed' and d.get('disposition')=='reviewed_context' for d in units),
        'open_objections':len(open_objections),
        'question_status':{'total':len(questions),'open_or_held':open_questions,
            'scoped_answered':sum(q.get('status')=='scoped_answered' for q in questions)},
        'publication_status':'qualified_snapshot' if gaps else 'scoped_supported',
        'open_leads':frontier.get('open_leads',0),'deferred_discovery':frontier.get('deferred_discovery',0),
        'reused_review_units':sum(bool(d.get('reused_from')) for d in units),
        'source_records_indexed':sum(o['fields'].get('events',0) for o in observations if o['type'] in ('linux_environment','windows_environment')),
        'scope':'Linux 수집 분모는 파일 수, Windows 수집 범위는 소스별 레코드 수·파싱 실패·보존 기간을 별도 표시. AI 제시·판단 인용은 고유 관측 ID 수. 표본/압축 문맥은 원문 전수검토가 아니며, 무결성 성공은 분석 완료가 아님.'}


def materials(document):
    rows=[]
    for intent in document.get('test_intents',[]):
        admission=intent.get('admission') or {}
        if admission.get('eligible') is not False:continue
        design=admission.get('design') or {}
        internal=admission.get('reason') in ('unsupported_tool','capability_intent_mismatch','required_input_unavailable')
        rows.append({'category':'internal_test_blocked' if internal else 'input_availability_unverified',
            'material':design.get('immediate_observable','미제공'),'blocked_conclusion':design.get('expected_update',''),
            'action':'기존 자료·도구의 검사 능력·원장 참조·입력 조건을 내부에서 수정하고 대체 검사를 설계. 외부 자료 필요를 뜻하지 않습니다.' if internal else
                '실제 미확보 자료와 질문의 결속을 확인한 뒤 사용자에게 요청.',
            'user_action_required':False if internal else None,
            'reference':intent['id'],'question_id':intent.get('question_id'),
            'missing_observation_ids':admission.get('missing_observation_ids',[])})
    for check in document['check_ledger']['checks']:
        if check['job_ids'] and all(c['evaluation_status']=='assessed' for c in check['contracts']):continue
        rows.append({'category':'retained' if check['job_ids'] else 'image_extractable',
            'material':check['request'].get('path') or check['request'].get('query') or check['request']['tool'],
            'blocked_conclusion':check['request'].get('reason','가설 판별조건 미평가'),
            'action':'보존된 검사 결과를 가설별 재평가' if check['job_ids'] else '같은 이미지에서 명시된 범위 추가 추출',
            'reference':check['scope_key']})
    # Coverage-domain numbers are not investigation findings or acquisition
    # needs. Report only actual unresolved test contracts above. Remaining
    # interpretive questions and unsupported acquisition scope are preserved
    # separately, never expanded into boilerplate demands on the user.
    return rows
