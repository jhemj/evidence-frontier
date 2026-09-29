"""Controller-owned question scheduling. Proposals never grant tool authority.

Coverage domains remain a checklist, not a mandatory ordered investigation.
Question and test ledgers retain omitted work independently of model prompts.
"""
from . import case_memory
from .retrieval import fingerprint_scope, source_origin
from .hypothesis_ledger import current_scope


def refresh(controller, cid, evidence, task):
    store=controller.store
    observations=[o for o in controller.active_observations(cid) if o.get('evidence_id')==evidence['id']]
    # Dynamic hypotheses are durable records and may outlive the task/evidence
    # generation that created them.  Build the same scope used by the ledger so
    # refresh cannot pull superseded or cross-generation hypotheses into the
    # current question corpus.  The supplied task/evidence are included for
    # lightweight callers that have not persisted those envelopes yet.
    tasks={t['id']:t for t in store.list('task',cid)}
    scoped_task={**task, 'evidence_id':task.get('evidence_id',evidence['id'])}
    tasks[scoped_task['id']]=scoped_task
    active={e['id'] for e in store.list('evidence',cid) if e.get('connected',True)}
    if evidence.get('connected',True):active.add(evidence['id'])
    def in_current_scope(hypothesis):
        # Investigation and judgment are different tasks. Share current
        # source-bound questions across them, not obsolete/unknown generations.
        return current_scope(hypothesis,tasks,active)
    hypotheses=[h for h in store.list('hypothesis',cid) if h.get('evidence_id')==evidence['id']
                and h.get('hypothesis_kind')=='dynamic' and not h.get('superseded')
                and in_current_scope(h)]
    claims=[c for c in store.list('claim',cid) if c.get('task_id')==task['id']]
    objections=[o for o in store.list('objection',cid) if o.get('task_id')==task['id']
                and o.get('generation',0)==task.get('retry_generation',0) and o.get('status')=='open']
    roots=[{'id':cid+':'+evidence['id'], 'source_kind':'case_question',
            'question':store.get(cid).get('question') or '제공한 증거로 확인 가능한 사건 경과와 조사 한계는 무엇인가?',
            'observation_ids':[]}]
    memory=case_memory.sync(store,cid,task,evidence,observations,hypotheses+roots,objections,claims)
    rows=memory['questions']
    from .scenarios import project as scenario_projection
    scenarios=scenario_projection({'task':list(tasks.values()),'evidence':[evidence],
        'observation':observations,'hypothesis':hypotheses,'dossier':store.list('dossier',cid)})
    priorities={h['id']:h for h in scenarios['cards'] if h['assessment_current']}
    for q in rows:
        h=next((priorities[i] for i in q['source_ids'] if i in priorities),{})
        q['investigation_priority']=h.get('investigation_priority','normal')
        q['priority_reason']=h.get('priority_reason','')
    decisions=store.list('decision_revision',cid)
    visits={q['id']:sum(d.get('question_id')==q['id'] for d in decisions) for q in rows}
    rows.sort(key=lambda q:(q.get('status')!='reopened',q.get('source_kind')!='objection',
        q.get('status') in ('held','scoped_answered'),visits[q['id']]//3,
        {'high':0,'normal':1,'low':2}[q['investigation_priority']],visits[q['id']],q.get('created_at',''),q['id']))
    intents=store.list('test_intent',cid)
    for q in rows:
        tests=[r for r in intents if r.get('question_id')==q['id']]
        q['working_state']={
            'tests_total':len(tests),'tests_omitted':max(0,len(tests)-6),
            'previous_tests':[{k:r.get(k) for k in ('id','tool','status','admission','conditions',
                'result_scope','assessment_status','latest_assessment')} for r in tests[-6:]],
            'latest_decision':next((d for d in reversed(decisions) if d.get('question_id')==q['id']),None),
            'related_observation_ids':q.get('related_observation_ids',[]),
            'scope':'Derived ledger view, not new evidence. Reuse result references explicitly; rephrasing is not a new physical test.'}
    return {**memory,'questions':rows}


def checkpoint(store,cid,task,hypothesis_id,finding,receipt_id,*,pending=False):
    questions=case_memory.project(store,cid,task)
    for question in questions:
        if hypothesis_id not in question['source_ids']:continue
        refs=finding.get('observation_ids',[])
        status='held' if pending or finding.get('open_objections') or not refs or finding.get('judgment')=='미확인' else 'scoped_answered'
        case_memory.assess(store,cid,question['id'],status,finding.get('reason','제한 범위 중간 판단'),
            refs,receipt_id,question['dependency_revision'])


def view(memory, limit=4):
    rows=memory['questions'][:limit]
    result={'corpus_revision':memory['corpus_revision'], 'total':len(memory['questions']),
        'omitted':max(0,len(memory['questions'])-len(rows)),
        'questions':[{k:q.get(k) for k in ('id','question','source_kind','source_ids','status','version',
                'dependency_revision','observation_ids','investigation_priority','priority_reason','working_state')} for q in rows],
        'instruction':'Focus on unresolved questions and strongest alternatives. Coverage categories are not hypotheses. '
            'Propose new hypotheses naturally; do not manufacture ten conclusions. Each tool should name question_id '
            'and choose a discriminating test by uncertainty reduction, impact and cost, not UI rank. '
            'and supporting/refuting/inconclusive conditions. Use question_updates for the supplied questions only. '
            'A held or scoped answer is not case closure. Omitted questions remain in the durable ledger.'}
    # Planning metadata must not eat the source-page envelope. Drop whole old
    # test descriptions, never source bytes or silently shorten test conditions.
    from copy import deepcopy
    from .review_context import serialize
    result=deepcopy(result)
    for q in result['questions']:
        state=q.get('working_state') or {}
        related=state.pop('related_observation_ids',[])
        state['related_observation_count']=len(related)
        while state.get('previous_tests') and len(serialize(state))>2200:
            state['previous_tests'].pop(0)
            state['tests_omitted']=state.get('tests_omitted',0)+1
        if len(serialize(state))>2200:
            prior=state.get('latest_decision') or {}
            state['latest_decision']={k:prior.get(k) for k in ('id','status','question_version','dependency_revision')}
            state['decision_text_omitted']=True
        q['working_state']=state
    return result


def apply_updates(store,cid,task,plan):
    supplied={q['id']:q for q in plan.get('question_context',{}).get('questions',[])}
    valid=set(plan.get('valid_ids',[]))
    for proposal in plan['output'].get('question_updates',[]):
        q=supplied.get(proposal['question_id']);refs=proposal.get('observation_ids',[])
        if not q or not set(refs)<=valid or proposal['status']=='scoped_answered' and not refs:
            store.add('receipt',cid,task_id=task['id'],receipt_type='question_update_rejected',
                source_plan_id=plan['id'],error='질문·인용 범위 또는 제한 답변의 근거 없음')
            continue
        try:
            case_memory.assess(store,cid,q['id'],proposal['status'],proposal['reason'],refs,
                plan['id'],q['dependency_revision'])
        except ValueError:
            store.add('receipt',cid,task_id=task['id'],receipt_type='question_update_rejected',
                source_plan_id=plan['id'],error='질문 의존성 변경으로 이전 결과 채택 거부')


def reserve(store,cid,task,question,request,evidence,source_run):
    scope={'evidence_id':evidence['id'],'signature':evidence.get('signature'),
        'task_id':task['id'],'generation':task.get('retry_generation',0),
        'source_run':source_run,'parser_version':'tool-scope-v2', 'request':fingerprint_scope(request)}
    conditions={k:request.get(k,'') for k in ('success_condition','refutation_condition','inconclusive_condition')}
    from .test_admission import assess
    available=[o['id'] for o in store.list('observation',cid) if o.get('evidence_id')==evidence['id']]
    admission=assess(request,store.get(cid).get('target_os','linux'),available)
    scope['test_design']=admission['design']
    intent=case_memory.reserve(store,cid,question,request['tool'],scope,conditions)
    values={'admission':admission}
    if not admission['eligible']:values.update(status='blocked',blocked_reason=admission['reason'])
    return store.update(intent['id'],**values)


def finish_intents(store,cid,job):
    for ident in job.get('test_intent_ids',[]):
        case_memory.complete(store,cid,ident,{'job_id':job['id'],
            'observation_ids':job.get('observation_ids',[]),'scope':job.get('result_scope',{})},
            complete=bool(job.get('result_scope',{}).get('complete')))


def batch_priority(batch,questions,dossiers):
    members={i for did in batch['dossier_ids'] for i in dossiers[did].get('observation_ids',[])}
    related=[q for q in questions if members.intersection(q.get('observation_ids',[]))]
    # Scheduler priority only; never evidence confidence or incident relevance.
    return (batch['status']!='await_checks',not any(q.get('status')=='reopened' for q in related),
        not any(q.get('source_kind')=='objection' for q in related),not bool(related),
        not any(q.get('investigation_priority')=='high' for q in related),
        not bool(batch.get('parent_batch_id')),batch['created_at'],batch['id'])


def reopen_deferred(store,cid,task,questions):
    """A new question/object link can admit retained, untriaged evidence.

    This does not label a program benign or change an accepted source fact.
    It consumes the same review budget and creates at most one queue item/tick.
    """
    from .review_contracts import review_exhausted
    if review_exhausted(store,cid,task,2):return None
    candidates=[q for q in questions if q.get('source_kind')!='case_question'
        and q.get('status') in ('open','reopened')]
    for q in candidates:
        refs=set(q.get('observation_ids',[])+q.get('related_observation_ids',[]))
        for d in store.list('dossier',cid):
            if d.get('task_id')!=task['id'] or d.get('generation',0)!=task.get('retry_generation',0):continue
            if d.get('status')!='deferred' or d.get('deferred_reason')!='review_budget':continue
            if not refs.intersection(d.get('observation_ids',[])):continue
            store.update(d['id'],status='pending',disposition='reopened_untriaged',
                reopened_by_question=q['id'],reopened_dependency_revision=q.get('dependency_revision'))
            return store.add('dossier_batch',cid,task_id=task['id'],evidence_id=d['evidence_id'],
                generation=task.get('retry_generation',0),dossier_ids=[d['id']],status='pending',
                round=0,attempts=0,output=None,job_ids=[],deferred_checks=[],
                reopened_by_question=q['id'])
    return None


def record_check_assessments(store,cid,task,output,receipt_id):
    """Accepted logical outcomes only; fetching a source never proves a claim."""
    from .review_contracts import contract,contracts
    jobs={j['id']:j for j in store.list('investigation_job',cid)
          if j.get('task_id')==task['id'] and j.get('generation',0)==task.get('retry_generation',0)}
    for assessment in output.get('check_assessments',[]):
        job=jobs.get(assessment['check_id'])
        if not job or assessment.get('evaluation_status')=='unassessed':continue
        condition=next((c for c in contracts(job) if c['dossier_id']==assessment.get('dossier_id')
                        and c['contract_id']==assessment.get('contract_id')),None)
        if condition is None:continue
        for ident in job.get('test_intent_ids',[]):
            intent=store.get(ident,'test_intent')
            if intent['case_id']!=cid or intent.get('scope',{}).get('task_id')!=task['id']:continue
            if contract({'hypothesis_id':condition['dossier_id'],**intent['conditions']})['contract_id']!=condition['contract_id']:continue
            history=intent.get('assessment_history',[])
            if any(r['receipt_id']==receipt_id and r['assessment']==assessment for r in history):continue
            store.update(ident,assessment_status='assessed',assessment_history=history+[
                {'receipt_id':receipt_id,'assessment':assessment}],latest_assessment=assessment)
