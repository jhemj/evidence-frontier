import json
import sqlite3
import time
import pytest
from workbench.worker_jobs import WorkerJobs
from workbench.models import WorkerJobRequest
from workbench.evidence_access import metadata

def close(manager):
    manager.stop.set();manager.wake.set();manager.thread.join(3);manager.db.close();manager.process_lock.close()

def test_duplicate_requests_publish_once_and_tampering_fails(tmp_path,monkeypatch):
    (tmp_path/'ev').mkdir();(tmp_path/'ev'/'x.img').write_bytes(b'fixture')
    calls=[]
    def execute(*args):calls.append(args);return {'status':'covered','complete':True,'observations':[]}
    monkeypatch.setattr('workbench.worker_jobs.execute',execute)
    manager=WorkerJobs(tmp_path/'analysis',tmp_path/'ev')
    try:
        body=WorkerJobRequest(job_key='a'*64,signature=metadata(tmp_path/'ev','x.img')['signature'],action='inventory',path='x.img')
        for _ in range(4):manager.submit(body)
        for _ in range(100):
            if manager.status('a'*64)['status']=='succeeded':break
            time.sleep(.01)
        assert manager.status('a'*64)['status']=='succeeded' and len(calls)==1
        assert manager.status('a'*64)['attempts']==1
        original=manager.runtime_code;manager.runtime_code='changed-build'
        with pytest.raises(ValueError,match='실행 조합'):manager.status('a'*64)
        manager.runtime_code=original
        file=manager.root/('a'*64+'.json');payload=json.loads(file.read_bytes());payload['result']['complete']=False;file.write_text(json.dumps(payload))
        with pytest.raises(ValueError,match='해시'):manager.status('a'*64)
    finally:close(manager)

def test_restart_unknown_execution_is_not_requeued(tmp_path):
    (tmp_path/'ev').mkdir();(tmp_path/'ev'/'x.img').write_bytes(b'fixture')
    manager=WorkerJobs(tmp_path/'analysis',tmp_path/'ev');close(manager)
    body=WorkerJobRequest(job_key='b'*64,signature=metadata(tmp_path/'ev','x.img')['signature'],action='inventory',path='x.img')
    request=body.model_dump();request.pop('job_key')
    with sqlite3.connect(tmp_path/'analysis/jobs/jobs.sqlite3') as db:
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)',('b'*64,json.dumps(request,sort_keys=True),'running',1,'now',None))
    manager=WorkerJobs(tmp_path/'analysis',tmp_path/'ev')
    try:
        assert manager.submit(body)['status']=='execution_unknown'
        assert manager.status('b'*64)['attempts']==1
    finally:close(manager)
