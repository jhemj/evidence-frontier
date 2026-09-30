"""Mock-only transport/Controller lifecycle contracts; no external model."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import threading

import httpx
import pytest

from workbench.investigator import consult
from workbench.model_concurrency import lease
from workbench.provider import Provider, ModelOutputError, ModelServiceError
from workbench.request_lifecycle import RequestAttempt, safe_metadata
from workbench.store import Store

CONFIG = {'protocol':'ollama', 'base_url':'http://127.0.0.1:11434', 'model':'lifecycle-fixture'}


def attempt_fixture(tmp_path, *, generation=0, logical='work-1'):
    store=Store(tmp_path/'lifecycle.db')
    task=store.add('task','fixture-case',retry_generation=generation,evidence_id='fixture-evidence')
    owner=store.add('dossier','fixture-case',task_id=task['id'],generation=generation,
        title='fixture source',status='pending')
    pack={'observations':[{'id':'OBSERVATION-fixture','fields':{
        'excerpt':'private-evidence-marker', 'path':'/fixture/private-source'}}]}
    input_record=store.add('review_input','fixture-case',task_id=task['id'],generation=generation,
        pack=pack,input_sha256='a'*64)
    attempt=RequestAttempt(store,'fixture-case',task,role='analyst',input_record=input_record,
        owner_records=[owner],logical_work_id=logical)
    return store,task,owner,input_record,attempt,pack


def provider(handler):
    p=Provider(CONFIG)
    p.client.close()
    p.client=httpx.Client(transport=httpx.MockTransport(handler))
    return p


def good_response(request):
    return httpx.Response(200,json={'done_reason':'stop','message':{
        'content':json.dumps({'summary':'fixture answer','claims':[]}),
        'thinking':'private-thought-marker'}, 'prompt_eval_count':35,'eval_count':9})


def events(store):
    return store.list('request_lifecycle','fixture-case')


def test_nonstreaming_request_observes_dispatch_not_generation_and_no_text(tmp_path,monkeypatch):
    monkeypatch.delenv('FRONTIER_MODEL_DIGEST',raising=False)
    store,task,owner,input_record,attempt,pack=attempt_fixture(tmp_path)
    p=provider(good_response)
    output,receipt=p.generate('private-question-marker',pack,attempt=attempt.id,emit=attempt.emit)
    attempt.emit('validating',validation_stage='source_contract')
    attempt.accept([owner],scope='fixture_adoption')
    rows=events(store);phases=[r['phase'] for r in rows]
    assert phases.index('resource_queued') < phases.index('resource_acquired')
    assert phases.index('input_ready') < phases.index('dispatch_attempted') < phases.index('response_waiting')
    assert phases.index('response_waiting') < phases.index('response_received') < phases.index('validating')
    assert not any('generat' in phase for phase in phases)
    assert phases[-2:] == ['accepted','ended']
    assert [r['seq'] for r in rows] == list(range(1,len(rows)+1))
    assert len({r['event_id'] for r in rows})==len(rows)
    assert all(r['attempt_id']==attempt.id and r['input_record_id']==input_record['id'] for r in rows)
    ready=next(r for r in rows if r['phase']=='input_ready')['metadata']
    assert ready['input_manifest']['source_refs']==[{'kind':'observation','id':'OBSERVATION-fixture',
        'version':hashlib.sha256(json.dumps(pack['observations'][0],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()}]
    assert any(r['metadata'].get('usage',{}).get('eval_count')==9 for r in rows)
    serialized=json.dumps(rows)
    assert all(marker not in serialized for marker in (
        'private-evidence-marker','private-thought-marker','private-question-marker','/fixture/private-source'))
    assert receipt['request_attempt_id']==attempt.id and output['summary']=='fixture answer'
    assert p.client.is_closed and p._lifecycle_emit is None
    with pytest.raises(ValueError,match='불변'):store.update(rows[0]['id'],phase='accepted')
    with pytest.raises(ValueError,match='already ended'):attempt.emit('validating')


def test_preflight_failure_does_not_claim_dispatch_or_received_generation(tmp_path,monkeypatch):
    monkeypatch.setenv('FRONTIER_MODEL_DIGEST','b'*64)
    store,task,owner,input_record,attempt,pack=attempt_fixture(tmp_path)
    def offline(request):
        assert request.url.path=='/api/tags'
        raise httpx.ConnectTimeout('private-error-marker',request=request)
    p=provider(offline)
    with pytest.raises(ModelServiceError) as caught:
        p.generate('fixture',pack,attempt=attempt.id,emit=attempt.emit)
    attempt.fail(caught.value,outcome='service_unavailable')
    rows=events(store)
    assert 'availability_check' in [r['phase'] for r in rows]
    assert not {'dispatch_attempted','response_waiting','response_received','delivery_unknown'} & {r['phase'] for r in rows}
    assert all(r['delivery_state']=='not_sent' for r in rows)
    assert rows[-1]['metadata']['outcome']=='service_unavailable'
    assert 'private-error-marker' not in json.dumps(rows)


def test_transport_loss_after_dispatch_preserves_unknown_delivery(tmp_path,monkeypatch):
    monkeypatch.delenv('FRONTIER_MODEL_DIGEST',raising=False)
    store,task,owner,input_record,attempt,pack=attempt_fixture(tmp_path)
    def timeout(request):
        assert request.url.path=='/api/chat'
        raise httpx.ReadTimeout('fixture timeout',request=request)
    p=provider(timeout)
    with pytest.raises(ModelServiceError) as caught:
        p.generate('fixture',pack,attempt=attempt.id,emit=attempt.emit)
    attempt.fail(caught.value)
    rows=events(store)
    assert 'dispatch_attempted' in {r['phase'] for r in rows}
    assert 'delivery_unknown' in {r['phase'] for r in rows}
    assert 'response_received' not in {r['phase'] for r in rows}
    assert rows[-1]['delivery_state']=='unknown'
    assert not rows[-1]['accepted_refs']


def test_received_invalid_output_is_not_delivery_unknown_or_adoption(tmp_path,monkeypatch):
    monkeypatch.delenv('FRONTIER_MODEL_DIGEST',raising=False)
    store,task,owner,input_record,attempt,pack=attempt_fixture(tmp_path)
    p=provider(lambda r:httpx.Response(200,json={'done_reason':'stop','message':{'content':'{}'}}))
    with pytest.raises(ModelOutputError) as caught:
        p.generate('fixture',pack,attempt=attempt.id,emit=attempt.emit)
    attempt.fail(caught.value)
    rows=events(store)
    assert rows[-1]['delivery_state']=='response_received'
    assert 'validating' in {r['phase'] for r in rows}
    assert not {'accepted','partial_accepted','delivery_unknown'} & {r['phase'] for r in rows}


def test_controller_adoption_exception_closes_attempt_after_rollback(tmp_path):
    store,task,owner,input_record,attempt,pack=attempt_fixture(tmp_path)
    with pytest.raises(RuntimeError,match='fixture adoption interrupted'):
        with attempt.finalizing(),store.tx():
            store.update(owner['id'],status='reviewed')
            raise RuntimeError('fixture adoption interrupted')
    assert store.get(owner['id'])['status']=='pending'
    rows=events(store)
    assert [row['phase'] for row in rows][-2:]==['failed','ended']
    assert rows[-1]['metadata']['outcome']=='controller_exception'
    assert not any(row['accepted_refs'] for row in rows)


def test_resource_queue_is_real_and_dispatch_waits_for_lease(tmp_path,monkeypatch):
    monkeypatch.delenv('FRONTIER_MODEL_DIGEST',raising=False)
    store,task,owner,input_record,attempt,pack=attempt_fixture(tmp_path)
    queued=threading.Event()
    def emit(phase,**metadata):
        attempt.emit(phase,**metadata)
        if phase=='resource_queued':queued.set()
    p=provider(good_response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with lease(p.transport_identity()):
            future=pool.submit(p.generate,'fixture',pack,attempt=attempt.id,emit=emit)
            assert queued.wait(timeout=2)
            assert not {'resource_acquired','dispatch_attempted'} & {r['phase'] for r in events(store)}
        future.result(timeout=3)
    attempt.finish('fixture_completed')


def test_legacy_callable_is_invoked_once_without_manufactured_transport(tmp_path):
    store,task,owner,input_record,attempt,pack=attempt_fixture(tmp_path)
    calls=[]
    def legacy(config,question,pack,role,provider_factory):
        calls.append(1)
        return {},{}
    attempt.consult(legacy,{},'fixture',pack,role='judgment',provider_factory=Provider)
    assert calls==[1]
    assert [r['phase'] for r in events(store)]==['input_registered']
    def transmitted(config,question,pack,**kwargs):
        calls.append(1)
        raise TypeError('fixture after potential transmission')
    with pytest.raises(TypeError):attempt.consult(transmitted,{},'fixture',pack)
    assert calls==[1,1]  # no TypeError fallback retry


def test_native_callback_binding_does_not_require_new_generate_mock_keywords(tmp_path):
    store,task,owner,input_record,attempt,pack=attempt_fixture(tmp_path)
    class LegacyProvider:
        def __init__(self,config):pass
        def generate(self,question,pack,role):return {'tool_calls':[]},{}
    attempt.consult(consult,CONFIG,'fixture',pack,role='investigator',provider_factory=LegacyProvider)
    assert [r['phase'] for r in events(store)]==['input_registered']


def test_partial_adoption_and_retry_keep_exact_owners_and_attempts(tmp_path):
    store,task,owner,input_record,attempt,pack=attempt_fixture(tmp_path)
    bad=store.add('dossier','fixture-case',task_id=task['id'],generation=0,status='pending')
    attempt.accept([owner],rejected_records=[bad],phase='partial_accepted',scope='validated_subset')
    adopted=events(store)[-2]
    assert [r['id'] for r in adopted['accepted_refs']]==[owner['id']]
    assert [r['id'] for r in adopted['rejected_refs']]==[bad['id']]
    retry=RequestAttempt(store,'fixture-case',task,role='analyst',input_record=input_record,
        owner_records=[owner],logical_work_id='work-1')
    assert retry.id!=attempt.id and retry.retry_of_attempt_id==attempt.id
    other=RequestAttempt(store,'fixture-case',task,role='analyst',input_record=input_record,
        owner_records=[owner],logical_work_id='different-work')
    assert other.retry_of_attempt_id is None
    regenerated={**task,'retry_generation':1}
    fresh=RequestAttempt(store,'fixture-case',regenerated,role='analyst',input_record=input_record,
        owner_records=[owner],logical_work_id='work-1')
    assert fresh.retry_of_attempt_id is None


def test_metadata_allowlist_excludes_raw_output_urls_auth_and_arbitrary_keys():
    metadata=safe_metadata({'raw_output':'private','error':'private','url':'https://fixture.invalid',
        'Authorization':'Bearer private','usage':{'prompt_tokens':7,'private':'private',
            'prompt_tokens_details':{'cached_tokens':3,'credential':'private'}},
        'prompt_budget':{'estimated_input_tokens':10,'secret-key':0},
        'input_manifest':{'source_refs':[{'kind':'observation','id':'OBSERVATION-fixture','version':'a'*64,
            'excerpt':'private'}],'owner_ids':['DOSSIER-fixture'],'question':'private'}})
    assert metadata['usage']=={'prompt_tokens':7,'cached_tokens':3}
    assert metadata['prompt_budget']=={'estimated_input_tokens':10}
    assert 'private' not in json.dumps(metadata) and 'secret-key' not in json.dumps(metadata)


def test_controller_final_and_partial_adoption_emit_exact_dossier_refs(tmp_path,monkeypatch):
    from test_dossiers import setup
    from workbench.dossiers import finish
    c,cid,e,t,o=setup(tmp_path)
    c.store.add('observation',cid,evidence_id=e['id'],type='linux_detection',timestamp=None,
        source_location='fixture:/second',fields={'rule_id':'second','title':'second fixture','path':'/second'})
    calls=[]
    def model(self,question,pack,role):
        calls.append(pack)
        findings=[{'dossier_id':d['id'],'title':'fixture source','judgment':'미확인','reason':'bounded',
            'observation_ids':d['observation_ids'][:1],'alternatives':[],'remaining_checks':[]}
            for d in pack['required_dossiers']]
        if len(calls)==2:findings[-1]['observation_ids']=['OBSERVATION-not-present']
        return {'summary':'fixture','findings':findings,'next_checks':[],'check_assessments':[]},{}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    finish(c,cid,e,t)
    finish(c,cid,e,t)
    rows=c.store.list('request_lifecycle',cid)
    partial=next(r for r in rows if r['phase']=='partial_accepted')
    assert {r['id'] for r in partial['rejected_refs']}=={calls[1]['required_dossiers'][-1]['id']}
    assert {r['id'] for r in partial['accepted_refs'] if r['kind']=='dossier'}=={calls[1]['required_dossiers'][0]['id']}
    assert not {'dispatch_attempted','response_received'} & {r['phase'] for r in rows}
    assert all(rows_for_attempt[-1]['phase']=='ended' for aid in {r['attempt_id'] for r in rows}
        if (rows_for_attempt:=[r for r in rows if r['attempt_id']==aid]))
