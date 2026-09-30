from copy import deepcopy

from workbench.case_memory import sync
from workbench.explanation_proposals import adopt
from workbench.review_policy import second_pass, returned_rank
from workbench.store import Store
from workbench.completion import project, materials
from scripts.audit_question_progress import audit


def test_candidates_are_optional_scoped_deduped_and_not_facts(tmp_path):
    s=Store(tmp_path/'db');task={'id':'t'};e={'id':'e'}
    root=sync(s,'c',task,e,[],[{'id':'c','question':'What occurred?','source_kind':'case_question'}])['questions']
    assert adopt(s,'c',task,e,{},'r',{'o'},root)==[]
    proposal={'question_id':'','explanation':'A competing explanation',
        'discriminating_question':'Can these records distinguish it?',
        'trigger_observation_ids':['o'],'next_discriminator':'Compare a supplied record'}
    a=adopt(s,'c',task,e,{'explanation_proposals':[proposal]},'r',{'o'},root)[0]
    assert a['judgment']=='미확인' and a['status']=='candidate'
    assert a['supporting_evidence_ids']==[] and not s.list('investigation_job','c')
    assert adopt(s,'c',task,e,{'explanation_proposals':[proposal]},'r2',{'o'},root)[0]['id']==a['id']
    b=adopt(s,'c',{'id':'t','retry_generation':1},e,{'explanation_proposals':[proposal]},'r3',{'o'},root)[0]
    assert b['id']!=a['id'] and b['business_source_id']==a['business_source_id']
    bad={**proposal,'trigger_observation_ids':['foreign']}
    assert adopt(s,'c',task,e,{'explanation_proposals':[bad]},'r',{'o'},root)==[]
    assert s.list('receipt','c')[-1]['failure_category']=='proposal_scope'


def test_conditional_second_review_is_only_for_bound_background():
    background={'findings':[{'timeline_role':'배경','incident_relevance':{'level':'context'},
        'judgment':'확인','fact_assertions':[{'observation_id':'o'}]}]}
    policy={'second_review_policy':'conditional-v1'}
    assert not second_pass(policy,background,{}, {})
    assert second_pass({},background,{}, {}) # Old/default policy remains controlled.
    assert second_pass({'second_review_policy':'always'},background,{}, {})
    for field,value in [('alternatives',['normal explanation']),('remaining_checks',['execution']),
                        ('timeline_role','핵심'),('fact_assertions',[])]:
        changed=deepcopy(background);changed['findings'][0][field]=value
        assert second_pass(policy,changed,{}, {})
    assert second_pass(policy,background,{}, {'job_ids':['job']})
    assert second_pass(policy,background,{'open_objections':[{'id':'x'}]}, {})
    assert second_pass(policy,background,{'review_stream':{'id':'stream'}}, {})


def test_returned_priority_and_audit_preserve_logical_physical_difference():
    intents=[{'kind':'test_intent','id':'a','status':'partial','scope':{'task_id':'t'},
        'assessment_status':'unassessed','result_scope':{'job_id':'j'}},
        {'kind':'test_intent','id':'b','status':'complete','scope':{'task_id':'t'},
        'assessment_status':'unassessed','result_scope':{'job_id':'j'}}]
    assert returned_rank({'job_ids':['j']},intents)==0
    assert returned_rank({'job_ids':['other']},intents)==1
    result=audit([{'kind':'task','id':'t'}, {'kind':'investigation_job','id':'j','task_id':'t',
        'request':{'tool':'search','query':'fixture'}},*intents])['returned_unassessed']
    assert result['logical_intents']==2 and result['linked_distinct_physical_results']==1
    assert result['importance_assessed']==0


def test_inventory_is_not_a_completion_obligation_or_a_benign_finding():
    doc={'case':{'status':'complete'},'observations':[],
        'dossiers':[{'id':'d','status':'deferred','observation_ids':['o']}],
        'coverage':[{'status':'covered'}],'check_ledger':{'not_executed':0,'unassessed_contracts':0},
        'investigation_frontier':{'unfinished_obligations':0,'open_leads':1,'deferred_discovery':1}}
    result=project(doc,[])
    assert result['inventory_not_reviewed']==1 and result['unfinished_review_obligations']==0
    assert result['analysis_complete_in_supported_scope']
    doc['case_questions']=[{'id':'q','status':'open','observation_ids':['o']}]
    result=project(doc,[])
    assert result['unfinished_review_obligations']==1 and not result['analysis_complete_in_supported_scope']


def test_internal_admission_failure_does_not_request_external_collection():
    doc={'test_intents':[{'id':'i','admission':{'eligible':False,'reason':'unsupported_tool'}}],
        'check_ledger':{'checks':[]}}
    result=materials(doc)[0]
    assert result['category']=='internal_test_blocked' and result['user_action_required'] is False


def test_semantic_recall_requires_correct_interpretation_labels():
    rows=[{'kind':'task','id':'t'},{'kind':'dossier','id':'d','task_id':'t','status':'reviewed',
        'finding':{'observation_ids':['o']}}]
    oracle={'critical_items':[{'recoverable':True,'observation_ids':['o'],'correctly_interpreted':False}]}
    result=audit(rows,oracle)['analyst_oracle_metrics']
    assert result['critical_citation_coverage']==1 and result['critical_recall']==0
