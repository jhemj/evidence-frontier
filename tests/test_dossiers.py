from workbench.controller import Controller
from workbench.store import Store
from workbench.dossiers import finish, digest


def setup(tmp_path):
    c=Controller(Store(tmp_path/'case.db'),tmp_path);cid=c.create('Review','','standard')['id']
    e=c.store.add('evidence',cid,path='fixture.E01',signature='sig',connected=True)
    cell=c.store.add('coverage',cid,evidence_id=e['id'],status='queued')
    t=c.store.add('task',cid,evidence_id=e['id'],cell_id=cell['id'],action='ai_judgment',analysis_version='linux-hunt-2')
    c.store.add('config','',provider={'model':'local','base_url':'http://localhost:11434','protocol':'ollama'})
    c.store.add('observation',cid,evidence_id=e['id'],type='linux_environment',timestamp=None,source_location='fixture',fields={'run_id':'RUN-'+'a'*32})
    o=c.store.add('observation',cid,evidence_id=e['id'],type='linux_detection',timestamp=None,source_location='fixture:/etc/cron.d/a',
        fields={'rule_id':'writable_persistence','title':'지속성 설정 단서','path':'/etc/cron.d/a'})
    return c,cid,e,t,o


def test_checks_execute_before_reassessment_and_restart_is_idempotent(tmp_path,monkeypatch):
    c,cid,e,t,o=setup(tmp_path);calls=[];submitted=[]
    def model(self,question,pack,role):
        calls.append(pack)
        fs=[{'dossier_id':d['id'],'title':d['title'],'judgment':'미확인','reason':'정황 검토','observation_ids':d['observation_ids'][:2],
             'alternatives':['정상 관리'],'remaining_checks':[]} for d in pack['required_dossiers']]
        checks=[]
        if not pack['executed_checks']:
            checks=[{'tool':'read_file','path':'/etc/cron.d/a','query':'','hypothesis_id':fs[0]['dossier_id'],'reason':'설정 확인','success_condition':'대상 경로와 설정 내용 확인'}]
        return {'summary':'검토','findings':fs,'next_checks':checks},{'output':{'findings':fs}}
    def worker(method,path,**kwargs):
        if method=='POST':submitted.append(kwargs['json']['job_key'])
        result={'status':'covered','complete':True,'observations':[{'type':'linux_tool_result','timestamp':None,'source_location':'fixture:/etc/cron.d/a','fields':{'path':'/etc/cron.d/a','excerpt':'test result'}}]}
        return {'status':'succeeded','result':result,'result_sha256':digest(result)}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    monkeypatch.setattr('workbench.dossiers.worker_request',worker)
    for _ in range(25):
        result=finish(c,cid,e,t)
        if result:break
    assert result['dossiers_reviewed']==1 and result['unavailable_baselines']==3
    assert submitted and len(submitted)==len(set(submitted))
    assert any(p['executed_checks'] and any(o['type']=='linux_tool_result' for o in p['observations']) for p in calls)
    count=len(calls);assert finish(c,cid,e,t)==result and len(calls)==count
    c.store.update(t['id'],retry_generation=1);finish(c,cid,e,c.store.get(t['id']))
    assert len(calls)==count+1


def test_model_failure_keeps_all_leads_and_baseline_areas(tmp_path,monkeypatch):
    c,cid,e,t,o=setup(tmp_path)
    def fail(*args,**kw):raise ValueError('invalid model output')
    monkeypatch.setattr('workbench.dossiers.Provider.generate',fail)
    for _ in range(10):
        result=finish(c,cid,e,t)
        if result:break
    assert result['status']=='failed'
    ds=c.store.list('dossier',cid)
    assert len(ds)==4 and sum(d['status']=='model_failed' for d in ds)==1
    assert sum(d['status']=='unavailable' for d in ds)==3
    assert c.store.get(o['id'])['fields']['rule_id']=='writable_persistence'
    card=c.snapshot(cid)['visual_timeline']['cards'][0]
    assert card['review_status']=='model_failed' and '실패' in card['reason']


def test_timeline_keeps_undated_facts_and_refutation_history(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    from workbench.dossiers import seed
    seed(c,cid,e,t)
    d=next(d for d in c.store.list('dossier',cid) if not d['baseline'])
    initial={'dossier_id':d['id'],'title':'행위 가설','judgment':'유력','reason':'처음 정황','observation_ids':[o['id']],'timeline_role':'핵심'}
    changed={**initial,'judgment':'미확인','reason':'독립 반대 근거로 해석 철회','timeline_role':'반증됨'}
    for f in (initial,changed):c.store.add('receipt',cid,task_id=t['id'],receipt_type='dossier_model',output={'findings':[f]})
    timeline=c.snapshot(cid)['visual_timeline']
    card=next(x for x in timeline['cards'] if x['id']==d['id'])
    assert card['start'] is None and card['role']=='반증됨'
    assert timeline['history_total']==1
    assert timeline['history'][0]['before']=='유력 · 핵심'
    c.store.update(e['id'],connected=False)
    assert c.snapshot(cid)['visual_timeline']['cards']==[]


def test_failed_current_scan_cannot_feed_old_run_to_ai(tmp_path,monkeypatch):
    c,cid,e,t,o=setup(tmp_path)
    from workbench.store import now
    c.store.add('task',cid,evidence_id=e['id'],action='integrity',status='covered',phase=1)
    c.store.add('task',cid,evidence_id=e['id'],action='linux_scan',status='failed',phase=5)
    epoch=c.store.add('epoch',cid,jobs=0,max_jobs=64,started_at=now())
    c.store.update(cid,status='running',epoch_id=epoch['id'])
    c.store.update(t['id'],status='queued',phase=7,attempts=0)
    monkeypatch.setattr('workbench.controller.metadata',lambda *a:{'signature':'sig'})
    c.step(cid)
    assert c.store.get(t['id'])['status']=='blocked'
    assert not c.store.list('dossier',cid)


def test_scan_retry_replaces_downstream_analysis_revision(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    cell=c.store.add('coverage',cid,evidence_id=e['id'],status='failed')
    scan=c.store.add('task',cid,evidence_id=e['id'],cell_id=cell['id'],action='linux_scan',status='failed',attempts=1,analysis_version='linux-hunt-2')
    c.retry(scan['id'])
    assert c.store.get(t['id'])['superseded']
    c.ensure_investigation(cid)
    active=[x for x in c.store.list('task',cid) if x['action']=='ai_judgment' and not x.get('superseded')]
    assert len(active)==1 and active[0]['id']!=t['id']


def test_partial_judgment_can_resume_without_repeating_raw_scan(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    c.store.update(t['id'],status='partial',attempts=32)
    scan=c.store.add('task',cid,evidence_id=e['id'],action='linux_scan',status='partial',attempts=1,analysis_version='linux-hunt-2')
    report_cell=c.store.add('coverage',cid,evidence_id=e['id'],status='covered')
    report_task=c.store.add('task',cid,evidence_id=e['id'],cell_id=report_cell['id'],action='investigation_report',phase=8,status='covered',analysis_version='linux-hunt-2')
    previous=c.store.add('judgment',cid,task_id=t['id'],generation=0,findings=[],summary='Prior partial result')
    c.retry(t['id'])
    c.ensure_investigation(cid)
    assert c.store.get(t['id'])['retry_generation']==1
    assert c.store.get(t['id'])['status']=='queued'
    assert c.store.get(scan['id'])['status']=='partial'
    assert c.store.get(scan['id'])['attempts']==1
    assert not c.store.get(scan['id']).get('superseded')
    assert c.store.get(previous['id'])['summary']=='Prior partial result'
    assert c.store.get(report_task['id'])['status']=='queued'


def test_prior_report_alert_is_a_lead_not_a_verdict(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    from workbench.dossiers import seed
    for text in ('[PASS] no matching process','[DETECT] suspicious executable'):
        c.store.add('observation',cid,evidence_id=e['id'],type='linux_inspection_result',timestamp=None,source_location='prior report',
            fields={'path':'/var/log/previous-report.txt','excerpt':text,'inspection_context':True})
    seed(c,cid,e,t)
    leads=[d for d in c.store.list('dossier',cid) if '이전 점검 경보' in d['title']]
    assert len(leads)==1 and leads[0]['total_records']==1 and leads[0]['finding'] is None


def test_identical_binary_copies_keep_separate_context_judgments(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    from workbench.dossiers import seed
    for path in ('/usr/lib/library.so','/opt/backup/library.so'):
        c.store.add('observation',cid,evidence_id=e['id'],type='linux_detection',timestamp=None,source_location=path,
            fields={'path':path,'rule_id':'ELF_directory_hooking_traits','title':'ELF static traits',
                    'source_sha256':'a'*64,'source_complete':True})
    seed(c,cid,e,t)
    rows=[d for d in c.store.list('dossier',cid) if d['title']=='ELF static traits']
    assert len(rows)==2 and all(r['total_records']==1 for r in rows)
    assert len({r['group_key'] for r in rows})==2
    assert {r['content_sha256'] for r in rows}=={'a'*64}


def test_normal_package_changes_do_not_displace_security_leads():
    from workbench.dossiers import lead_priority
    normal=('package_digest_mismatch',0,'/etc/hostname')
    for key in (('package_digest_mismatch',0,'/usr/bin/ssh'),
                ('package_digest_mismatch',0,'/etc/ssh/sshd_config'),
                ('writable_persistence',0,'/etc/cron.d/job'),
                ('prior_report_alert',0,'/var/log/inspection.txt')):
        assert lead_priority(key)<lead_priority(normal)


def test_ai_discovery_without_static_rule_gets_its_own_source_review(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    from workbench.dossiers import seed
    graph=c.store.add('task',cid,evidence_id=e['id'],action='linux_investigate')
    candidate=c.store.add('claim',cid,task_id=graph['id'],automatic=True,text='AI가 새로 발견한 경쟁 가설',observation_ids=[o['id']])
    seed(c,cid,e,t)
    rows=[d for d in c.store.list('dossier',cid) if d.get('source_claim_id')==candidate['id']]
    assert len(rows)==1 and rows[0]['finding'] is None and rows[0]['observation_ids']==[o['id']]
