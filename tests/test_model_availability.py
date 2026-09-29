import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from test_dossiers import setup
from workbench.provider import Provider,ModelServiceError
from workbench.models import ProviderConfig
from workbench import model_availability as availability

CONFIG={'base_url':'http://127.0.0.1:11434','model':'fixture'}


def test_digest_preflight_timeout_is_not_model_output_and_closes_client(monkeypatch):
    monkeypatch.setenv('FRONTIER_MODEL_DIGEST','a'*64)
    p=Provider(CONFIG);p.client.close();seen=[]
    def offline(request):
        seen.append(request.url.path)
        raise httpx.ConnectTimeout('timed out',request=request)
    p.client=httpx.Client(transport=httpx.MockTransport(offline))
    with pytest.raises(ModelServiceError) as caught:p.generate('q',{},role='judgment')
    assert seen==['/api/tags'] and p.client.is_closed
    assert caught.value.metadata['request_attempted'] is False
    assert caught.value.metadata['operation']=='/api/tags'


@pytest.mark.parametrize('status',[429,500,503])
def test_transient_status_is_service_failure(status,monkeypatch):
    monkeypatch.delenv('FRONTIER_MODEL_DIGEST',raising=False)
    p=Provider(CONFIG);p.client.close()
    p.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(status)))
    with pytest.raises(ModelServiceError):p.generate('q',{})
    assert p.client.is_closed


def test_wrong_digest_still_fails_closed(monkeypatch):
    monkeypatch.setenv('FRONTIER_MODEL_DIGEST','a'*64)
    p=Provider(CONFIG);p.client.close()
    p.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'models':[]})))
    with pytest.raises(ValueError,match='digest') as caught:p.generate('q',{})
    assert not isinstance(caught.value,ModelServiceError)
    assert p.client.is_closed


def test_outage_cannot_split_or_fail_unrelated_dossiers(tmp_path,monkeypatch):
    from workbench.dossiers import finish
    c,cid,e,t,o=setup(tmp_path)
    t=c.store.update(t['id'],review_policy='autonomous-v1')
    for typ in ('linux_authentication','linux_persistence'):
        c.store.add('observation',cid,evidence_id=e['id'],type=typ,timestamp=None,source_location='/log',fields={'path':'/log'})
    calls=[]
    def fail(*args,**kwargs):
        calls.append(1)
        raise ModelServiceError('unreachable',transport='ollama',operation='/api/tags')
    monkeypatch.setattr('workbench.dossiers.Provider.generate',fail)
    for attempt in range(3):
        finish(c,cid,e,t)
        for _ in range(8):finish(c,cid,e,t)
        assert len(calls)==attempt+1
        state=c.store.get(cid)['model_wait']
        if attempt<2:c.store.update(cid,model_wait={**state,'retry_at':'2000-01-01T00:00:00+00:00'})
    assert c.store.get(cid)['status']=='paused'
    assert len(c.store.list('dossier_batch',cid))==1
    assert c.store.list('dossier_batch',cid)[0]['attempts']==0
    assert not any(d['status']=='model_failed' for d in c.store.list('dossier',cid))
    assert not c.store.list('review_diagnostic',cid)
    assert len(c.store.list('review_input',cid))==3 # immutable reservations retained
    assert len([r for r in c.store.list('receipt',cid) if r.get('receipt_type')=='model_service_unavailable'])==3


def test_recovery_clears_wait_without_fabricating_findings(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    availability.defer(c.store,cid,t,ModelServiceError('offline',transport='ollama',operation='chat'))
    assert availability.waiting(c.store,cid)
    availability.recovered(c.store,cid)
    assert not availability.waiting(c.store,cid)
    assert not c.store.list('judgment',cid)


def test_two_connections_validate_routes_and_bind_runtime():
    from workbench.runtime_contract import binding
    cfg={**CONFIG,'secondary':{**CONFIG,'model':'second'},'role_routes':{'judgment':'secondary'}}
    assert ProviderConfig(**cfg).secondary.model=='second'
    assert binding('code',cfg)['fingerprint']!=binding('code',CONFIG)['fingerprint']
    with pytest.raises(ValueError):ProviderConfig(**CONFIG,role_routes={'judgment':'secondary'})
    with pytest.raises(ValueError):ProviderConfig(**cfg,third=CONFIG)
    with pytest.raises(ValueError):ProviderConfig(**CONFIG,role_routes={'unknown':'primary'})
    with pytest.raises(ValueError):ProviderConfig(**cfg,falsifier_model='third')


def test_secondary_does_not_inherit_primary_digest_relay_or_credential(monkeypatch):
    monkeypatch.setenv('FRONTIER_MODEL_DIGEST','a'*64)
    monkeypatch.setenv('MODEL_API_KEY','primary-secret-fixture')
    p=Provider({**CONFIG,'secondary':{**CONFIG,'base_url':'http://127.0.0.1:22434','model':'second'},'role_routes':{'judgment':'secondary'}})
    p.select_role('judgment')
    assert p.base=='http://127.0.0.1:22434'
    assert 'Authorization' not in p.client.headers
    p.client.close();seen=[]
    def response(request):
        seen.append(request.url.path)
        return httpx.Response(200,json={'done_reason':'stop','message':{'content':json.dumps({'summary':'ok','claims':[]})}})
    p.client=httpx.Client(transport=httpx.MockTransport(response))
    check=p.verify_model_identity('second')
    assert check['expected_digest']=='unverified' and seen==[]
    p.client.close()


def test_independent_transports_can_run_in_parallel_but_each_is_serial():
    from workbench.model_concurrency import lease
    barrier=threading.Barrier(2)
    def work(identity):
        with lease(identity):barrier.wait(timeout=3)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(work,x) for x in ('a','b')]
        for f in futures:f.result(timeout=4)
    entered=threading.Event();release=threading.Event();second=threading.Event()
    def first():
        with lease('same'):entered.set();assert release.wait(3)
    def later():
        with lease('same'):second.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(first);assert entered.wait(2)
        b=pool.submit(later);assert not second.wait(.05)
        release.set();a.result();b.result()


def test_secondary_public_endpoint_is_rejected_on_save(tmp_path):
    from fastapi.testclient import TestClient
    from workbench.api import create_app
    with TestClient(create_app(tmp_path/'data',tmp_path,start_worker=False)) as client:
        config={**CONFIG,'secondary':{**CONFIG,'base_url':'http://8.8.8.8:11434','trusted_lan':True}}
        r=client.put('/api/settings',json=config,headers={'X-Requested-With':'frontier'})
        assert r.status_code==400


def test_secondary_generation_uses_only_its_connection_and_model(monkeypatch):
    monkeypatch.delenv('FRONTIER_MODEL_DIGEST',raising=False)
    p=Provider({**CONFIG,'secondary':{**CONFIG,'model':'second','base_url':'http://127.0.0.1:22434'},'role_routes':{'analyst':'secondary'}})
    p.select_role('analyst');p.client.close();seen=[]
    def response(request):
        seen.append((str(request.url),json.loads(request.content)['model']))
        return httpx.Response(200,json={'done_reason':'stop','message':{'content':'{"summary":"ok","claims":[]}'}})
    p.client=httpx.Client(transport=httpx.MockTransport(response))
    _,receipt=p.generate('q',{})
    assert seen==[('http://127.0.0.1:22434/api/chat','second')]
    assert receipt['model_slot']=='secondary' and receipt['model']=='second'
