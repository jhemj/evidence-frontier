import threading
import time
from workbench.worker_jobs import WorkerJobs
from workbench.models import WorkerJobRequest
from workbench.evidence_access import metadata


def test_durable_job_exposes_live_bytes_without_claiming_completion(tmp_path, monkeypatch):
    source = tmp_path / 'source';source.mkdir();(source/'image.raw').write_bytes(b'fixture')
    published, finish = threading.Event(), threading.Event()
    def execute(root, action, path, progress):
        progress(stage='segment_hash',bytes_done=4,total_bytes=7)
        published.set()
        assert finish.wait(3)
        return {'status':'covered','observations':[],'complete':True}
    monkeypatch.setattr('workbench.worker_jobs.execute',execute)
    manager=WorkerJobs(tmp_path/'analysis',source)
    key='c'*64
    try:
        manager.submit(WorkerJobRequest(job_key=key,action='integrity',path='image.raw',
            signature=metadata(source,'image.raw')['signature']))
        assert published.wait(3)
        assert manager.progress(key)['bytes_done']==4
        assert manager.status(key)['status']=='running'
        assert 'result' not in manager.status(key)
        finish.set()
        for _ in range(100):
            if manager.status(key)['status']=='succeeded':break
            time.sleep(.01)
        assert manager.status(key)['status']=='succeeded'
        assert manager.progress(key) is None
    finally:
        finish.set();manager.stop.set();manager.wake.set();manager.thread.join(3)
        manager.db.close();manager.process_lock.close()
