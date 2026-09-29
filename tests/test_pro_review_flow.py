from copy import deepcopy
from workbench.store import Store
from workbench.partial_review import isolate
from workbench.completion import presentations,project
from workbench.review_queue import schedule
from workbench.review_stream import scope_fingerprint
from workbench.question_engine import view


def partial_fixture(tmp_path):
    s=Store(tmp_path/'c.db');cid='c'
    t={'id':'t','evidence_id':'e','retry_generation':0}
    ds=[s.add('dossier',cid,task_id='t',evidence_id='e',generation=0,
        observation_ids=[str(i)],status='pending') for i in range(2)]
    b=s.add('dossier_batch',cid,task_id='t',evidence_id='e',dossier_ids=[d['id'] for d in ds],
        round=1,attempts=0,status='pending',job_ids=[],deferred_checks=[])
    f=[{'dossier_id':d['id'],'title':'bounded','judgment':'미확인','reason':'scope only',
        'observation_ids':[str(i)]} for i,d in enumerate(ds)]
    out={'findings':f,'next_checks':[],'check_assessments':[]}
    issues=[{'code':'unknown_observation','dossier_id':ds[1]['id']}]
    return s,cid,t,ds,b,out,issues


def test_independent_valid_sibling_is_preserved_and_invalid_is_repaired(tmp_path):
    s,cid,t,ds,b,out,issues=partial_fixture(tmp_path)
    original=deepcopy(out)
    assert isolate(s,cid,t,b,out,issues,{}, {'input_record_id':'input','included_ids':['0','1']},'diag')
    assert s.get(ds[0]['id'])['status']=='reviewed'
    assert s.get(ds[1]['id'])['status']=='pending'
    assert s.get(b['id'])['status']=='split'
    child=[x for x in s.list('dossier_batch',cid) if x['id']!=b['id']][0]
    assert child['dossier_ids']==[ds[1]['id']] and child['attempts']==1
    assert out==original and not s.list('investigation_job',cid)


def test_global_or_source_selection_error_prevents_partial_adoption(tmp_path):
    s,cid,t,ds,b,out,issues=partial_fixture(tmp_path)
    assert not isolate(s,cid,t,b,out,issues+[{'code':'excerpt_selection_invalid'}],{}, {'input_record_id':'in'},'diag')
    assert all(s.get(d['id'])['status']=='pending' for d in ds)


def test_partial_acceptance_does_not_count_rejected_source_as_assessed(tmp_path):
    s,cid,t,ds,b,out,issues=partial_fixture(tmp_path)
    r=s.add('review_input',cid,task_id='t',generation=0,batch_id=b['id'],included_ids=['0','1'])
    assert isolate(s,cid,t,b,out,issues,{}, {'input_record_id':r['id'],'included_ids':['0','1']},'diag')
    rows=presentations(s,cid,{'t':t})
    doc={'observations':[{'id':str(i),'type':'event','fields':{}} for i in range(2)],
         'dossiers':[],'coverage':[],'check_ledger':{'not_executed':0,'unassessed_contracts':0}}
    stats=project(doc,rows)
    assert stats['ai_transmitted_observations']==2
    assert stats['ai_valid_assessed_observations']==1


def test_working_memory_changes_do_not_restart_source_pages():
    a={'observations':[{'id':'o','fields':{'excerpt':'raw'}}],
       'question_context':{'questions':[{'id':'q','working_state':{'tests_total':0}}]}}
    b=deepcopy(a);b['question_context']['questions'][0]['working_state']['tests_total']=1
    assert scope_fingerprint(a)==scope_fingerprint(b)
    b['observations'][0]['fields']['excerpt']='changed'
    assert scope_fingerprint(a)!=scope_fingerprint(b)


def test_question_metadata_budget_drops_whole_old_tests_not_conditions():
    q={'id':'q','question':'question','working_state':{'tests_total':4,'tests_omitted':0,
        'previous_tests':[{'id':str(i),'conditions':{'success_condition':'a'*1600}} for i in range(4)]}}
    memory={'corpus_revision':'r','questions':[q]}
    result=view(memory)['questions'][0]['working_state']
    assert result['tests_omitted']==3
    assert len(result['previous_tests'][0]['conditions']['success_condition'])==1600
    assert len(q['working_state']['previous_tests'])==4


def test_question_admission_precedes_generic_flood_without_clearing_it():
    ds=[{'id':str(i),'baseline':False,'review_family':'other','group_key':str(i),
         'review_priority':0,'observation_ids':[str(i)]} for i in range(30)]
    q={'id':'q','status':'open','investigation_priority':'high','observation_ids':['29']}
    assert schedule(ds,limit=4,questions=[q])[0]['id']=='29'
    assert len(ds)==30  # not destructive merging or a benign whitelist


def test_pause_budget_and_feasible_unfinished_work_are_separate_axes():
    doc={'case':{'status':'paused'},'observations':[],'dossiers':[],'coverage':[],
         'check_ledger':{'not_executed':0,'unassessed_contracts':0},
         'test_intents':[{'id':'i','admission':{'eligible':True},'status':'reserved'}]}
    out=project(doc,[])
    assert out['execution_exit']=='user_paused'
    assert out['feasible_unfinished_tests']==['i']
    assert not out['analysis_complete_in_supported_scope']
    doc['case']['status']='resource_limit'
    assert project(doc,[])['execution_exit']=='budget'
