import threading
from concurrent.futures import ThreadPoolExecutor

from test_dossiers import setup
from workbench.model_concurrency import batch_lease, configure_endpoint, lease, try_reserve_batch


def test_batch_reservation_is_atomic_and_per_case_bounded():
    tokens=[]
    barrier=threading.Barrier(4)

    def reserve(batch):
        barrier.wait(timeout=2)
        token=try_reserve_batch('case-1', batch)
        if token:
            tokens.append(token)
        return token is not None

    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(reserve, ('a','a','b','c')))
    assert sum(results)==2
    assert len(tokens)==2
    for token in tokens:
        token.release()
    # Release is idempotent, which makes finally-based worker cleanup safe.
    tokens[0].release()


def test_batch_lease_does_not_duplicate_and_keeps_default_endpoint_serial():
    entered=threading.Event()
    release=threading.Event()
    second=threading.Event()

    def first():
        with batch_lease('case-serial', 'one', 'endpoint-serial') as acquired:
            assert acquired
            entered.set()
            release.wait(timeout=2)

    def duplicate():
        entered.wait(timeout=2)
        with batch_lease('case-serial', 'one', 'endpoint-serial') as acquired:
            assert not acquired
            second.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        a=pool.submit(first)
        b=pool.submit(duplicate)
        assert second.wait(timeout=2)
        release.set()
        a.result(timeout=2)
        b.result(timeout=2)


def test_endpoint_parallelism_requires_explicit_configuration():
    identity='endpoint-explicit'
    configure_endpoint(identity, limit=2)
    entered=0
    lock=threading.Lock()
    barrier=threading.Barrier(2)

    def work():
        nonlocal entered
        with lease(identity):
            with lock:
                entered+=1
            barrier.wait(timeout=2)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(work) for _ in range(2)]
        for future in futures:
            future.result(timeout=3)
    assert entered==2


def test_endpoint_reconfiguration_cannot_downgrade_or_upgrade_live_identity():
    identity='endpoint-reconfiguration'
    configure_endpoint(identity, limit=2)
    configure_endpoint(identity, limit=2)  # idempotent repeated driver install
    import pytest
    with pytest.raises(RuntimeError):
        configure_endpoint(identity, limit=1)


def test_review_concurrency_config_defaults_to_one_and_accepts_only_explicit_two():
    from workbench.models import ProviderConfig
    assert ProviderConfig(base_url='http://localhost:11434').review_concurrency==1
    assert ProviderConfig(base_url='http://localhost:11434', review_concurrency=2).review_concurrency==2
    import pytest
    with pytest.raises(ValueError):
        ProviderConfig(base_url='http://localhost:11434', review_concurrency=True)
    with pytest.raises(ValueError):
        ProviderConfig(base_url='http://localhost:11434', review_concurrency=3)


def test_independent_dossier_batches_enter_model_together_and_adopt_once(tmp_path, monkeypatch):
    from workbench.dossiers import finish

    controller, cid, evidence, task, first = setup(tmp_path)
    config=controller.store.list('config')[-1]
    controller.store.update(config['id'], provider={**config['provider'], 'review_concurrency':2})
    for rule, path in (('writable_persistence', '/etc/cron.d/a'),
                       ('extra_uid_zero', '/etc/passwd'),
                       ('reverse_shell', '/tmp/rsh'),
                       ('download_execute', '/tmp/drop')):
        controller.store.add('observation', cid, evidence_id=evidence['id'],
            type='linux_detection', timestamp=None, source_location=path,
            fields={'rule_id': rule, 'title': 'independent lead', 'path': path})
    barrier=threading.Barrier(2)
    calls=[]
    barrier_lock=threading.Lock()
    barrier_calls=0

    def consult_stub(*args, **kwargs):
        nonlocal barrier_calls
        pack=args[2]
        with barrier_lock:
            barrier_calls+=1
            sync=barrier_calls<=2
        if sync:
            barrier.wait(timeout=3)
        calls.append(pack['required_dossiers'][0]['id'])
        findings=[{'dossier_id':d['id'], 'title':'fact', 'judgment':'미확인',
            'reason':'bounded', 'observation_ids':d['observation_ids'],
            'alternatives':[], 'remaining_checks':[]} for d in pack['required_dossiers']]
        return {'summary':'parallel', 'findings':findings, 'next_checks':[]}, {'model':'fixture'}

    monkeypatch.setattr('workbench.dossiers.consult', consult_stub)
    finish(controller, cid, evidence, task)
    # Round zero records the blind assessment and schedules the compare pass.
    barrier=threading.Barrier(2)
    finish(controller, cid, evidence, task)
    assert len(calls)==4 and len(set(calls))==2
    for _ in range(4):
        if sum(d.get('status')=='reviewed' for d in controller.store.list('dossier',cid))==2:
            break
        finish(controller, cid, evidence, task)
    reviewed=[d for d in controller.store.list('dossier',cid) if d.get('status')=='reviewed']
    assert len(reviewed)==4
    done=[b for b in controller.store.list('dossier_batch',cid) if b['status']=='done']
    assert len(done)==2 and len({b.get('receipt_id') for b in done})==2
