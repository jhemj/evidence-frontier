import copy
import io
import zipfile
from xml.etree import ElementTree as ET
import pytest
from fastapi.testclient import TestClient
from test_dossiers import setup
from workbench.reporting import report_document, build_report, render_view
from workbench.report_views import project
from workbench.report_docx import render as word
from workbench.report_redaction import redact


def fixture(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    f={'dossier_id':'d','title':'예약 실행 설정 확인','judgment':'확인','reason':'원문에 예약 명령이 등록되어 있습니다. 실제 실행 결과는 확인되지 않았습니다.',
        'observation_ids':[o['id']],'stages':[],'alternatives':['승인된 관리 작업 가능성'],'remaining_checks':['대상 실행 로그'],'timeline_role':'핵심'}
    c.store.add('judgment',cid,task_id=t['id'],evidence_id=e['id'],generation=0,summary='검토 결과',findings=[f])
    return c,cid,e,t,o


def xml_text(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return '\n'.join(z.read(n).decode() for n in z.namelist() if n.endswith(('.xml','.rels')))


def test_shared_projection_parity_editable_and_no_external_resources(tmp_path):
    c,cid,*_=fixture(tmp_path);d=report_document(c,cid);views=project(d)
    assert views['executive']['finding_map']==views['analyst']['finding_map']
    assert views['executive']['counts']==views['analyst']['counts']=={'확인':1,'유력':0,'미확인':0}
    for view in views.values():
        html=render_view(view);xml=xml_text(word(view))
        assert 'F-001' in html and 'F-001' in xml
        for b in view['blocks']:
            if b['kind'] in ('heading','paragraph'):
                plain=b['text']
                # HTML and Word consume exactly the same blocks, not independent model prose.
                assert plain.split('\n')[0] in html
        assert 'w:tblHeader' in xml and 'w:pgSz' in xml and 'w:t' in xml
        assert 'TargetMode="External"' not in xml
        assert '조사 진행 중' in xml and '판단 항목 수' in xml


def test_distribution_removes_secrets_from_text_metadata_and_echoes(tmp_path):
    c,cid,e,t,o=fixture(tmp_path)
    secret='SYNTHETIC-password-981!';token='SYNTHETIC-token-682'
    c.store.add('observation',cid,evidence_id=e['id'],type='linux_command',timestamp=None,
        source_location='/test',fields={'password':secret,'command':f'curl --password "{secret}"\nAuthorization: Bearer {token}'})
    c.store.update(cid,name='테스트 '+secret)
    doc=report_document(c,cid);before=copy.deepcopy(doc)
    for view in project(doc).values():
        data=render_view(view)+xml_text(word(view))
        assert secret not in data and token not in data
        assert '[민감값 삭제]' in data
    assert doc==before and secret in c.store.get(cid)['name']
    value={'a':'https://name:secret-value@host/\nsshpass -p pass-value ssh host\ntoken="TOKEN-VALUE"',
        'echo':'secret-value pass-value TOKEN-VALUE','private':'-----BEGIN OPENSSH PRIVATE KEY-----\nPRIVATE-VALUE\n-----END OPENSSH PRIVATE KEY-----'}
    result=str(redact(value))
    assert all(v not in result for v in ('secret-value','pass-value','TOKEN-VALUE','PRIVATE-VALUE'))


def test_excluded_history_and_unavailable_are_not_current_counts(tmp_path):
    c,cid,e,t,o=fixture(tmp_path);doc=report_document(c,cid)
    f=copy.deepcopy(doc['judgments'][0]['findings'][0]);f['dossier_id']='refuted';f['timeline_role']='반증됨'
    doc['judgments'][0]['findings'].append(f)
    doc['dossier_history']=[{'id':'old','status':'reviewed','finding':f}]
    doc['dossiers']=[{'id':'unavailable','status':'unavailable','title':'미확보 원문'}]
    view=project(doc)['analyst']
    assert sum(view['counts'].values())==1
    html=render_view(view)
    assert 'old' in html and '미확보 원문' in html and '현재 집계 제외' in html


def test_naive_source_time_not_relabelled_as_kst(tmp_path):
    c,cid,e,t,o=fixture(tmp_path);doc=report_document(c,cid)
    next(row for row in doc['observations'] if row['id']==o['id'])['timestamp']='2026-09-01T12:34:56'
    views=project(doc);html=render_view(views['executive'])
    # This is a derived detection time, not a qualified event time. Preserve
    # it in the source appendix rather than promoting it to first intrusion.
    detail=render_view(views['analyst'])
    assert '2026-09-01T12:34:56' in detail and '2026-09-01T12:34:56+09:00' not in detail
    assert '2026-09-01T12:34:56' not in html
    assert '최초 침입 시각을 뜻하지' in html


def test_atomic_four_files_no_new_analysis_and_failure_not_registered(tmp_path,monkeypatch):
    c,cid,*_=fixture(tmp_path)
    monkeypatch.setattr('workbench.provider.Provider.generate',lambda *a,**kw:pytest.fail('report called model'))
    monkeypatch.setattr('workbench.evidence_access.worker_request',lambda *a,**kw:pytest.fail('report called worker'))
    monkeypatch.setattr('workbench.investigation_export.prepare',lambda *a: ({},{},{}))
    before=len(c.store.list('task',cid));r=build_report(c,cid,tmp_path/'reports')
    assert set(r['distributed_files'])=={'executive.html','executive.docx','analyst.html','analyst.docx'}
    assert len(c.store.list('task',cid))==before
    previous=len(c.store.list('report',cid))
    original=word
    def fail_second(view):
        if view['reader']=='analyst':raise ValueError('Word validation failure')
        return original(view)
    monkeypatch.setattr('workbench.report_docx.render',fail_second)
    with pytest.raises(ValueError,match='Word validation'):build_report(c,cid,tmp_path/'reports')
    assert len(c.store.list('report',cid))==previous


def test_saved_view_is_immutable_and_tampering_is_rejected(tmp_path):
    from workbench.api import create_app
    app=create_app(tmp_path/'data',tmp_path,start_worker=False);c=app.state.controller
    cid=c.create('예제','','standard')['id']
    with TestClient(app) as client:
        r=client.post(f'/api/cases/{cid}/reports',headers={'X-Requested-With':'frontier'}).json()
        url=f"/api/reports/{r['id']}/files/executive.html"
        before=client.get(url)
        assert before.status_code==200 and before.headers['X-Report-Scope-Changed']=='false'
        c.store.update(cid,name='갱신된 사건')
        after=client.get(url)
        assert after.content==before.content and after.headers['X-Report-Scope-Changed']=='true'
        word_response=client.get(f"/api/reports/{r['id']}/files/analyst.docx")
        assert word_response.status_code==200 and 'wordprocessingml.document' in word_response.headers['content-type']
        (tmp_path/'data'/'reports'/r['report_id']/'executive.html').write_text('tampered')
        assert client.get(url).status_code==409
        assert client.get(f"/api/reports/{r['id']}/files/report.json").status_code==404


def test_synthesis_and_review_share_lifetime_cap(tmp_path):
    from workbench.review_contracts import model_attempts,review_exhausted,model_exhausted
    c,cid,e,t,o=fixture(tmp_path);t={**t,'review_policy':'autonomous-v1'}
    for i in range(556):c.store.add('review_input',cid,task_id=t['id'])
    assert review_exhausted(c.store,cid,t) and not model_exhausted(model_attempts(c.store,cid,t),False)
    for i in range(20):c.store.add('synthesis_input',cid,task_id=t['id'])
    assert model_exhausted(model_attempts(c.store,cid,t),False)
    assert not model_exhausted(model_attempts(c.store,cid,t),True)


def test_control_bytes_visible_and_short_secret_does_not_destroy_ids(tmp_path):
    c,cid,*_=fixture(tmp_path);doc=report_document(c,cid)
    doc['observations'][0]['fields']['password']='a'
    doc['case']['name']='값 a / a-name / test\x00case'
    view=project(doc)['analyst'];data=xml_text(word(view))
    assert '값 [민감값 삭제]' in data and 'a-name' in data and '\\u0000' in data
    assert '\x00' not in data


def test_synthesis_stages_remain_visible_and_linked(tmp_path):
    c,cid,e,t,o=fixture(tmp_path);doc=report_document(c,cid)
    doc['case_synthesis']=[{'number':3,'question':'실행되었는가?','status':'reviewed','scope':{'presented_observations':1,'relevant_observations':1},
        'finding':{'title':'설정과 실행 구분','judgment':'미확인','reason':'설정만 확인됨','observation_ids':[],
            'stages':[{'stage':'configuration','judgment':'확인','statement':'예약 문자열이 있음','observation_ids':[o['id']]}]}}]
    html=render_view(project(doc)['analyst'])
    assert '종합 단계 · 설정 [확인] 예약 문자열이 있음' in html
    assert f'href="#{o["id"]}"' in html and f'id="{o["id"]}"' in html
