import hashlib
import json
import threading
from types import SimpleNamespace
import httpx
import pytest
from workbench.decision_runtime import (ResourceBroker,ResourceUnavailable,JevAnnotator,
    digest,inspect_position)
from workbench.store import Store


def response(letters='ABCD'):
    rows=[{'token':l,'bytes':[ord(l)],'logprob':-i-1.0} for i,l in enumerate(letters)]
    return {'done':True,'response':letters[0],'eval_count':1,'logprobs':[{**rows[0],'top_logprobs':rows}]}


def test_complete_position_only():
    assert inspect_position(response(),dict.fromkeys('ABCD'))['A']==-1
    for patch in ({'eval_count':2},{'done':False},{'thinking':'hidden'},{'response':' A'}):
        with pytest.raises(ValueError):inspect_position({**response(),**patch},dict.fromkeys('ABCD'))
    with pytest.raises(ValueError,match='incomplete'):inspect_position(response('ABC'),dict.fromkeys('ABCD'))


@pytest.mark.parametrize('value',[True,float('nan'),1.0])
def test_invalid_score(value):
    r=response();r['logprobs'][0]['logprob']=value
    with pytest.raises(ValueError):inspect_position(r,dict.fromkeys('ABCD'))


def test_unknown_lease_persists_without_ttl_or_process_probe(tmp_path):
    path=tmp_path/'broker.sqlite3'
    with ResourceBroker(path,'gpu').lease('first') as lease:lease.dispatched=True
    with pytest.raises(ResourceUnavailable,match='reconciliation'):
        with ResourceBroker(path,'gpu').lease('second'):pass


def test_only_actual_response_releases(tmp_path):
    broker=ResourceBroker(tmp_path/'broker.sqlite3','gpu')
    with broker.lease('one') as lease:
        lease.dispatched=True;lease.received({'delivery_state':'response_received'})
    with broker.lease('two') as lease:lease.not_sent('cancelled_before_dispatch')


def test_metadata_failure_does_not_claim_unknown_generation(tmp_path):
    broker=ResourceBroker(tmp_path/'broker.sqlite3','gpu')
    with pytest.raises(ValueError):
        with broker.lease('one'):raise ValueError('metadata_missing')
    with broker.lease('two') as lease:lease.not_sent('not_started')


def test_noncooperating_lease_blocked(tmp_path):
    broker=ResourceBroker(tmp_path/'broker.sqlite3','gpu')
    with broker.lease('one') as lease:
        with pytest.raises(ResourceUnavailable):
            with ResourceBroker(broker.path,'gpu').lease('two'):pass
        lease.not_sent('one_done')


class Client:
    def __init__(self,responder,**kwargs):self.responder=responder
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def get(self,url):return self.responder('GET',url,None)
    def post(self,url,json):return self.responder('POST',url,json)


def fixture(tmp_path,*,inference=None):
    store=Store(tmp_path/'case.sqlite3')
    case=store.add('case','',status='running',epoch_id='epoch')
    store.update(case['id'],case_id=case['id'])
    e=store.add('evidence',case['id'],connected=True)
    task=store.add('task',case['id'],evidence_id=e['id'],retry_generation=0)
    ob=store.add('observation',case['id'],evidence_id=e['id'],fields={'excerpt':'synthetic status=ready'})
    d=store.add('dossier',case['id'],task_id=task['id'],evidence_id=e['id'],generation=0,
        receipt_id='receipt',status='reviewed',title='synthetic record',observation_ids=[ob['id']],finding={})
    vocab={'tokenizer.ggml.tokens':list('ABCD')};template='pinned'
    calls=[]
    def responder(method,url,payload):
        calls.append((method,url,payload))
        if url.endswith('/api/tags'):value={'models':[{'name':'synthetic','digest':'manifest'}]}
        elif url.endswith('/api/show'):value={'model_info':vocab,'template':template}
        else:
            value=(inference(store,d) if inference else response());value['model']='synthetic'
        return httpx.Response(200,json=value,request=httpx.Request(method,url))
    controller=SimpleNamespace(store=store,stop=threading.Event())
    annotator=JevAnnotator(controller,base_url='http://127.0.0.1:11434',model='synthetic',
        manifest_digest='manifest',template_sha256=hashlib.sha256(template.encode()).hexdigest(),
        tokenizer_sha256=hashlib.sha256(json.dumps(vocab,sort_keys=True).encode()).hexdigest(),
        broker_path=str(tmp_path/'broker.sqlite3'),resource_group='synthetic-gpu',
        client_factory=lambda **kw:Client(responder,**kw),interval=1,limit=1)
    return store,case,d,annotator,calls


def test_installed_annotation_is_not_probability_or_policy(tmp_path):
    store,case,d,a,calls=fixture(tmp_path);a.tick()
    result=store.list('jev_annotation',case['id'])[0]
    assert result['status']=='annotated' and result['label']=='설정 단서'
    assert result['probabilities'] is None and result['policy_applied'] is False
    assert result['retained_input_independently_verified'] is False
    assert result['delivery_state']=='response_received'
    assert store.get(d['id'])['finding']=={}
    a.tick();assert len([x for x in calls if x[1].endswith('/api/generate')])==1


def test_missing_scores_never_use_generated_letter(tmp_path):
    store,case,d,a,calls=fixture(tmp_path,inference=lambda s,d:response('A'));a.tick()
    result=store.list('jev_annotation',case['id'])[0]
    assert result['status']=='unsupported' and result['probabilities'] is None
    assert result['delivery_state']=='response_received' and 'category' not in result


def test_correction_during_generation_retains_late_only(tmp_path):
    def corrected(store,d):store.update(d['id'],receipt_id='corrected');return response()
    store,case,d,a,calls=fixture(tmp_path,inference=corrected);a.tick()
    assert store.list('jev_annotation',case['id'])[0]['status']=='late'


def test_paused_case_has_zero_model_requests(tmp_path):
    store,case,d,a,calls=fixture(tmp_path);store.update(case['id'],status='paused');a.tick()
    assert calls==[]
