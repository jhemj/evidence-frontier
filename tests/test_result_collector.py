"""No real worker, model, incident, or service calls in collector acceptance."""
import copy
import json
import threading
import time
from types import SimpleNamespace

import pytest

from workbench.result_collector import (
    ResultCollector, STATUS_VERSION, digest, request_body, request_digest,
)
from workbench.store import Store, now


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'case.sqlite3')
    binding = {'fingerprint': 'c' * 64, 'worker_source': 'b' * 64}
    case = store.add('case', '', status='running', target_os='linux', runtime_binding=binding)
    case = store.update(case['id'], case_id=case['id'])
    epoch = store.add('epoch', case['id'], status='running', started_at=now())
    case = store.update(case['id'], epoch_id=epoch['id'])
    evidence = store.add('evidence', case['id'], path='fixture.img', signature='fixture:1', connected=True)
    cell = store.add('coverage', case['id'], evidence_id=evidence['id'], status='running')
    task = store.add('task', case['id'], evidence_id=evidence['id'], cell_id=cell['id'],
                     status='queued', action='linux_investigate', retry_generation=0, runtime_binding=binding)
    source_run = 'RUN-' + 'd' * 32
    source = store.add('receipt', case['id'], evidence_id=evidence['id'], task_id=task['id'],
                       receipt_type='worker', result={'run_id': source_run})
    job = store.add('investigation_job', case['id'], task_id=task['id'], evidence_id=evidence['id'],
                    generation=0, source_run=source_run, fingerprint='a' * 64, status='submitted',
                    dispatched_at=now(), request={'tool': 'search', 'query': 'fixture',
                                                 'reason': 'fixture-only search scope'}, test_intent_ids=[])
    controller = SimpleNamespace(store=store, wake=threading.Event(), stop=threading.Event(),
                                 model_lock=threading.Lock(), runtime_binding=lambda: copy.deepcopy(binding))
    value = SimpleNamespace(store=store, case=case, epoch=epoch, evidence=evidence, task=task,
                            job=job, source=source, binding=binding, controller=controller,
                            path=tmp_path / 'case.sqlite3')
    yield value
    controller.stop.set()
    store.db.close()


def status(f, *, state='succeeded', events=True):
    result = {'status': 'covered', 'complete': True, 'observations':
              [{'category': 'fixture', 'source': 'fixture', 'summary': 'fixture record', 'fields': {}}] if events else []}
    reply = {'version': STATUS_VERSION, 'id': f.job['fingerprint'], 'status': state,
             'attempts': 1, 'updated_at': now(), 'error': None,
             'request_sha256': request_digest(request_body(f.job, f.evidence, f.case)),
             'source_run': f.job['source_run'], 'runtime_code': f.binding['worker_source'],
             'attempt_id': 'e' * 32}
    if state == 'succeeded':
        reply.update(result=result, result_sha256=digest(result))
    if state == 'failed':
        reply['error'] = 'fixture terminal worker failure'
    reply['status_sha256'] = digest(reply)
    return reply


def rehash(reply):
    reply['status_sha256'] = digest({k: v for k, v in reply.items() if k != 'status_sha256'})
    return reply


def collector(f, reply=None, **kwargs):
    calls = []
    def fetch(method, path, **params):
        calls.append((method, path, params))
        return copy.deepcopy(reply if reply is not None else status(f))
    c = ResultCollector(f.controller, enabled=True, fetch=fetch, wallclock_budget=.08, **kwargs)
    return c, calls


def run_until(c, predicate, timeout=1.):
    end = time.monotonic() + timeout
    while not predicate() and time.monotonic() < end:
        c.collect_once()
    assert predicate()


def collection_receipts(f):
    return [r for r in f.store.list('receipt', f.case['id'])
            if r.get('receipt_type') == 'worker_result_collection']


def test_default_disabled_has_no_reads_writes_threads_or_calls(setup):
    f = setup
    c = ResultCollector(f.controller, fetch=lambda *a, **k: pytest.fail('must not fetch'))
    revision = f.store.ui_revision(f.case['id'])
    c.start()
    assert c.thread is None and c.collect_once()['started'] == 0
    assert f.store.ui_revision(f.case['id']) == revision
    assert not collection_receipts(f)


def test_success_collected_once_but_not_semantically_evaluated(setup):
    f = setup
    c, calls = collector(f)
    counts = c.collect_once()
    job = f.store.get(f.job['id'])
    assert counts['ingested'] == 1 and job['status'] == 'ingested'
    assert len(job['observation_ids']) == 1
    assert len(f.store.list('lineage', f.case['id'])) == 1
    assert not f.store.list('judgment') and not f.store.list('claim')
    receipt = collection_receipts(f)[0]
    assert receipt['evaluation_status'] == 'unassessed'
    assert receipt['scope']['request_sha256'] == status(f)['request_sha256']
    assert receipt['worker_status']['result_sha256'] == digest(receipt['result'])
    assert receipt['timing_basis'] == 'local_monotonic_poll_only_no_cross_host_latency'
    assert receipt['first_terminal_observed_at'] and job['ingested_at']
    assert receipt['disposition'] == 'ingested'
    c.collect_once()
    assert len(collection_receipts(f)) == 1
    assert all(method == 'GET' and path == '/jobs/' + f.job['fingerprint'] for method, path, _ in calls)


@pytest.mark.parametrize('field,value,reason', [
    ('id', 'f' * 64, 'worker_request_mismatch'),
    ('request_sha256', 'f' * 64, 'worker_request_mismatch'),
    ('source_run', 'RUN-' + 'f' * 32, 'worker_source_run_mismatch'),
    ('runtime_code', 'f' * 64, 'worker_runtime_unverified'),
    ('attempt_id', None, 'worker_attempt_unverified'),
    ('attempts', 0, 'worker_attempt_unverified'),
    ('attempts', True, 'worker_attempt_unverified'),
    ('result_sha256', 'f' * 64, 'worker_result_hash_mismatch'),
    ('version', 'worker-job-status-1', 'worker_status_provenance_unavailable'),
])
def test_exact_terminal_proof_required(setup, field, value, reason):
    f = setup
    reply = status(f)
    reply[field] = value
    rehash(reply)
    c, _ = collector(f, reply)
    assert c.collect_once()['quarantined'] == 1
    receipt = collection_receipts(f)[0]
    assert receipt['failure_code'] == reason
    assert f.store.get(f.job['id'])['status'] == 'submitted'
    assert not f.store.list('observation') and not f.store.list('lineage')


def test_result_changed_under_unchanged_status_and_result_hash_is_quarantined(setup):
    f = setup
    reply = status(f)
    reply['result']['complete'] = False
    c, _ = collector(f, reply)
    assert c.collect_once()['quarantined'] == 1
    assert collection_receipts(f)[0]['failure_code'] == 'worker_status_hash_mismatch'


@pytest.mark.parametrize('change', ['foreign_case', 'foreign_evidence', 'fake_source', 'fake_job_key', 'undispatched'])
def test_untrusted_or_unissued_scope_is_not_fetched(setup, change):
    f = setup
    if change == 'foreign_case':
        other = f.store.add('case', '', status='running')
        f.store.update(f.task['id'], case_id=other['id'])
    elif change == 'foreign_evidence':
        f.store.update(f.evidence['id'], case_id='CASE-other')
    elif change == 'fake_source':
        f.store.update(f.job['id'], source_run='RUN-' + 'f' * 32)
    elif change == 'fake_job_key':
        f.store.update(f.job['id'], fingerprint='https://untrusted.invalid/command')
    else:
        f.store.update(f.job['id'], dispatched_at=None)
    c, calls = collector(f)
    c.collect_once()
    assert not calls and not collection_receipts(f) and not f.store.list('observation')


@pytest.mark.parametrize('change', ['paused', 'ended', 'cancelled', 'generation', 'source', 'disconnected', 'binding', 'new_epoch'])
def test_late_or_stale_result_retained_never_current_adoption(setup, change):
    f = setup
    reply = status(f)
    def fetch(*args, **kwargs):
        if change == 'paused':
            f.store.update(f.case['id'], status='paused')
        elif change == 'ended':
            f.store.update(f.case['id'], status='resource_limit')
            f.store.update(f.epoch['id'], status='resource_limit')
        elif change == 'cancelled':
            f.store.update(f.job['id'], status='cancelled')
        elif change == 'generation':
            f.store.update(f.task['id'], retry_generation=1)
        elif change == 'source':
            f.store.update(f.job['id'], source_run='RUN-' + 'f' * 32)
        elif change == 'disconnected':
            f.store.update(f.evidence['id'], connected=False)
        elif change == 'binding':
            f.controller.runtime_binding = lambda: {'fingerprint': 'f' * 64}
        else:
            new = f.store.add('epoch', f.case['id'], status='running', started_at=now())
            f.store.update(f.case['id'], epoch_id=new['id'])
        return reply
    c = ResultCollector(f.controller, enabled=True, fetch=fetch, wallclock_budget=.08)
    assert c.collect_once()['late_preserved'] == 1
    assert collection_receipts(f)[0]['result'] == reply['result']
    assert f.store.get(f.job['id'])['status'] != 'ingested'
    assert not f.store.list('observation') and not f.store.list('test_intent')
    f.store.update(f.case['id'], status='running', epoch_id=f.epoch['id'])
    f.store.update(f.task['id'], status='queued', retry_generation=0)
    replacement, calls = collector(f)
    replacement.collect_once()
    assert not calls and not f.store.list('observation')


def test_missing_dispatch_epoch_cannot_be_invented(setup):
    f = setup
    f.store.update(f.job['id'], dispatched_at='unknown')
    c, _ = collector(f)
    assert c.collect_once()['late_preserved'] == 1
    assert not f.store.list('observation')


def test_terminal_failure_is_raw_availability_not_refutation(setup):
    f = setup
    c, _ = collector(f, status(f, state='failed'))
    assert c.collect_once()['ingested'] == 1
    job = f.store.get(f.job['id'])
    assert job['result_status'] == 'failed' and job['result_scope']['complete'] is False
    assert job['result_scope']['failure_code'] == 'worker_failed'
    assert not job['observation_ids'] and not f.store.list('judgment')
    assert collection_receipts(f)[0]['evaluation_status'] == 'unassessed'


def test_execution_unknown_is_reconciliation_without_rerun(setup):
    f = setup
    c, calls = collector(f, status(f, state='execution_unknown'))
    assert c.collect_once()['reconciliation_required'] == 1
    assert not f.store.list('observation')
    assert f.store.get(f.job['id'])['status'] == 'submitted'
    c.collect_once()
    assert len(calls) == 1 and calls[0][0] == 'GET'


def test_running_attempt_is_pinned_and_new_attempt_not_silently_adopted(setup):
    f = setup
    replies = [status(f, state='running'), rehash({**status(f), 'attempt_id': 'f' * 32, 'attempts': 2})]
    c = ResultCollector(f.controller, enabled=True, fetch=lambda *a, **k: replies.pop(0), wallclock_budget=.08)
    c.collect_once()
    assert f.store.get(f.job['id'])['collector_worker_attempt_id'] == 'e' * 32
    run_until(c, lambda: bool(collection_receipts(f)))
    assert collection_receipts(f)[0]['failure_code'] == 'worker_attempt_changed'
    assert not f.store.list('observation')


def test_two_collectors_different_db_connections_have_one_atomic_ingest(setup):
    f = setup
    second_store = Store(f.path)
    second = SimpleNamespace(**{**vars(f.controller), 'store': second_store})
    barrier = threading.Barrier(2)
    reply = status(f)
    def fetch(*args, **kwargs):
        barrier.wait(1.)
        return reply
    one = ResultCollector(f.controller, enabled=True, fetch=fetch, wallclock_budget=.2)
    two = ResultCollector(second, enabled=True, fetch=fetch, wallclock_budget=.2)
    threads = [threading.Thread(target=c.collect_once) for c in (one, two)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(1.)
    try:
        assert not any(thread.is_alive() for thread in threads)
        assert len(collection_receipts(f)) == 1
        assert len(f.store.list('observation')) == 1 and len(f.store.list('lineage')) == 1
        assert len([r for r in f.store.list('receipt') if r.get('receipt_type') == 'investigation_tool']) == 1
    finally:
        second_store.db.close()


def test_restart_does_not_republish_same_physical_result(setup):
    f = setup
    first, _ = collector(f)
    first.collect_once()
    first.close()
    second, calls = collector(f)
    second.collect_once()
    assert not calls and len(collection_receipts(f)) == 1
    assert len(f.store.list('observation')) == 1


def test_io_never_holds_store_transaction_or_model_lock(setup):
    f = setup
    entered = threading.Event()
    def fetch(*args, **kwargs):
        assert not f.store.db.in_transaction
        assert f.store.lock.acquire(blocking=False)
        f.store.lock.release()
        independent = Store(f.path)
        try:
            independent.db.execute('PRAGMA busy_timeout=50')
            with independent.tx():
                independent.add('audit', f.case['id'], event='fixture concurrent writer')
        finally:
            independent.db.close()
        entered.set()
        return status(f)
    f.controller.model_lock.acquire()
    try:
        c = ResultCollector(f.controller, enabled=True, fetch=fetch, wallclock_budget=.2)
        assert c.collect_once()['ingested'] == 1
        assert entered.is_set() and f.controller.model_lock.locked()
    finally:
        f.controller.model_lock.release()


def test_long_fake_provider_does_not_delay_already_completed_worker_collection(setup):
    f = setup
    provider_active = threading.Event()
    release_provider = threading.Event()
    def provider():
        with f.controller.model_lock:
            provider_active.set()
            release_provider.wait(1.)
    provider_thread = threading.Thread(target=provider)
    provider_thread.start()
    assert provider_active.wait(.5)
    c, _ = collector(f, interval=.05)
    c.start()
    try:
        end = time.monotonic() + .8
        while f.store.get(f.job['id'])['status'] != 'ingested' and time.monotonic() < end:
            time.sleep(.005)
        assert f.store.get(f.job['id'])['status'] == 'ingested'
        assert provider_thread.is_alive() and f.controller.model_lock.locked()
    finally:
        c.close()
        release_provider.set()
        provider_thread.join(.5)


def test_ignoring_http_timeout_is_wallclock_bounded_and_not_duplicated(setup):
    f = setup
    release = threading.Event()
    calls = []
    def fetch(*args, **kwargs):
        calls.append(args)
        release.wait(1.)
        return status(f)
    c = ResultCollector(f.controller, enabled=True, fetch=fetch, wallclock_budget=.025, max_inflight=1)
    started = time.monotonic()
    assert c.collect_once()['inflight'] == 1
    assert time.monotonic() - started < .3
    c.collect_once()
    assert len(calls) == 1
    release.set()
    run_until(c, lambda: f.store.get(f.job['id'])['status'] == 'ingested')


def test_transaction_failure_can_retry_durable_output_without_partial_publish(setup, monkeypatch):
    f = setup
    import workbench.investigation as investigation
    original = investigation.store_tool_result
    monkeypatch.setattr(investigation, 'store_tool_result', lambda *a, **k: (_ for _ in ()).throw(ValueError('fixture crash')))
    c, calls = collector(f)
    with pytest.raises(ValueError, match='fixture crash'):
        c.collect_once()
    assert not collection_receipts(f) and not f.store.list('observation')
    assert not f.store.get(f.job['id']).get('collector_terminal_receipt_id')
    monkeypatch.setattr(investigation, 'store_tool_result', original)
    run_until(c, lambda: f.store.get(f.job['id'])['status'] == 'ingested')
    assert len(calls) == 2 and len(collection_receipts(f)) == 1


def test_opt_in_legacy_and_collector_share_same_boundary(setup):
    f = setup
    c, _ = collector(f)
    reply = status(f)
    assert c.ingest_status(f.job, reply) == 'ingested'
    assert c.ingest_status(f.job, reply) == 'duplicate'
    c.collect_once()
    assert len(f.store.list('observation')) == 1
    assert len(collection_receipts(f)) == 1


def test_legacy_poll_reply_cannot_bypass_quarantine(setup):
    f = setup
    c, _ = collector(f)
    bad = rehash({**status(f), 'request_sha256': 'f' * 64})
    assert c.ingest_status(f.job, bad) == 'quarantined'
    assert c.ingest_status(f.job, status(f)) == 'duplicate'
    assert not f.store.list('observation')


def test_controller_opt_in_only_starts_collector_with_loop(tmp_path, monkeypatch):
    from workbench.controller import Controller
    store = Store(tmp_path / 'controller.sqlite3')
    try:
        plain = Controller(store, tmp_path)
        assert plain.result_collector is None
        enabled = Controller(store, tmp_path, result_collector_enabled=True,
                             result_collector_options={'fetch': lambda *a, **k: pytest.fail('no issued jobs')})
        assert enabled.result_collector.thread is None
        monkeypatch.setattr(enabled, '_loop', lambda: enabled.stop.set())
        enabled.loop()
        assert enabled.result_collector.thread is not None
        assert not enabled.result_collector.thread.is_alive()
    finally:
        store.db.close()


def test_collector_flag_is_pinned_in_runtime_binding(tmp_path):
    from workbench.controller import Controller
    store = Store(tmp_path / 'controller.sqlite3')
    try:
        plain = Controller(store, tmp_path)
        enabled = Controller(store, tmp_path, result_collector_enabled=True)
        assert 'worker_result_collector' not in plain.runtime_binding()
        assert enabled.runtime_binding()['worker_result_collector'] == 'issued-worker-collector-1'
        assert plain.runtime_binding()['fingerprint'] != enabled.runtime_binding()['fingerprint']
    finally:
        store.db.close()


def test_canonical_environment_observation_is_supported_source_proof(setup):
    f = setup
    f.store.db.execute('DELETE FROM records WHERE id=?', (f.source['id'],))
    source = f.store.add('observation', f.case['id'], evidence_id=f.evidence['id'],
                         type='linux_environment', fields={'run_id': f.job['source_run']})
    c, _ = collector(f)
    assert c.collect_once()['ingested'] == 1
    assert collection_receipts(f)[0]['scope']['source_ref']['id'] == source['id']


def test_bounded_ingest_never_materializes_all_case_observations(setup, monkeypatch):
    f = setup
    original = f.store.list
    def no_full_observation_scan(kind, *args, **kwargs):
        if kind == 'observation':pytest.fail('collector cannot materialize all observations')
        return original(kind, *args, **kwargs)
    monkeypatch.setattr(f.store, 'list', no_full_observation_scan)
    c, _ = collector(f)
    assert c.collect_once()['ingested'] == 1
    assert len(original('observation')) == 1


def test_same_observation_from_different_issued_jobs_is_not_independent_new_evidence(setup):
    f = setup
    first, _ = collector(f)
    assert first.collect_once()['ingested'] == 1
    first_ids = f.store.get(f.job['id'])['observation_ids']
    second_job = f.store.add('investigation_job', f.case['id'], task_id=f.task['id'],
        evidence_id=f.evidence['id'], generation=0, source_run=f.job['source_run'],
        fingerprint='f'*64, status='submitted', dispatched_at=now(),
        request=f.job['request'], test_intent_ids=[])
    second_f = SimpleNamespace(**{**vars(f), 'job': second_job})
    second, _ = collector(second_f)
    assert second.collect_once()['ingested'] == 1
    assert f.store.get(second_job['id'])['observation_ids'] == first_ids
    assert len(f.store.list('observation')) == 1
    assert len(f.store.list('lineage')) == 2  # Two result uses, not two independent originals.


def test_graph_legacy_terminal_uses_same_collector_proof_boundary(setup, monkeypatch):
    from workbench.investigation_graph import Investigation
    f = setup
    c, _ = collector(f)
    f.controller.result_collector = c
    monkeypatch.setattr('workbench.investigation_graph.worker_request', lambda *a, **k: status(f))
    graph = Investigation(f.controller, f.case['id'], f.evidence, f.task)
    assert graph.await_jobs({'job_id': f.job['id']}) == {'route':'dispatch'}
    assert graph.ingest_validate({'job_id': f.job['id']}) == {'route':'dispatch'}
    c.collect_once()
    assert len(f.store.list('observation')) == 1
    assert len([r for r in f.store.list('receipt') if r.get('receipt_type')=='investigation_tool']) == 1


@pytest.mark.parametrize('kind', ['hash_mismatch', 'late'])
def test_graph_legacy_terminal_cannot_adopt_invalid_or_late_scope(setup, monkeypatch, kind):
    from workbench.investigation_graph import Investigation
    f = setup
    c, _ = collector(f)
    f.controller.result_collector = c
    def fetch(*args, **kwargs):
        reply = status(f)
        if kind == 'hash_mismatch':reply = rehash({**reply, 'request_sha256':'f'*64})
        else:f.store.update(f.case['id'], status='resource_limit')
        return reply
    monkeypatch.setattr('workbench.investigation_graph.worker_request', fetch)
    graph = Investigation(f.controller, f.case['id'], f.evidence, f.task)
    assert graph.await_jobs({'job_id': f.job['id']}) == {'route':'await_jobs'}
    assert graph.ingest_validate({'job_id': f.job['id']}) == {'route':'await_jobs'}
    assert not f.store.list('observation')


def test_graph_default_legacy_poll_race_rechecks_fresh_ingested_state(setup, monkeypatch):
    from workbench.investigation_graph import Investigation
    f = setup
    c, _ = collector(f)
    def fetch(*args, **kwargs):
        # Default-off legacy poll started first, then collector commits before
        # that GET returns. The fresh writer-fenced recheck prevents re-ingest.
        assert c.ingest_status(f.job, status(f)) == 'ingested'
        return status(f)
    monkeypatch.setattr('workbench.investigation_graph.worker_request', fetch)
    graph = Investigation(f.controller, f.case['id'], f.evidence, f.task)
    assert graph.await_jobs({'job_id':f.job['id']}) == {'route':'dispatch'}
    assert graph.ingest_validate({'job_id':f.job['id']}) == {'route':'dispatch'}
    assert len(f.store.list('observation')) == 1 and len(f.store.list('lineage')) == 1


@pytest.mark.parametrize('enabled', [False, True])
def test_dossier_poll_and_collector_commit_one_physical_result(setup, monkeypatch, enabled):
    import workbench.dossiers as dossiers
    f = setup
    c, _ = collector(f)
    if enabled:f.controller.result_collector = c
    batch = f.store.add('dossier_batch', f.case['id'], task_id=f.task['id'],
        evidence_id=f.evidence['id'], generation=0, status='await_checks',
        job_ids=[f.job['id']], dossier_ids=[], round=1, attempts=0)
    def fetch(*args, **kwargs):
        assert c.ingest_status(f.job, status(f)) == 'ingested'
        return status(f)
    monkeypatch.setattr('workbench.runtime_contract.guard', lambda *a, **k: None)
    monkeypatch.setattr(dossiers, 'worker_request', fetch)
    prepared = (f.task, {'questions':[]}, [batch], [batch])
    assert dossiers._finish_one(f.controller, f.case['id'], f.evidence, f.task, prepared=prepared) is None
    assert len(f.store.list('observation')) == 1 and len(f.store.list('lineage')) == 1
    assert len([r for r in f.store.list('receipt') if r.get('receipt_type')=='investigation_tool']) == 1


def test_dossier_terminal_quarantine_cannot_use_old_ingest_path(setup, monkeypatch):
    import workbench.dossiers as dossiers
    f = setup
    c, _ = collector(f)
    f.controller.result_collector = c
    batch = f.store.add('dossier_batch', f.case['id'], task_id=f.task['id'],
        evidence_id=f.evidence['id'], generation=0, status='await_checks',
        job_ids=[f.job['id']], dossier_ids=[], round=1, attempts=0)
    reply = rehash({**status(f), 'request_sha256':'f'*64})
    monkeypatch.setattr('workbench.runtime_contract.guard', lambda *a, **k: None)
    monkeypatch.setattr(dossiers, 'worker_request', lambda *a, **k: reply)
    prepared = (f.task, {'questions':[]}, [batch], [batch])
    dossiers._finish_one(f.controller, f.case['id'], f.evidence, f.task, prepared=prepared)
    assert not f.store.list('observation')
    monkeypatch.setattr(dossiers, 'worker_request', lambda *a, **k: pytest.fail('quarantined job is not re-polled'))
    dossiers._finish_one(f.controller, f.case['id'], f.evidence, f.task, prepared=prepared)
    assert not f.store.list('observation')


@pytest.mark.parametrize('field,value', [('task_id','TASK-foreign'), ('evidence_id','EVIDENCE-foreign'),
    ('generation',1), ('generation',True), ('source_run','RUN-'+'f'*32), ('signature','different')])
def test_logical_intent_must_match_exact_physical_owner_scope(setup, field, value):
    from workbench.retrieval import fingerprint_scope
    f = setup
    target = {'task_id':f.task['id'], 'evidence_id':f.evidence['id'], 'generation':0,
              'source_run':f.job['source_run'], 'signature':f.evidence['signature'],
              'request':fingerprint_scope(f.job['request'])}
    target[field] = value
    intent = f.store.add('test_intent', f.case['id'], scope=target, status='reserved')
    f.store.update(f.job['id'], test_intent_ids=[intent['id']])
    c, _ = collector(f)
    assert c.collect_once()['quarantined']==1
    assert f.store.get(intent['id'])['status']=='reserved'
    assert collection_receipts(f)[0]['failure_code']=='logical_intent_scope_mismatch'
    assert not f.store.list('observation')


def test_intent_result_availability_remains_unassessed(setup):
    from workbench.retrieval import fingerprint_scope
    f = setup
    target = {'task_id':f.task['id'], 'evidence_id':f.evidence['id'], 'generation':0,
              'source_run':f.job['source_run'], 'signature':f.evidence['signature'],
              'request':fingerprint_scope(f.job['request'])}
    intent = f.store.add('test_intent', f.case['id'], scope=target, status='reserved')
    f.store.update(f.job['id'], test_intent_ids=[intent['id']])
    c, _ = collector(f)
    assert c.collect_once()['ingested']==1
    actual = f.store.get(intent['id'])
    assert actual['status']=='complete' and actual['assessment_status']=='unassessed'
    assert actual['result_scope']['job_id']==f.job['id']


@pytest.mark.parametrize('transport_failure',[False,True])
def test_graph_post_return_cannot_revert_collector_ingest(setup, monkeypatch, transport_failure):
    from workbench.investigation_graph import Investigation
    import httpx
    f = setup
    f.store.update(f.job['id'],status='admitted')
    c, _ = collector(f)
    f.controller.result_collector = c
    plan = f.store.add('investigation_plan',f.case['id'],task_id=f.task['id'],output={'tool_calls':[]})
    graph = Investigation(f.controller,f.case['id'],f.evidence,f.task)
    monkeypatch.setattr(graph,'admit',lambda *a,**k:None)
    monkeypatch.setattr(graph,'run',lambda *a,**k:{'source_run':f.job['source_run']})
    def fetch(*args,**kwargs):
        assert c.ingest_status(f.store.get(f.job['id']),status(f))=='ingested'
        if transport_failure:raise httpx.ReadTimeout('fixture delivery response lost')
        return status(f)
    monkeypatch.setattr('workbench.investigation_graph.worker_request',fetch)
    graph.dispatch({'plan_id':plan['id']})
    assert f.store.get(f.job['id'])['status']=='ingested'
    assert len(f.store.list('observation'))==1


def test_dossier_post_return_cannot_revert_collector_ingest(setup,monkeypatch):
    import workbench.dossiers as dossiers
    f = setup
    f.store.update(f.job['id'],status='admitted')
    c, _ = collector(f)
    f.controller.result_collector = c
    batch = f.store.add('dossier_batch',f.case['id'],task_id=f.task['id'],evidence_id=f.evidence['id'],
        generation=0,status='await_checks',job_ids=[f.job['id']],dossier_ids=[],round=1,attempts=0)
    def fetch(method,*args,**kwargs):
        if method=='POST':assert c.ingest_status(f.store.get(f.job['id']),status(f))=='ingested'
        return status(f)
    monkeypatch.setattr('workbench.runtime_contract.guard',lambda *a,**k:None)
    monkeypatch.setattr(dossiers,'worker_request',fetch)
    dossiers._finish_one(f.controller,f.case['id'],f.evidence,f.task,
        prepared=(f.task,{'questions':[]},[batch],[batch]))
    assert f.store.get(f.job['id'])['status']=='ingested'
    assert len(f.store.list('observation'))==1 and len(f.store.list('lineage'))==1


def test_new_source_run_during_poll_retains_old_run_only_as_late_result(setup):
    f=setup
    reply=status(f)
    def fetch(*args,**kwargs):
        f.store.add('receipt',f.case['id'],task_id=f.task['id'],evidence_id=f.evidence['id'],
            result={'run_id':'RUN-'+'f'*32})
        return reply
    c=ResultCollector(f.controller,enabled=True,fetch=fetch,wallclock_budget=.08)
    assert c.collect_once()['late_preserved']==1
    assert not f.store.list('observation')
    assert collection_receipts(f)[0]['scope']['source_run']==f.job['source_run']


def test_collector_started_after_new_source_head_cannot_currently_adopt_old_issued_run(setup):
    f=setup
    f.store.add('receipt',f.case['id'],task_id=f.task['id'],evidence_id=f.evidence['id'],
        result={'run_id':'RUN-'+'f'*32})
    c,_=collector(f)
    assert c.collect_once()['late_preserved']==1
    assert not f.store.list('observation')


def test_source_owner_superseded_during_poll_is_not_current_source(setup):
    f=setup
    source_task=f.store.add('task',f.case['id'],evidence_id=f.evidence['id'],status='covered')
    f.store.add('receipt',f.case['id'],task_id=source_task['id'],evidence_id=f.evidence['id'],
        result={'run_id':f.job['source_run']})
    reply=status(f)
    def fetch(*args,**kwargs):
        f.store.update(source_task['id'],superseded=True)
        return reply
    c=ResultCollector(f.controller,enabled=True,fetch=fetch,wallclock_budget=.08)
    assert c.collect_once()['late_preserved']==1
    assert not f.store.list('observation')
