import httpx
from workbench.controller import Controller
from workbench.store import Store


def setup(tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'events.ndjson').write_text('{}\n')
    controller = Controller(Store(tmp_path / 'case.db'), evidence)
    case = controller.create('outage', '', 'triage')
    controller.register(case['id'], 'events.ndjson')
    monkeypatch.setenv('WORKER_URL', 'http://worker.test')
    monkeypatch.setattr('workbench.runtime_contract.guard', lambda *args: {})
    controller.start(case['id'])
    monkeypatch.setattr(controller.stop, 'wait', lambda *args: False)
    return controller, case['id']


def test_unreachable_worker_does_not_cascade_or_exhaust_jobs(tmp_path, monkeypatch):
    controller, cid = setup(tmp_path, monkeypatch)
    def unavailable(*args):
        raise httpx.RemoteProtocolError('worker disconnected')
    monkeypatch.setattr('workbench.controller.metadata', unavailable)
    first = controller.store.list('task', cid)[0]
    for _ in range(4):
        assert controller.step(cid)
    task = controller.store.get(first['id'])
    assert task['worker_waiting'] and task['status'] == 'queued'
    assert task['attempts'] == 1
    epoch = controller.store.get(controller.store.get(cid)['epoch_id'])
    assert epoch['jobs'] == 1
    assert all(t['attempts'] == 0 for t in controller.store.list('task', cid)[1:])
    assert not controller.store.list('receipt', cid)
    assert controller.store.get(cid)['status'] == 'running'


def test_unknown_recovered_job_pauses_before_downstream(tmp_path, monkeypatch):
    controller, cid = setup(tmp_path, monkeypatch)
    evidence = controller.store.list('evidence', cid)[0]
    monkeypatch.setattr('workbench.controller.metadata', lambda *args: {'signature': evidence['signature']})
    monkeypatch.setattr('workbench.evidence_access.worker_request', lambda *args, **kwargs: {'status': 'execution_unknown'})
    assert controller.step(cid)
    first = controller.store.list('task', cid)[0]
    assert first['execution_unknown'] and first['status'] == 'blocked'
    assert controller.store.get(cid)['status'] == 'paused'
    assert not controller.step(cid)
    assert all(t['attempts'] == 0 for t in controller.store.list('task', cid)[1:])
