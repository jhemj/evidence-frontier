import hashlib
import json
import zipfile
from pathlib import Path
import httpx
import pytest
from fastapi.testclient import TestClient
from workbench.api import create_app
from workbench.controller import Controller,ROOT
from workbench.store import Store
from workbench.worker import execute,safe_path,segments,read_events
from workbench.reporting import build_report,report_document,render
from workbench.openrelik import OpenRelikGateway
from workbench.provider import validate_url

@pytest.fixture
def controller(tmp_path):
    ev=tmp_path/'evidence';ev.mkdir()
    (ev/'activity.ndjson').write_bytes((ROOT/'examples/activity.ndjson').read_bytes())
    return Controller(Store(tmp_path/'data/cases.db'),ev)

def run_case(c,profile='standard'):
    case=c.create('Test','Verify activity',profile)
    c.register(case['id'],'activity.ndjson')
    c.start(case['id'])
    while c.step(case['id']):pass
    return c.snapshot(case['id'])

def test_pipeline_keeps_gaps_and_dedupes_observations(controller):
    s=run_case(controller)
    assert s['case']['status']=='quiescent'
    assert s['summary']=={'total':6,'covered':5,'gaps':1}
    assert len(s['observation'])==6 # integrity + metadata + 3 events + path correlation
    assert len(s['receipt'])==6
    assert len(s['hypothesis'])==1
    assert len(s['lineage'])==9 # duplicate observations retain provenance from timeline pass
    assert s['evidence'][0]['segment_manifest'][0]['sha256']==hashlib.sha256((controller.evidence_root/'activity.ndjson').read_bytes()).hexdigest()
    events=[o for o in s['observation'] if o.get('type')=='windows_event']
    assert events[0]['timestamp']=='2026-09-01T00:21:12+00:00'

def test_finite_epoch_repeat_does_not_rerun(controller):
    s=run_case(controller,'triage');case=s['case']['id']
    assert s['case']['status']=='complete'
    controller.start(case)
    assert not controller.step(case)
    assert len(controller.store.list('receipt',case))==4

def test_mutated_evidence_blocks_downstream(controller):
    c=controller.create('Changed','','standard');controller.register(c['id'],'activity.ndjson')
    (controller.evidence_root/'activity.ndjson').write_text('{}',encoding='utf8')
    controller.start(c['id'])
    while controller.step(c['id']):pass
    s=controller.snapshot(c['id'])
    assert s['summary']['covered']==0
    assert not s['observation']

def test_empty_is_scoped_zero_not_failure(controller):
    p=controller.evidence_root/'empty.jsonl';p.write_text('',encoding='utf8')
    result=execute(controller.evidence_root,'normalize','empty.jsonl')
    assert result['status']=='covered_zero' and result['complete']
    p.write_text('{bad json}',encoding='utf8')
    result=execute(controller.evidence_root,'normalize','empty.jsonl')
    assert result['status']=='failed' and not result['complete']

def test_naive_timestamp_is_not_silently_assumed(controller):
    p=controller.evidence_root/'bad.jsonl';p.write_text(json.dumps({'timestamp':'2026-01-01T00:00:00','fields':{},'source_location':'L1'}))
    with pytest.raises(ValueError,match='시간대'):read_events(p)

def test_path_and_segment_guards(controller,tmp_path):
    outside=tmp_path/'outside';outside.write_text('secret')
    with pytest.raises(ValueError):safe_path(controller.evidence_root,'../outside')
    with pytest.raises(ValueError):safe_path(controller.evidence_root,str(outside))
    e=controller.evidence_root/'disk.E01';e.write_bytes(b'a')
    (controller.evidence_root/'disk.E03').write_bytes(b'c')
    with pytest.raises(ValueError,match='누락'):segments(e)

def test_restart_pauses_and_records_unknown_execution(controller):
    c=controller.create('Recovery','','standard');controller.register(c['id'],'activity.ndjson');controller.start(c['id'])
    task=controller.store.list('task',c['id'])[0]
    controller.store.update(task['id'],status='running',attempts=1)
    controller.recover()
    assert controller.store.get(c['id'])['status']=='paused'
    assert controller.store.get(task['id'])['status']=='failed'
    controller.retry(task['id'])
    assert controller.store.get(task['id'])['status']=='queued'

def test_report_escaping_hashes_and_no_unapproved_claims(controller,tmp_path):
    s=run_case(controller)
    controller.store.update(s['case']['id'],name='<script>alert(1)</script>')
    controller.store.add('claim',s['case']['id'],status='candidate',text='invented',observation_ids=[])
    document=report_document(controller,s['case']['id'])
    assert not document['claims'] and len(document['limitations'])==1
    assert '<script>alert(1)</script>' not in render(document)
    result=build_report(controller,s['case']['id'],tmp_path/'reports')
    with zipfile.ZipFile(tmp_path/'reports'/result['report_id']/'report.zip') as z:
        manifest=json.loads(z.read('manifest.json'))
        assert all(hashlib.sha256(z.read(name)).hexdigest()==digest for name,digest in manifest['files'].items())

def test_claim_gate_requires_falsification_and_same_case(controller):
    s=run_case(controller)
    ob=s['observation'][0]
    claim=controller.store.add('claim',s['case']['id'],status='candidate',text='Observation',observation_ids=[ob['id']],falsification=None)
    with pytest.raises(ValueError,match='반증'):controller.decide(claim['id'],'approve','checked')
    controller.store.update(claim['id'],falsification={'alternatives':[],'contradicting_observation_ids':[],'missing_checks':['independent parse']})
    with pytest.raises(ValueError,match='미완료'):controller.decide(claim['id'],'approve','checked')
    controller.store.update(claim['id'],falsification={'alternatives':[],'contradicting_observation_ids':[],'missing_checks':[]})
    assert controller.decide(claim['id'],'approve','Checked source reference')['status']=='approved'

def test_api_end_to_end_and_origin_guard(tmp_path):
    app=create_app(tmp_path/'data',ROOT/'examples',start_worker=False)
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.post('/api/cases',json={'name':'bad'}).status_code==403
        headers={'X-Requested-With':'frontier'}
        assert client.post('/api/cases',headers={**headers,'Origin':'https://evil.example'},json={'name':'bad'}).status_code==403
        r=client.post('/api/cases',headers=headers,json={'name':'HTTP case','profile':'triage'})
        assert r.status_code==200
        id=r.json()['id']
        assert client.post(f'/api/cases/{id}/evidence',headers=headers,json={'path':'activity.ndjson'}).status_code==200
        assert client.post(f'/api/cases/{id}/start',headers=headers).status_code==200
        while app.state.controller.step(id):pass
        assert client.get(f'/api/cases/{id}').json()['case']['status']=='complete'
        report=client.post(f'/api/cases/{id}/reports',headers=headers).json()
        assert client.get('/api/reports/'+report['id']+'/download').content[:2]==b'PK'

def test_openrelik_only_approved_templates_and_files():
    calls=[];spec={'tasks':[{'name':'approved-worker'}]}
    digest=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    def handle(request):
        calls.append(request)
        if request.url.path.endswith('/templates/7'):return httpx.Response(200,json={'spec_json':json.dumps(spec)})
        return httpx.Response(200,json={'id':12,'folder':{'id':99},'spec_json':json.dumps(spec)})
    g=OpenRelikGateway('http://localhost/api/v1','test',{'inventory':{'id':7,'sha256':digest}},httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(ValueError):g.submit('shell',1,[10],[10])
    with pytest.raises(ValueError):g.submit('inventory',1,[99],[10])
    registered=g.submit('inventory',1,[10],[10]);g.run(registered)
    assert calls[-1].url.path=='/api/v1/folders/99/workflows/12/run/'
    assert calls[-1].headers['x-openrelik-access-token']=='test'
    assert json.loads(calls[-1].content)['workflow_spec']==spec

def test_public_model_url_denied():
    with pytest.raises(ValueError):validate_url('http://8.8.8.8:1234',True)
    with pytest.raises(ValueError):validate_url('http://169.254.169.254',True)
    assert validate_url('http://127.0.0.1:11434')=='http://127.0.0.1:11434'
