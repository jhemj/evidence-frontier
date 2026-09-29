from workbench.store import Store
from workbench import case_memory, question_engine
from workbench.review_contracts import contract


def test_only_matching_condition_is_assessed_and_replay_does_not_erase_it(tmp_path):
    store=Store(tmp_path/'case.db');cid='case';task={'id':'t'}
    call={'hypothesis_id':'d','success_condition':'positive','refutation_condition':'contrary'}
    condition=contract(call)
    scope={'task_id':'t','generation':0}
    a=case_memory.reserve(store,cid,{'question_key':'q','version':1},'search',scope,
        {k:call.get(k,'') for k in ('success_condition','refutation_condition','inconclusive_condition')})
    b=case_memory.reserve(store,cid,{'question_key':'q','version':1},'search',scope,
        {'success_condition':'different'})
    job=store.add('investigation_job',cid,task_id='t',status='ingested',contracts=[condition],
        test_intent_ids=[a['id'],b['id']],observation_ids=['o'],result_scope={'complete':True})
    question_engine.finish_intents(store,cid,job)
    assessment={'check_id':job['id'],'dossier_id':'d','contract_id':condition['contract_id'],
        'outcome':'refutes','reason':'positive contrary record','observation_ids':['o']}
    for _ in range(2):question_engine.record_check_assessments(store,cid,task,{'check_assessments':[assessment]},'receipt')
    question_engine.finish_intents(store,cid,job)
    assert store.get(a['id'])['assessment_status']=='assessed'
    assert len(store.get(a['id'])['assessment_history'])==1
    assert store.get(b['id'])['assessment_status']=='unassessed'


def test_partial_fetch_never_becomes_logical_support(tmp_path):
    store=Store(tmp_path/'case.db')
    intent=case_memory.reserve(store,'c',{'question_key':'q'},'search',{})
    result=case_memory.complete(store,'c',intent['id'],{'observation_ids':[]},False)
    assert result['status']=='partial' and result['assessment_status']=='unassessed'
