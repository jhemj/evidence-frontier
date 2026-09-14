import hashlib
import json
import threading
import zipfile
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from workbench.api import create_app
from workbench.controller import Controller
from workbench.reporting import build_report
from workbench.store import Store


def setup(tmp_path):
    controller=Controller(Store(tmp_path/'case.db'),tmp_path)
    cid=controller.create('Snapshot','','standard')['id']
    evidence=controller.store.add('evidence',cid,name='fixture',path='fixture',signature='unchanged',connected=True)
    return controller,cid,evidence


def test_slow_report_api_does_not_lock_case_reads_or_message_writes(tmp_path,monkeypatch):
    app=create_app(tmp_path/'data',tmp_path,start_worker=False)
    c=app.state.controller;cid=c.create('Concurrent','','standard')['id']
    entered=threading.Event();release=threading.Event()
    def prepare(*args):
        entered.set()
        assert release.wait(5),'test did not release report generation'
        return {},{},{}
    monkeypatch.setattr('workbench.investigation_export.prepare',prepare)
    with TestClient(app) as client,ThreadPoolExecutor(max_workers=2) as pool:
        pending=pool.submit(client.post,f'/api/cases/{cid}/reports',headers={'X-Requested-With':'frontier'})
        assert entered.wait(5)
        try:
            response=pool.submit(client.get,'/api/cases').result(timeout=2)
            assert response.status_code==200
            response=pool.submit(client.post,f'/api/cases/{cid}/pause',headers={'X-Requested-With':'frontier'}).result(timeout=2)
            assert response.status_code==200
            pool.submit(c.store.add,'message',cid,text='Question during packaging').result(timeout=2)
        finally:release.set()
        assert pending.result(timeout=5).status_code==200


@pytest.mark.parametrize('change',['disconnect','reconnect','signature','judgment','observation','task'])
def test_changed_scope_is_not_registered_as_current_report(tmp_path,monkeypatch,change):
    c,cid,e=setup(tmp_path)
    def prepare(*args):
        if change=='disconnect':c.store.update(e['id'],connected=False)
        elif change=='reconnect':
            c.store.update(e['id'],connected=False);c.store.update(e['id'],connected=True)
        elif change=='signature':c.store.update(e['id'],signature='changed')
        elif change=='judgment':c.store.update(cid,result_revision='new-judgment')
        else:c.store.add(change,cid,evidence_id=e['id'])
        return {},{},{}
    monkeypatch.setattr('workbench.investigation_export.prepare',prepare)
    with pytest.raises(ValueError,match='근거나 판단이 변경'):
        build_report(c,cid,tmp_path/'reports')
    assert not c.store.list('report',cid)


def test_written_source_bytes_must_match_prepared_hash(tmp_path,monkeypatch):
    c,cid,e=setup(tmp_path);source=tmp_path/'retained.bin';source.write_bytes(b'changed')
    monkeypatch.setattr('workbench.investigation_export.prepare',lambda *args:({},
        {'evidence/retained.bin':source},{'evidence/retained.bin':hashlib.sha256(b'original').hexdigest()}))
    with pytest.raises(ValueError,match='원문 산출물이 변경'):
        build_report(c,cid,tmp_path/'reports')
    assert not c.store.list('report',cid)


def test_failed_output_never_creates_completed_report(tmp_path,monkeypatch):
    c,cid,e=setup(tmp_path)
    def fail(*args):raise OSError('output failure')
    monkeypatch.setattr('workbench.investigation_export.prepare',fail)
    with pytest.raises(OSError):build_report(c,cid,tmp_path/'reports')
    assert not c.store.list('report',cid)


def test_concurrent_reports_keep_distinct_immutable_packages(tmp_path,monkeypatch):
    c,cid,e=setup(tmp_path);barrier=threading.Barrier(2)
    def prepare(*args):barrier.wait(timeout=5);return {},{},{}
    monkeypatch.setattr('workbench.investigation_export.prepare',prepare)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(build_report,c,cid,tmp_path/'reports') for _ in range(2)]
        records=[f.result(timeout=10) for f in futures]
    assert len({r['report_id'] for r in records})==2
    assert records[0]['snapshot']['scope_sha256']==records[1]['snapshot']['scope_sha256']
    for r in records:
        archive=tmp_path/'reports'/r['report_id']/'report.zip'
        assert hashlib.sha256(archive.read_bytes()).hexdigest()==r['sha256']
        with zipfile.ZipFile(archive) as z:
            manifest=json.loads(z.read('manifest.json'));doc=json.loads(z.read('report.json'))
            assert manifest['snapshot']==doc['snapshot']==r['snapshot']
            for name,expected in manifest['files'].items():assert hashlib.sha256(z.read(name)).hexdigest()==expected


def test_other_connection_changes_and_rollbacks_update_revision_atomically(tmp_path):
    c,cid,e=setup(tmp_path);other=Store(tmp_path/'case.db')
    before=c.store.report_revision(cid)
    with pytest.raises(RuntimeError):
        with other.tx():
            other.update(e['id'],connected=False)
            raise RuntimeError('rollback')
    assert c.store.report_revision(cid)==before
    assert c.store.get(e['id'])['connected']
    with other.tx():
        other.update(e['id'],connected=False)
        other.update(e['id'],connected=True)
    assert c.store.report_revision(cid)==before+2
    other.db.close()


def test_messages_and_reports_do_not_invalidate_scope_but_removal_does(tmp_path):
    c,cid,e=setup(tmp_path);before=c.store.report_revision(cid)
    c.store.add('message',cid,text='question');c.store.audit(cid,'read')
    c.store.add('report',cid,report_id='prior')
    assert c.store.report_revision(cid)==before
    observation=c.store.add('observation',cid,evidence_id=e['id'])
    assert c.store.report_revision(cid)==before+1
    # Low-level removal is detected even though the application disallows it.
    with c.store.tx():c.store.db.execute('DELETE FROM records WHERE id=?',(observation['id'],))
    assert c.store.report_revision(cid)==before+2
