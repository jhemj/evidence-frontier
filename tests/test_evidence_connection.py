import hashlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from workbench.api import create_app
from workbench.controller import Controller,ROOT
from workbench.store import Store
from workbench.evidence_access import metadata,catalog
from workbench.reporting import build_report,report_document,preview_document


@pytest.fixture
def c(tmp_path):
    evidence=tmp_path/'evidence';evidence.mkdir()
    (evidence/'activity.ndjson').write_bytes((ROOT/'examples/activity.ndjson').read_bytes())
    return Controller(Store(tmp_path/'cases.sqlite3'),evidence)


def test_disconnect_preserves_history_and_report_then_reconnects(c,tmp_path):
    case=c.create('Connection','','triage')['id'];e=c.register(case,'activity.ndjson')
    c.start(case)
    while c.step(case):pass
    before=c.snapshot(case)
    report=build_report(c,case,tmp_path/'reports')
    archive=tmp_path/'reports'/report['report_id']/'report.zip'
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    c.connection(case,e['id'],False)
    after=c.snapshot(case)
    for kind in ('observation','receipt','coverage','task'):assert after[kind]==before[kind]
    assert after['summary']=={'total':0,'covered':0,'gaps':0}
    assert not c.pack(case)['observations']
    assert not report_document(c,case)['observations']
    assert report_document(c,case)['disconnected_evidence'][0]['id']==e['id']
    assert hashlib.sha256(archive.read_bytes()).hexdigest()==digest
    with pytest.raises(ValueError,match='연결'):c.start(case)
    c.connection(case,e['id'],True)
    c.start(case)
    assert not c.step(case)
    assert c.snapshot(case)['receipt']==before['receipt']
    assert c.pack(case)['observations']


def test_disconnect_waits_for_running_task_even_after_pause(c):
    case=c.create('Concurrency','','triage')['id'];e=c.register(case,'activity.ndjson');c.start(case)
    with pytest.raises(ValueError,match='일시정지'):c.connection(case,e['id'],False)
    task=c.store.list('task',case)[0]
    epoch=c.store.get(case)['epoch_id']
    c.store.update(task['id'],status='running');c.pause(case)
    with pytest.raises(ValueError,match='진행 중'):c.connection(case,e['id'],False)
    c.store.update(task['id'],status='queued');c.connection(case,e['id'],False)
    assert c.store.get(epoch)['status']=='scope_changed'
    assert c.store.get(task['id'])['status']=='queued'
    other=c.create('Other','','triage')['id']
    with pytest.raises(ValueError,match='이 사건'):c.connection(other,e['id'],True)
    c.connection(case,e['id'],True);c.start(case)
    assert c.step(case)


def test_ai_and_external_work_prevent_disconnect(c):
    case=c.create('Concurrency','','triage')['id'];e=c.register(case,'activity.ndjson')
    c.model_lock.acquire()
    try:
        with pytest.raises(ValueError,match='AI'):c.connection(case,e['id'],False)
    finally:c.model_lock.release()
    job=c.store.add('remote_job',case,status='needs_review',evidence_ids=[e['id']])
    with pytest.raises(ValueError,match='외부'):c.connection(case,e['id'],False)
    c.store.update(job['id'],status='SUCCESS');c.connection(case,e['id'],False)


def test_e01_groups_segments_and_detects_secondary_segment_change(c):
    (c.evidence_root/'disk.E01').write_bytes(b'one')
    (c.evidence_root/'disk.E02').write_bytes(b'two')
    files=catalog(c.evidence_root)
    image=next(f for f in files if f['path']=='disk.E01')
    assert image['segment_count']==2 and image['total_size']==6
    assert not any(f['path']=='disk.E02' for f in files)
    with pytest.raises(ValueError,match='첫 파일'):metadata(c.evidence_root,'disk.E02')
    case=c.create('Segments','','triage')['id'];e=c.register(case,'disk.E01');c.connection(case,e['id'],False)
    (c.evidence_root/'disk.E02').write_bytes(b'changed')
    with pytest.raises(ValueError,match='변경'):c.connection(case,e['id'],True)
    assert c.store.get(e['id'])['connected'] is False


def test_http_disconnect_and_reconnect_are_case_scoped(tmp_path):
    app=create_app(tmp_path,ROOT/'examples',start_worker=False)
    h={'X-Requested-With':'frontier'}
    with TestClient(app) as client:
        case=client.post('/api/cases',headers=h,json={'name':'HTTP connection','question':'','profile':'triage'}).json()['id']
        e=client.post(f'/api/cases/{case}/evidence',headers=h,json={'path':'activity.ndjson'}).json()['id']
        url=f'/api/cases/{case}/evidence/{e}'
        assert client.post(url+'/disconnect').status_code==403
        assert client.post(url+'/disconnect',headers=h).json()['connected'] is False
        assert client.post(url+'/reconnect',headers=h).json()['connected'] is True


def test_partial_scope_keeps_observations_without_claiming_completion(c,monkeypatch):
    case=c.create('Partial','','triage')['id'];e=c.register(case,'activity.ndjson');c.start(case)
    assert c.step(case) # real hash gate
    monkeypatch.setattr('workbench.controller.execute',lambda *a:{'status':'partial','complete':False,'truncated':False,
        'error':'Budget exhausted','observations':[{'type':'filesystem_entry','timestamp':None,'source_location':'inode:1','fields':{'path':'/a'}}]})
    assert c.step(case)
    s=c.snapshot(case)
    assert len(s['observation'])==2
    assert s['summary']['covered']==1 and s['summary']['gaps']==1
    assert report_document(c,case)['limitations']


def test_disconnection_excludes_transitive_correlations(c):
    case=c.create('References','','triage')['id'];e=c.register(case,'activity.ndjson')
    (c.evidence_root/'second.ndjson').write_text('')
    other=c.register(case,'second.ndjson')
    source=c.store.add('observation',case,evidence_id=e['id'],fields={})
    correlation=c.store.add('observation',case,evidence_id=other['id'],fields={'related_observation_ids':[source['id']]})
    c.store.add('observation',case,evidence_id=other['id'],fields={'related_observation_ids':[correlation['id']]})
    c.connection(case,e['id'],False)
    assert not c.active_observations(case)
    assert len(c.store.list('observation',case))==3


def test_report_preview_is_bounded_but_full_report_keeps_every_observation(c):
    case=c.create('Preview','','triage')['id'];e=c.register(case,'activity.ndjson')
    for i in range(205):c.store.add('observation',case,evidence_id=e['id'],fields={},type='event',source_location=f'line:{i}')
    assert len(preview_document(c,case)['observations'])==200
    assert '나머지 5건' in preview_document(c,case)['notice']
    assert len(report_document(c,case)['observations'])==205
