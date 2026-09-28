import json
from unittest.mock import patch
import httpx
import pytest
from workbench.provider import Provider
from workbench.store import Store
from workbench.controller import Controller,ROOT

CONFIG={'protocol':'ollama','base_url':'http://127.0.0.1:11434','model':'test','falsifier_model':'','trusted_lan':False}

def fake_provider(body):
    provider=Provider(CONFIG)
    provider.client.close()
    provider.client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json=body)))
    return provider

def test_structured_model_output_and_receipt():
    provider=fake_provider({'done_reason':'stop','message':{'content':json.dumps({'summary':'Bounded answer','claims':[]})},'eval_count':12,'prompt_eval_count':100})
    answer,receipt=provider.generate('Question',{'observations':[]})
    assert answer['summary']=='Bounded answer'
    assert receipt['usage']['eval_count']==12

def test_truncation_is_not_a_valid_answer():
    provider=fake_provider({'done_reason':'length','message':{'content':'{}'}})
    with pytest.raises(ValueError,match='잘렸'):provider.generate('Question',{})

def test_unknown_model_fields_fail_schema():
    provider=fake_provider({'message':{'content':json.dumps({'summary':'Answer','claims':[],'run_shell':'malicious instruction'})}})
    with pytest.raises(ValueError):provider.generate('Question',{})

def test_hallucinated_claim_ids_and_absence_never_enter_ledger(tmp_path):
    c=Controller(Store(tmp_path/'db.sqlite'),ROOT/'examples')
    case=c.create('Model gate','','triage');c.register(case['id'],'activity.ndjson');c.start(case['id'])
    while c.step(case['id']):pass
    observation=c.store.list('observation',case['id'])[0]
    output={'summary':'Candidate interpretation','claims':[{'text':'Fake reference','observation_ids':['OBS-nonexistent'],'claim_type':'interpretation','alternatives':[],'uncertainty':''},{'text':'Nothing happened','observation_ids':[observation['id']],'claim_type':'absence','alternatives':[],'uncertainty':''}]}
    with patch('workbench.controller.Provider.generate',return_value=(output,{'model':'fixture','role':'analyst'})):
        c.analyze(case['id'],'Question',CONFIG)
    assert not c.store.list('claim',case['id'])
    assert len([a for a in c.store.list('audit',case['id']) if a['action']=='claim_rejected_by_gate'])==2


def test_model_timeout_is_bounded_and_failure_remains_visible(tmp_path,monkeypatch):
    monkeypatch.setenv('MODEL_TIMEOUT','300')
    provider=Provider(CONFIG)
    assert provider.client.timeout.read==300 and provider.client.timeout.connect==10
    provider.client.close()
    c=Controller(Store(tmp_path/'db.sqlite'),ROOT/'examples')
    case=c.create('Timeout','','triage');c.register(case['id'],'activity.ndjson');c.start(case['id'])
    while c.step(case['id']):pass
    with patch('workbench.controller.Provider.generate',side_effect=httpx.ReadTimeout('timed out')):
        with pytest.raises(httpx.ReadTimeout):c.analyze(case['id'],'Question',CONFIG)
    assert c.store.list('message',case['id'])[-1]['mode']=='error'
    assert c.store.list('receipt',case['id'])[-1]['receipt_type']=='model_error'
    assert not c.model_lock.locked()
