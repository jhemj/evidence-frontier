import hashlib
from copy import deepcopy

import pytest

from workbench.claim_review import build_pack, validate_scope, input_hash
from workbench.controller import Controller
from workbench.investigation import compact_observation
from workbench.investigation_graph import Investigation
from workbench.review_context import InputBudgetError, model_view_size
from workbench.review_stream import resolved
from workbench.store import Store


def setup(tmp_path):
    c=Controller(Store(tmp_path/'case.db'),tmp_path)
    cid=c.create('claim input','','standard')['id'];c.store.update(cid,status='running')
    e=c.store.add('evidence',cid,path='fixture.E01',signature='sig',connected=True)
    t=c.store.add('task',cid,action='linux_investigate',evidence_id=e['id'])
    c.store.add('config','',provider={'model':'local','protocol':'ollama'})
    run=c.store.add('investigation_run',cid,task_id=t['id'],evidence_id=e['id'],
        signature='sig',source_run='RUN-'+'a'*32,model_calls=0,max_model_calls=6,
        tool_calls=0,max_tool_calls=36,challenge_calls=0,max_challenge_calls=10,
        review_calls=0,max_review_calls=5,stop_reason=None)
    return c,cid,e,t,run


def observation(c,cid,e,index=0,**fields):
    return c.store.add('observation',cid,evidence_id=e['id'],type='linux_command',
        timestamp=None,source_location=f'fixture:/logs/{index}',
        fields={'path':f'/logs/{index}','excerpt':f'command {index}',**fields})


def claim(c,cid,t,rows,**fields):
    return c.store.add('claim',cid,task_id=t['id'],text='A scoped recorded action',
        observation_ids=[o['id'] for o in rows],challenge_status='specified',
        falsification=None,status='candidate',**fields)


def test_all_primary_sources_survive_diverse_background_and_no_sixteen_cap(tmp_path):
    c,cid,e,t,_=setup(tmp_path)
    primary=[observation(c,cid,e,i) for i in range(20)]
    for i in range(80):
        c.store.add('observation',cid,evidence_id=e['id'],type='linux_detection',
            timestamp=None,source_location=f'fixture:/other/{i}',
            fields={'path':f'/other/{i}','rule_id':f'rule-{i}','excerpt':'background'})
    item=claim(c,cid,t,primary)
    pack=build_pack(c,item)
    shown=resolved(pack)['observations']
    assert shown==[compact_observation(o,preserve_content=True) for o in primary]
    assert pack['selection_audit']['missing_required']==[]
    assert pack['selection_audit']['included']==20
    assert pack['selection_audit']['omitted']==80


def test_long_tail_lists_and_counterevidence_are_lossless(tmp_path):
    c,cid,e,t,_=setup(tmp_path)
    body=''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(110))
    a=observation(c,cid,e,1,excerpt=body,rows=list(range(25)))
    b=observation(c,cid,e,2)
    item=claim(c,cid,t,[a],counterevidence_ids=[b['id']])
    pack=build_pack(c,item)
    assert resolved(pack)['observations']==[compact_observation(o,preserve_content=True) for o in (a,b)]
    assert model_view_size(pack)<=36000


def test_optional_overflow_is_disclosed_mandatory_overflow_is_blocked(tmp_path):
    c,cid,e,t,_=setup(tmp_path)
    a=observation(c,cid,e,1)
    huge=''.join(hashlib.sha256(str(i).encode()).hexdigest() for i in range(1200))
    b=observation(c,cid,e,2,excerpt=huge)
    item=claim(c,cid,t,[a])
    job={'id':'job-bound','claim_ids':[item['id']],'request':{'tool':'search'},
         'purpose':'challenge','observation_ids':[b['id']],'result_status':'covered',
         'result_scope':{'complete':True}}
    pack=build_pack(c,item,[job])
    assert [o['id'] for o in resolved(pack)['observations']]==[a['id']]
    assert pack['executed_checks'][0]['unpresented_observation_ids']==[b['id']]
    large=claim(c,cid,t,[a,b])
    with pytest.raises(InputBudgetError):build_pack(c,large)


def test_only_explicitly_bound_jobs_and_sources_are_attached(tmp_path):
    c,cid,e,t,_=setup(tmp_path)
    a,b,d=[observation(c,cid,e,i) for i in range(3)]
    item=claim(c,cid,t,[a]);other=claim(c,cid,t,[d])
    base={'request':{'tool':'search'},'result_status':'partial','purpose':'challenge'}
    jobs=[{**base,'id':'mine','claim_ids':[item['id'],other['id']],'observation_ids':[b['id']]},
          {**base,'id':'foreign','claim_ids':[other['id']],'observation_ids':[d['id']]},
          {**base,'id':'legacy','observation_ids':[d['id']]}]
    pack=build_pack(c,item,jobs)
    assert [j['id'] for j in pack['executed_checks']]==['mine']
    assert {o['id'] for o in resolved(pack)['observations']}=={a['id'],b['id']}
    assert pack['review_scope']['unassigned_historical_checks']==2


def test_stale_disconnected_and_tampered_inputs_cannot_be_adopted(tmp_path):
    c,cid,e,t,_=setup(tmp_path);a=observation(c,cid,e)
    item=claim(c,cid,t,[a]);pack=build_pack(c,item)
    tampered=resolved(pack);tampered['observations'][0]['fields']['excerpt']='changed'
    with pytest.raises(InputBudgetError,match='source view changed'):validate_scope(c,item,tampered)
    c.store.update(item['id'],text='Another interpretation')
    with pytest.raises(InputBudgetError,match='Claim changed'):validate_scope(c,item,pack)
    c.store.update(item['id'],text=item['text']);c.store.update(e['id'],connected=False)
    with pytest.raises(InputBudgetError,match='disconnected'):validate_scope(c,item,pack)
    with pytest.raises(InputBudgetError,match='missing'):build_pack(c,item)


def test_automatic_review_records_input_and_claim_specific_checks(tmp_path,monkeypatch):
    c,cid,e,t,run=setup(tmp_path)
    rows=[observation(c,cid,e,i) for i in range(2)];item=claim(c,cid,t,rows)
    base={'task_id':t['id'],'status':'ingested','purpose':'challenge',
          'request':{'tool':'search'},'observation_ids':[],'result_status':'partial'}
    mine=c.store.add('investigation_job',cid,**base,claim_ids=[item['id']])
    c.store.add('investigation_job',cid,**base,claim_ids=['another-claim'])
    monkeypatch.setattr('workbench.runtime_contract.guard',lambda *args:None)
    def model(config,question,pack,**kwargs):
        assert set(item['observation_ids'])<={o['id'] for o in resolved(pack)['observations']}
        assert [j['id'] for j in pack['executed_checks']]==[mine['id']]
        return {'alternatives':['authorized activity'],'contradicting_observation_ids':[],
                'missing_checks':['authorization']},{'role':'falsifier'}
    monkeypatch.setattr('workbench.investigation_graph.consult',model)
    Investigation(c,cid,e,t).challenge({'run_id':run['id']})
    revised=c.store.get(item['id'])
    assert revised['actual_check_ids']==[mine['id']]
    record=c.store.list('falsifier_input',cid)[0]
    assert record['input_sha256']==input_hash(record['pack'])
    assert c.store.list('receipt',cid)[-1]['input_record_id']==record['id']
    with pytest.raises(ValueError,match='불변'):c.store.update(record['id'],pack={})


def test_automatic_input_gap_never_calls_model_or_marks_reviewed(tmp_path,monkeypatch):
    c,cid,e,t,run=setup(tmp_path)
    item=claim(c,cid,t,[{'id':'missing-source'}])
    def fail(*args,**kwargs):pytest.fail('missing premises must block transmission')
    monkeypatch.setattr('workbench.investigation_graph.consult',fail)
    Investigation(c,cid,e,t).challenge({'run_id':run['id']})
    assert c.store.get(item['id'])['challenge_status']=='input_blocked'
    assert c.store.get(item['id'])['falsification'] is None
    assert c.store.get(run['id'])['review_calls']==0


def test_duplicate_physical_job_retains_all_claim_bindings(tmp_path):
    c,cid,e,t,run=setup(tmp_path);inv=Investigation(c,cid,e,t)
    a=observation(c,cid,e);one=claim(c,cid,t,[a]);two=claim(c,cid,t,[a])
    calls=[{'tool':'read_file','path':'/logs/0','reason':'reread original'}]
    for item in (one,two,one):inv.admit({'run_id':run['id']},calls,'challenge',claim_id=item['id'])
    jobs=c.store.list('investigation_job',cid)
    assert len(jobs)==1 and jobs[0]['claim_ids']==[one['id'],two['id']]
    assert c.store.get(run['id'])['challenge_calls']==1


def test_manual_review_uses_identical_required_source_contract(tmp_path,monkeypatch):
    c,cid,e,t,_=setup(tmp_path);rows=[observation(c,cid,e,i) for i in range(2)]
    item=claim(c,cid,t,rows)
    def model(self,question,pack,role):
        assert set(item['observation_ids'])=={o['id'] for o in resolved(pack)['observations']}
        return {'alternatives':[],'contradicting_observation_ids':[],
                'missing_checks':['independent confirmation']},{'role':role}
    monkeypatch.setattr('workbench.controller.Provider.generate',model)
    c.falsify(item['id'],{'model':'fixture'})
    assert c.store.list('falsifier_input',cid)


def test_previous_review_prose_is_not_represented_as_evidence(tmp_path):
    from workbench.review_context import serialize
    c,cid,e,t,_=setup(tmp_path);a=observation(c,cid,e,1);b=observation(c,cid,e,2)
    item=claim(c,cid,t,[a])
    item=c.store.update(item['id'],falsification={'alternatives':['previous unverified interpretation'],
        'contradicting_observation_ids':[b['id']],'missing_checks':[]})
    pack=build_pack(c,item)
    assert 'previous unverified interpretation' not in serialize(pack)
    assert b['id'] in pack['review_scope']['required_observation_ids']
    c.store.update(item['id'],falsification=None)
    with pytest.raises(InputBudgetError,match='Claim changed'):validate_scope(c,item,pack)
