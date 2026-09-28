import json
from fastapi.testclient import TestClient
from workbench.store import Store
from workbench.controller import Controller
from workbench.activity import page,project


def setup(tmp_path):
    s=Store(tmp_path/'case.sqlite3');c=Controller(s,tmp_path)
    cid=c.create('Actual work','','standard')['id'];s.update(cid,status='running')
    e=s.add('evidence',cid,name='fixture.E01',connected=True)
    t=s.add('task',cid,evidence_id=e['id'],action='linux_investigate',status='running')
    return s,cid,e,t


def test_actual_work_excludes_polls_proposals_and_parent_phases(tmp_path):
    s,cid,e,t=setup(tmp_path)
    s.update(t['id'],started_at='2026-01-01T00:00:00Z')
    s.add('audit',cid,action='heartbeat')
    s.add('message',cid,text='working…')
    s.add('investigation_job',cid,task_id=t['id'],status='admitted',request={'tool':'search'})
    s.add('task',cid,evidence_id=e['id'],action='inventory',status='queued')
    r=s.add('model_reservation',cid,task_id=t['id'],purpose='plan',status='reserved',target='조사 영역 1')
    assert [i['id'] for i in project(s,cid)]==[r['id']]
    s.add('receipt',cid,task_id=t['id'],receipt_type='investigator_model',reservation_id=r['id'],output={'secret':'not UI'})
    rows=project(s,cid)
    assert len(rows)==1 and rows[0]['status']=='done' and rows[0]['ended_at']
    assert 'secret' not in json.dumps(rows)


def test_job_status_time_and_no_fake_start_for_legacy_records(tmp_path):
    s,cid,e,t=setup(tmp_path)
    j=s.add('investigation_job',cid,task_id=t['id'],status='submitted',worker_status='queued',
        dispatched_at='2026-01-01T00:00:00Z',request={'tool':'read_file','path':'/fixture'})
    assert project(s,cid)[0]['status']=='waiting'
    s.update(j['id'],worker_status='running')
    assert project(s,cid)[0]['status']=='running'
    s.update(j['id'],status='ingested',result_status='partial',ended_at='2026-01-01T00:00:03Z')
    row=project(s,cid)[0];assert row['status']=='partial' and row['started_at']=='2026-01-01T00:00:00Z'
    old=s.add('investigation_job',cid,task_id=t['id'],status='ingested',result_status='covered',request={'tool':'search','query':'old'})
    assert next(i for i in project(s,cid) if i['id']==old['id'])['started_at'] is None


def test_pause_stale_reservations_and_input_completion(tmp_path):
    s,cid,e,t=setup(tmp_path)
    a=s.add('review_input',cid,task_id=t['id'],pack={'observations':['must not ship']})
    b=s.add('review_input',cid,task_id=t['id'],activity_target='단서 2개',pack={})
    rows={r['id']:r for r in project(s,cid)}
    assert rows[a['id']]['status']=='interrupted' and rows[b['id']]['status']=='running'
    s.add('receipt',cid,task_id=t['id'],receipt_type='dossier_model_error',input_record_id=b['id'],error='invalid source IDs')
    assert next(r for r in project(s,cid) if r['id']==b['id'])['status']=='failed'
    f=s.add('model_reservation',cid,task_id=t['id'],purpose='falsifier',status='reserved')
    s.update(cid,status='paused')
    assert next(r for r in project(s,cid) if r['id']==f['id'])['status']=='paused'
    s.update(t['id'],superseded=True)
    assert next(r for r in project(s,cid) if r['id']==f['id'])['status']=='interrupted'


def test_full_history_paginated_filtered_and_keeps_failed_prior_scope(tmp_path):
    s,cid,e,t=setup(tmp_path)
    ids=[]
    for n in range(77):
        r=s.add('model_reservation',cid,task_id=t['id'],purpose='plan',status='failed',generation=0,error='fixture')
        ids.append(r['id'])
    s.update(t['id'],retry_generation=1)
    first=page(s,cid,kind='model',status='failed');second=page(s,cid,kind='model',status='failed',offset=50)
    assert first['total']==77 and first['has_more'] and not second['has_more']
    assert [r['id'] for r in first['items']+second['items']]==ids[::-1]
    assert all(r['archived_scope'] for r in first['items'])
    assert page(s,cid,kind='tool')['total']==0


def test_activity_api_filters_validation_and_unchanged_poll(tmp_path):
    from workbench.api import create_app
    app=create_app(tmp_path/'api',tmp_path,start_worker=False)
    with TestClient(app) as client:
        s=app.state.controller.store
        cid=app.state.controller.create('Activity API','','standard')['id']
        s.update(cid,status='running')
        evidence=s.add('evidence',cid,name='fixture',connected=True)
        s.add('task',cid,evidence_id=evidence['id'],action='linux_investigate',status='running')
        r=s.add('model_reservation',cid,purpose='plan',status='failed',error='fixture')
        out=client.get(f'/api/cases/{cid}/activity?kind=model&status=failed&limit=1').json()
        assert out['items'][0]['id']==r['id']
        for query in ('offset=-1','limit=101','status=unknown','kind=unknown'):
            assert client.get(f'/api/cases/{cid}/activity?{query}').status_code==422
        snap=client.get(f'/api/cases/{cid}').json()
        unchanged=client.get(f'/api/cases/{cid}?since='+snap['view_revision']).json()
        assert unchanged['unchanged'] and unchanged['activity']['items'][0]['id']==r['id']
        assert unchanged['eta_estimate']['low_seconds']>=60
        assert snap['eta_estimate']['high_seconds']>=snap['eta_estimate']['low_seconds']
