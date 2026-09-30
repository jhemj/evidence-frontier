import json
import zipfile

import pytest
from workbench.controller import Controller
from workbench.store import Store, now
from workbench.terminal_reports import process


def setup(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_ROOT',str(tmp_path/'data'))
    controller=Controller(Store(tmp_path/'case.db'),tmp_path)
    cid=controller.create('Partial-report contract fixture','','standard')['id']
    evidence=controller.store.add('evidence',cid,name='fixture',path='fixture',signature='unchanged',connected=True)
    cell=controller.store.add('coverage',cid,evidence_id=evidence['id'],action='investigation_report',phase=8,status='queued',label='Report')
    task=controller.store.add('task',cid,evidence_id=evidence['id'],cell_id=cell['id'],action='investigation_report',
        label='Report',phase=8,status='queued',attempts=0,fingerprint='fixture')
    epoch=controller.store.add('epoch',cid,status='running',started_at=now(),jobs=64,max_jobs=64)
    controller.store.update(cid,status='running',epoch_id=epoch['id'])
    monkeypatch.setattr('workbench.provider.Provider.generate',lambda *a,**k:pytest.fail('report finalization must not call a model'))
    return controller,cid,task,epoch


@pytest.mark.parametrize('limit',['time','jobs'])
def test_execution_budget_still_creates_consistent_partial_dual_report(tmp_path,monkeypatch,limit):
    c,cid,task,epoch=setup(tmp_path,monkeypatch)
    if limit=='time':c.store.update(epoch['id'],jobs=0,started_at='2000-01-01T00:00:00+00:00')
    assert c.step(cid) is False
    case=c.store.get(cid)
    assert case['status']=='resource_limit' and case['ended_at']
    assert case['end_reason']==('runtime_limit' if limit=='time' else 'job_limit')
    assert c.store.get(task['id'])['status']=='queued'
    assert process(c,cid) is True and process(c,cid) is False
    report=c.store.list('report',cid)[0];final=c.store.list('report_finalization',cid)[0]
    assert final['status']=='generated' and final['report_record_id']==report['id']
    assert report['snapshot']['scope_revision']==final['source_revision']==c.store.report_revision(cid)
    with zipfile.ZipFile(tmp_path/'data'/'reports'/report['report_id']/'report.zip') as archive:
        doc=json.loads(archive.read('report.json'))
        assert doc['case']['status']=='resource_limit'
        assert doc['completion']['execution_terminated'] is True
        assert doc['execution_closure']['reason']==case['end_reason']
        assert doc['completion']['analysis_complete_in_supported_scope'] is False
        for reader in ('executive','analyst'):
            assert '예산 한도로 실행 종료' in archive.read(reader+'.html').decode()
            assert archive.read(reader+'.docx').startswith(b'PK')
    assert c.store.get(task['id'])['status']=='queued' # Export is not analysis completion.


@pytest.mark.parametrize('status',['complete','quiescent'])
def test_normal_terminal_paths_request_model_free_report(tmp_path,monkeypatch,status):
    c,cid,task,epoch=setup(tmp_path,monkeypatch)
    c.store.update(task['id'],status='covered' if status=='complete' else 'failed')
    assert c.step(cid) is False and c.store.get(cid)['status']==status
    assert process(c,cid) is True
    assert c.store.list('report_finalization',cid)[0]['status']=='generated'


def test_report_failure_is_independent_durable_and_not_retried_each_poll(tmp_path,monkeypatch):
    c,cid,task,epoch=setup(tmp_path,monkeypatch);c.step(cid)
    calls=[]
    def fail(*a,**k):calls.append(1);raise OSError('output unavailable')
    monkeypatch.setattr('workbench.reporting.build_report',fail)
    assert process(c,cid) is True
    for _ in range(3):assert process(c,cid) is False
    assert calls==[1] and c.store.get(cid)['status']=='resource_limit'
    assert c.store.list('report_finalization',cid)[0]['status']=='failed'
    assert not c.store.list('report',cid)


def test_late_adoption_does_not_silently_mix_into_closing_snapshot(tmp_path,monkeypatch):
    c,cid,task,epoch=setup(tmp_path,monkeypatch);c.step(cid)
    c.store.add('observation',cid,type='fixture',fields={})
    assert process(c,cid) is True
    assert c.store.list('report_finalization',cid)[0]['status']=='superseded'
    assert not c.store.list('report',cid)


def test_restart_recovers_pending_build_and_adopted_report_acknowledgement(tmp_path,monkeypatch):
    c,cid,task,epoch=setup(tmp_path,monkeypatch);c.step(cid)
    request=c.store.list('report_finalization',cid)[0]
    c.store.update(request['id'],status='building')
    restarted=Controller(c.store,tmp_path)
    assert process(restarted,cid) is True
    report=c.store.list('report',cid)[0]
    # Simulate crash after immutable report adoption, before acknowledgement.
    c.store.update(request['id'],status='building',report_record_id=None)
    assert process(restarted,cid) is True
    assert c.store.list('report',cid)==[report]
    assert c.store.get(request['id'])['report_record_id']==report['id']


def test_concurrent_adoption_of_same_closure_has_one_canonical_report(tmp_path,monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from workbench.reporting import build_report
    c,cid,task,epoch=setup(tmp_path,monkeypatch);c.step(cid)
    request=c.store.list('report_finalization',cid)[0];barrier=Barrier(2)
    def prepare(*a):barrier.wait(timeout=5);return {},{},{}
    monkeypatch.setattr('workbench.investigation_export.prepare',prepare)
    def build():return build_report(c,cid,tmp_path/'reports',expected_revision=request['source_revision'],finalization_id=request['id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending=[pool.submit(build) for _ in range(2)]
        reports=[f.result(timeout=10) for f in pending]
    assert reports[0]['id']==reports[1]['id']
    assert len(c.store.list('report',cid))==1


def test_historical_terminal_case_is_not_rewritten_or_restarted(tmp_path,monkeypatch):
    c,cid,task,epoch=setup(tmp_path,monkeypatch)
    c.store.update(cid,status='resource_limit') # Old database has no explicit request.
    before=c.store.report_revision(cid)
    assert process(c,cid) is False
    assert not c.store.list('report_finalization',cid) and not c.store.list('report',cid)
    assert c.store.report_revision(cid)==before


def test_manual_pause_waits_for_running_work_then_generates_partial_report(tmp_path,monkeypatch):
    c,cid,task,epoch=setup(tmp_path,monkeypatch)
    c.store.update(task['id'],status='running')
    assert c.pause(cid)['status']=='pause_requested'
    assert process(c,cid) is False
    c.store.update(task['id'],status='queued');c.store.update(cid,status='paused')
    assert process(c,cid) is True
    assert c.store.list('report_finalization',cid)[0]['reason']=='user_paused'
    assert c.store.get(cid)['status']=='paused'


def test_model_circuit_report_does_not_trigger_another_model_request(tmp_path,monkeypatch):
    from workbench.model_availability import defer
    from workbench.provider import ModelServiceError
    c,cid,task,epoch=setup(tmp_path,monkeypatch)
    defer(c.store,cid,task,ModelServiceError('configuration',transport='fixture',operation='prepare',retryable=False))
    assert c.store.get(cid)['status']=='paused'
    assert process(c,cid) is True
    assert c.store.list('report_finalization',cid)[0]['reason']=='model_configuration'
    report=c.store.list('report',cid)[0]
    with zipfile.ZipFile(tmp_path/'data'/'reports'/report['report_id']/'report.zip') as archive:
        doc=json.loads(archive.read('report.json'))
        assert doc['completion']['execution_exit']=='model_service'
        for reader in ('executive','analyst'):
            text=archive.read(reader+'.html').decode()
            assert 'AI 연결·설정 확인을 위해 일시정지' in text and '사용자 요청으로 중단' not in text
