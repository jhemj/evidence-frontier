"""Independent installation-lane scope/delivery gates; synthetic transport only."""
import hashlib
import json
import threading
from types import SimpleNamespace

import httpx
import pytest

from workbench.decision_runtime import JevAnnotator, ResourceBroker, ResourceUnavailable
from workbench.provider import Provider, ModelServiceError
from workbench.store import Store, now


def fixture(tmp_path, *, mutate=None, moment='generate'):
    store=Store(tmp_path/'case.sqlite3')
    case=store.add('case','',status='running')
    epoch=store.add('epoch',case['id'],status='running',started_at=now())
    case=store.update(case['id'],case_id=case['id'],epoch_id=epoch['id'])
    evidence=store.add('evidence',case['id'],connected=True,path='fixture.img',signature='fixture:1')
    task=store.add('task',case['id'],evidence_id=evidence['id'],retry_generation=0,status='queued')
    ob=store.add('observation',case['id'],evidence_id=evidence['id'],type='fixture',
                 fields={'excerpt':'synthetic configuration record only'})
    dossier=store.add('dossier',case['id'],task_id=task['id'],evidence_id=evidence['id'],generation=0,
        status='reviewed',title='synthetic configuration',observation_ids=[ob['id']],
        finding={'literal_statement':'synthetic record contains a configured check'})
    receipt=store.add('receipt',case['id'],task_id=task['id'],evidence_id=evidence['id'],generation=0,
        receipt_type='dossier_model',output={'findings':[{'dossier_id':dossier['id']}]})
    dossier=store.update(dossier['id'],receipt_id=receipt['id'])
    data=SimpleNamespace(store=store,case=case,epoch=epoch,evidence=evidence,task=task,
                         observation=ob,dossier=dossier,receipt=receipt,calls=[])
    vocab={'tokenizer.ggml.tokens':list('ABCD')};template='pinned'
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,url):return self.send('GET',url,None)
        def post(self,url,json):return self.send('POST',url,json)
        def send(self,method,url,payload):
            assert not store.db.in_transaction
            assert store.lock.acquire(blocking=False)
            store.lock.release()
            data.calls.append((method,url,payload))
            if url.endswith('/api/tags'):
                if mutate and moment=='metadata':mutate(data)
                value={'models':[{'name':'synthetic','digest':'manifest'}]}
            elif url.endswith('/api/show'):
                value={'template':template,'model_info':vocab}
            else:
                if mutate and moment=='generate':mutate(data)
                rows=[{'token':letter,'bytes':[ord(letter)],'logprob':-i-1.}
                      for i,letter in enumerate('ABCD')]
                value={'model':'synthetic','done':True,'response':'A','eval_count':1,
                       'logprobs':[{**rows[0],'top_logprobs':rows}]}
            return httpx.Response(200,json=value,request=httpx.Request(method,url))
    controller=SimpleNamespace(store=store,stop=threading.Event())
    data.annotator=JevAnnotator(controller,base_url='http://127.0.0.1:11434',model='synthetic',
        manifest_digest='manifest',template_sha256=hashlib.sha256(template.encode()).hexdigest(),
        tokenizer_sha256=hashlib.sha256(json.dumps(vocab,sort_keys=True).encode()).hexdigest(),
        broker_path=tmp_path/'broker.sqlite3',resource_group='synthetic-gpu',client_factory=Client,
        limit=1,interval=1)
    return data


def change(f,kind):
    if kind=='title':f.store.update(f.dossier['id'],title='corrected title')
    elif kind=='sources':
        other=f.store.add('observation',f.case['id'],evidence_id=f.evidence['id'],type='fixture',fields={'excerpt':'changed source'})
        f.store.update(f.dossier['id'],observation_ids=[other['id']])
    elif kind=='generation':f.store.update(f.task['id'],retry_generation=1)
    elif kind=='superseded':f.store.update(f.task['id'],superseded=True)
    elif kind=='disconnected':f.store.update(f.evidence['id'],connected=False)
    elif kind=='signature':f.store.update(f.evidence['id'],signature='changed signature')
    elif kind=='epoch':
        epoch=f.store.add('epoch',f.case['id'],status='running',started_at=now())
        f.store.update(f.case['id'],epoch_id=epoch['id'])
    else:raise AssertionError(kind)


@pytest.mark.parametrize('kind',['title','sources','generation','superseded','disconnected','signature','epoch'])
def test_generation_advisory_must_remain_late_when_exact_source_scope_changes(tmp_path,kind):
    f=fixture(tmp_path,mutate=lambda data:change(data,kind))
    try:
        f.annotator.tick()
        annotation=f.store.list('jev_annotation',f.case['id'])[0]
        assert annotation['status']!='annotated', 'stale input cannot be shown as current classification'
        assert annotation['probabilities'] is None and annotation['policy_applied'] is False
    finally:f.store.db.close()


@pytest.mark.parametrize('kind',['title','sources','generation','superseded','disconnected','signature','epoch'])
def test_changed_scope_before_dispatch_produces_zero_generation_requests(tmp_path,kind):
    f=fixture(tmp_path,mutate=lambda data:change(data,kind),moment='metadata')
    try:
        f.annotator.tick()
        assert not [call for call in f.calls if call[1].endswith('/api/generate')]
        annotation=f.store.list('jev_annotation',f.case['id'])[0]
        assert annotation.get('delivery_state','not_sent')=='not_sent'
        assert annotation['status']!='annotated'
    finally:f.store.db.close()


def test_shared_broker_blocked_request_is_not_marked_as_dispatched(tmp_path,monkeypatch):
    path=tmp_path/'broker.sqlite3'
    with ResourceBroker(path,'synthetic-gpu').lease('unknown') as lease:
        lease.dispatched=True
    monkeypatch.setenv('FRONTIER_RESOURCE_BROKER',str(path))
    monkeypatch.setenv('FRONTIER_RESOURCE_GROUP','synthetic-gpu')
    events=[]
    provider=object.__new__(Provider)
    provider.config={'protocol':'ollama'}
    provider.base='http://127.0.0.1:11434'
    provider._request_attempted=False
    provider._lifecycle_emit=lambda phase,**kwargs:events.append((phase,kwargs))
    provider.client=SimpleNamespace(post=lambda *a,**k:pytest.fail('unknown lease cannot dispatch'))
    with pytest.raises(ModelServiceError) as error:
        provider.response('/api/chat',{'model':'synthetic','messages':[]})
    assert error.value.metadata['delivery_state']=='not_sent'
    assert error.value.metadata['request_attempted'] is False
    assert error.value.metadata['phase']=='resource_wait'
    assert provider._request_attempted is False
    assert not [phase for phase,_ in events if phase in {'dispatch_attempted','response_waiting'}]
