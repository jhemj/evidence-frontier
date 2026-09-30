"""Luna adapter callback contracts with fake infer only; never calls a model."""
import json
import os

import httpx
import pytest

from scripts import run_assisted_e2e as driver
from workbench.provider import Provider
from workbench.request_lifecycle import RequestAttempt
from workbench.store import Store


@pytest.fixture(autouse=True)
def isolate_driver_globals():
    environment=dict(os.environ)
    original_generate,original_response=Provider.generate,Provider.response
    yield
    os.environ.clear();os.environ.update(environment)
    Provider.generate,Provider.response=original_generate,original_response


def test_assisted_real_provider_callback_has_adapter_boundary_and_no_ollama(tmp_path,monkeypatch):
    monkeypatch.setenv('FRONTIER_MODEL_DIGEST','a'*64)
    calls=[]
    def fake_infer(messages,schema,root,*,emit=None):
        calls.append(1)
        assert callable(emit)
        emit('dispatch_attempted',delivery_state='unknown',process_started=True,
            request_attempted=None,dispatch_boundary='local_cli_adapter')
        emit('response_waiting',delivery_state='unknown',process_started=True,
            request_attempted=None,dispatch_boundary='local_cli_adapter')
        content=json.dumps({'alternatives':[],'contradicting_observation_ids':[],'missing_checks':[]})
        emit('response_received',delivery_state='response_received',request_attempted=True,
            usage={'input_tokens':17,'output_tokens':11},dispatch_boundary='codex_luna_response')
        return content,{'phase':'response_received','request_attempted':True,
            'delivery_state':'response_received','usage':{'input_tokens':17,'output_tokens':11}}
    monkeypatch.setattr(driver,'infer',fake_infer)
    driver.install_assisted_provider(tmp_path/'transport',luna_roles=('falsifier',))
    store=Store(tmp_path/'case.db');task=store.add('task','fixture',retry_generation=0)
    claim=store.add('claim','fixture',text='fixture',task_id=task['id'])
    attempt=RequestAttempt(store,'fixture',task,role='falsifier',owner_records=[claim])
    p=Provider({'base_url':'http://127.0.0.1:11434','model':'requested-production-fixture'})
    p.client.close();p.client=httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail('no Ollama fallback/probe')))
    output,receipt=p.generate('fixture',{'observations':[]},role='falsifier',attempt=attempt.id,emit=attempt.emit)
    attempt.accept([claim],scope='fixture_falsifier')
    rows=store.list('request_lifecycle','fixture')
    assert calls==[1] and receipt['model']=='gpt-6-luna' and output['contradicting_observation_ids']==[]
    checked=next(r for r in rows if r['phase']=='availability_verified')
    assert checked['metadata']['identity_verified'] is False  # no imaginary Ollama digest verification
    dispatched=next(r for r in rows if r['phase']=='dispatch_attempted')
    assert dispatched['metadata']['dispatch_boundary']=='local_cli_adapter'
    assert dispatched['metadata']['request_attempted'] is None and dispatched['delivery_state']=='unknown'
    assert not any('generat' in r['phase'] for r in rows)
    received=next(r for r in rows if r['phase']=='response_received')
    assert received['metadata']['usage']=={'input_tokens':17,'output_tokens':11}
    assert received['metadata']['request_attempted'] is True
    assert rows[-1]['phase']=='ended' and p.client.is_closed


def test_adapter_local_start_then_discovery_failure_refines_delivery_not_sent(tmp_path):
    from workbench.provider import ModelServiceError
    store=Store(tmp_path/'case.db');task=store.add('task','fixture',retry_generation=0)
    attempt=RequestAttempt(store,'fixture',task,role='falsifier')
    attempt.emit('dispatch_attempted',delivery_state='unknown',process_started=True,
        request_attempted=None,dispatch_boundary='local_cli_adapter')
    attempt.fail(ModelServiceError('fixture discovery',transport='codex-luna-test-only',
        operation='model-discovery',request_attempted=False,phase='discovery',delivery_state='not_sent'))
    rows=store.list('request_lifecycle','fixture')
    assert rows[-1]['delivery_state']=='not_sent'
    assert not {'response_received','accepted'} & {r['phase'] for r in rows}
    assert rows[-2]['metadata']['failure_phase']=='discovery'


def test_adapter_explicit_error_response_preserves_received_without_adoption(tmp_path):
    from workbench.provider import ModelServiceError
    store=Store(tmp_path/'case.db');task=store.add('task','fixture',retry_generation=0)
    attempt=RequestAttempt(store,'fixture',task,role='falsifier')
    attempt.emit('dispatch_attempted',delivery_state='unknown',process_started=True,
        request_attempted=None,dispatch_boundary='local_cli_adapter')
    attempt.fail(ModelServiceError('fixture server rejection',transport='codex-luna-test-only',
        operation='cli-authentication',request_attempted=True,phase='authentication',
        category='model_authentication',retryable=False,delivery_state='response_received'))
    rows=store.list('request_lifecycle','fixture')
    assert rows[-1]['delivery_state']=='response_received'
    assert rows[-2]['metadata']['failure_phase']=='authentication'
    assert rows[-2]['metadata']['request_attempted'] is True
    assert not any(row['accepted_refs'] for row in rows)
