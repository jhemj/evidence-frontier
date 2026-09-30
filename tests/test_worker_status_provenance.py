"""Worker HTTP status exposes its durable request/attempt/result provenance."""
import hashlib
import json
import time

import pytest

from workbench.evidence_access import metadata
from workbench.models import WorkerJobRequest
from workbench.worker_jobs import WorkerJobs


def close(manager):
    manager.stop.set()
    manager.wake.set()
    manager.thread.join(2.)
    manager.db.close()
    manager.process_lock.close()


@pytest.fixture
def completed(tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'fixture.img').write_bytes(b'fixture only')
    monkeypatch.setattr('workbench.worker_jobs.execute', lambda *a, **k:
                        {'status':'covered', 'complete':True, 'observations':[]})
    manager = WorkerJobs(tmp_path / 'analysis', evidence)
    body = WorkerJobRequest(job_key='a'*64, action='inventory', path='fixture.img',
                            signature=metadata(evidence, 'fixture.img')['signature'])
    manager.submit(body)
    for _ in range(100):
        if manager.status('a'*64)['status']=='succeeded':break
        time.sleep(.002)
    assert manager.status('a'*64)['status']=='succeeded'
    yield manager, body
    close(manager)


def test_status_request_hash_matches_exact_normalized_request(completed):
    manager, body = completed
    request = body.model_dump()
    request.pop('job_key')
    reply = manager.status('a'*64)
    assert reply['version']=='worker-job-status-2'
    assert reply['request_sha256']==hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    assert reply['runtime_code']==manager.runtime_code
    assert reply['attempt_id'] and reply['attempts']==1
    assert reply['status_sha256']==manager.digest({k:v for k,v in reply.items() if k!='status_sha256'})


def test_every_status_rechecks_request_not_only_restart(completed):
    manager, _ = completed
    path = manager.root / ('a'*64+'.json')
    envelope = json.loads(path.read_bytes())
    envelope['request_sha256']='f'*64
    path.write_text(json.dumps(envelope))
    with pytest.raises(ValueError, match='요청 해시'):
        manager.status('a'*64)


def test_every_status_rechecks_active_attempt_not_only_restart(completed):
    manager, _ = completed
    with manager.lock:
        manager.db.execute('INSERT INTO attempts (id,job_id,owner,started_at,runtime_code) VALUES (?,?,?,?,?)',
                           ('f'*32, 'a'*64, 'fixture-new-owner', 'fixture-time', manager.runtime_code))
        manager.db.execute('UPDATE active_attempts SET attempt_id=? WHERE job_id=?', ('f'*32,'a'*64))
    with pytest.raises(ValueError, match='attempt'):
        manager.status('a'*64)


def test_failed_attempt_runtime_is_not_relabelled_after_worker_restart(tmp_path, monkeypatch):
    evidence = tmp_path / 'evidence'
    evidence.mkdir()
    (evidence / 'fixture.img').write_bytes(b'fixture only')
    def fail(*args, **kwargs):raise ValueError('fixture failure')
    monkeypatch.setattr('workbench.worker_jobs.execute', fail)
    monkeypatch.setattr('workbench.runtime_contract.code_identity', lambda:'b'*64)
    body = WorkerJobRequest(job_key='a'*64, action='inventory', path='fixture.img',
                            signature=metadata(evidence, 'fixture.img')['signature'])
    manager = WorkerJobs(tmp_path / 'analysis', evidence)
    manager.submit(body)
    for _ in range(100):
        if manager.status('a'*64)['status']=='failed':break
        time.sleep(.002)
    assert manager.status('a'*64)['runtime_code']=='b'*64
    close(manager)
    monkeypatch.setattr('workbench.runtime_contract.code_identity', lambda:'c'*64)
    manager = WorkerJobs(tmp_path / 'analysis', evidence)
    try:
        reply = manager.status('a'*64)
        assert manager.runtime_code=='c'*64 and reply['runtime_code']=='b'*64
        with manager.lock:
            manager.db.execute('UPDATE attempts SET runtime_code=NULL')
        assert manager.status('a'*64)['runtime_code'] is None
    finally:
        close(manager)


def test_failed_status_hash_binds_error_and_attempt(completed):
    manager, _ = completed
    with manager.lock:
        manager.db.execute("UPDATE jobs SET status='failed',error='fixture failure' WHERE id=?", ('a'*64,))
    first = manager.status('a'*64)
    with manager.lock:
        manager.db.execute("UPDATE jobs SET error='different fixture failure' WHERE id=?", ('a'*64,))
    second = manager.status('a'*64)
    assert first['status_sha256']!=second['status_sha256']
    assert 'result' not in first and 'result_sha256' not in first
