import json
import stat
from workbench.coverage_map import source_matrix
from workbench.source_selection import spread
from workbench.review_validation import check_errors


def test_pre_candidate_matrix_keeps_noise_and_unsupported_gaps(tmp_path):
    inventory=tmp_path/'inventory.ndjson'
    rows=[{'mode':stat.S_IFREG,'path':f'/srv/data/{i}','partition_offset':0} for i in range(300)]
    rows.append({'mode':stat.S_IFREG,'path':'/var/log/secure','partition_offset':0})
    inventory.write_text(''.join(json.dumps(r)+'\n' for r in reversed(rows)))
    matrix=source_matrix(inventory,[],[{'status':'read_only'}],[])
    areas={r['area']:r for r in matrix}
    assert areas['applications']['not_selected']==300
    assert areas['authentication']['discovered']==1
    assert areas['memory']['status']=='unsupported'
    assert all(not r['absence_is_refutation'] for r in matrix)
    incomplete=source_matrix(inventory,[],[{'status':'error'}],[])
    assert incomplete[0]['status']=='enumeration_incomplete'


def test_source_selection_is_order_and_interpretation_independent():
    rows=[{'id':str(i),'type':'linux_authentication','timestamp':f'2026-01-{i+1:02}',
        'fields':{'path':'/var/log/secure','byte_offset':i,'ai_summary':'malicious'}} for i in range(25)]
    first=[r['id'] for r in spread(rows,8)]
    for r in rows:r['fields']['ai_summary']='normal'
    assert [r['id'] for r in spread(list(reversed(rows)),8)]==first
    assert any(8<int(i)<17 for i in first)


def test_multi_source_selection_spans_each_sources_full_range():
    rows=[{'id':f'{source}-{i}','type':'linux_authentication','timestamp':f'2026-01-01T{i:04}',
        'fields':{'path':source,'byte_offset':i}} for source in ('one','two') for i in range(100)]
    selected=spread(rows,8)
    for source in ('one','two'):
        indices=[r['fields']['byte_offset'] for r in selected if r['fields']['path']==source]
        assert len(indices)==4 and min(indices)==0 and max(indices)==99
    assert selected==spread(list(reversed(rows)),8)


def test_completed_search_without_logging_preconditions_is_inconclusive():
    checks=[{'id':'job','status':'covered_zero','observation_ids':['scope-record']}]
    output={'check_assessments':[{'check_id':'job','outcome':'refutes','basis':'absence',
        'reason':'zero matches','observation_ids':['scope-record']}]}
    assert any(e['code']=='absence_preconditions_unverified' for e in check_errors(output,checks,{}))
    output['check_assessments'][0]['outcome']='inconclusive'
    assert not check_errors(output,checks,{})


def test_running_case_pauses_on_changed_model_without_altering_records(tmp_path):
    from test_dossiers import setup
    c,cid,e,t,o=setup(tmp_path)
    bound=c.runtime_binding();c.store.update(cid,status='running',runtime_binding=bound)
    config=c.store.list('config')[-1]
    c.store.update(config['id'],provider={'model':'different','base_url':'http://localhost:11434','protocol':'ollama'})
    assert c.step(cid) is False
    assert c.store.get(cid)['status']=='paused'
    assert c.store.get(o['id'])==o
    assert c.store.get(t['id'])==t


def test_three_dossiers_sharing_one_job_retain_every_contract(tmp_path,monkeypatch):
    from test_dossiers import setup
    from test_review_repair import response
    from workbench.dossiers import finish, seed
    c,cid,e,t,o=setup(tmp_path)
    for kind in ('linux_authentication','linux_persistence','linux_command'):
        c.store.add('observation',cid,evidence_id=e['id'],type=kind,timestamp=None,
            source_location='fixture',fields={'path':'/var/log/fixture','excerpt':'inert observation'})
    seed(c,cid,e,t)
    for batch in c.store.list('dossier_batch',cid):
        if len(batch['dossier_ids'])!=3:c.store.update(batch['id'],status='done')
    def model(self,question,pack,role):
        output=response(pack)
        output['next_checks']=[{'tool':'search','query':'same scope','reason':'context','hypothesis_id':d['id'],
            'success_condition':'support '+d['id'],'refutation_condition':'contrary '+d['id']} for d in pack['required_dossiers']]
        return output,{'output':output}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    finish(c,cid,e,t)
    jobs=c.store.list('investigation_job',cid)
    assert len(jobs)==1 and len(jobs[0]['dossier_ids'])==3
    assert {v['dossier_id'] for v in jobs[0]['contracts']}==set(jobs[0]['dossier_ids'])
    c.store.update(jobs[0]['id'],status='ingested',observation_ids=[o['id']],result_status='covered',result_scope={})
    finish(c,cid,e,t)
    def new_contract(self,question,pack,role):
        output=response(pack)
        output['next_checks']=[{'tool':'search','query':'same scope','reason':'new logical test',
            'hypothesis_id':pack['required_dossiers'][0]['id'],'success_condition':'new support','refutation_condition':'new contrary'}]
        return output,{'output':output}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',new_contract)
    finish(c,cid,e,t)
    batch=next(b for b in c.store.list('dossier_batch',cid) if len(b['dossier_ids'])==3)
    assert batch['status']=='await_checks' and batch['round']==1
    finish(c,cid,e,t);finish(c,cid,e,t)
    batch=c.store.get(batch['id'])
    assert batch['status']=='done'
    assert len(batch['output']['check_assessments'])==4
    assert len(c.store.list('investigation_job',cid))==1


def test_previous_generation_is_history_before_new_judgment_exists(tmp_path):
    from test_dossiers import setup
    from workbench.judgment import current
    c,cid,e,t,o=setup(tmp_path)
    previous=c.store.add('judgment',cid,task_id=t['id'],evidence_id=e['id'],generation=0,
        findings=[{'observation_ids':[o['id']]}])
    assert current(c,cid)==[previous]
    c.store.update(t['id'],retry_generation=1)
    assert current(c,cid)==[]
    assert c.store.get(previous['id'])==previous


def test_empty_baselines_publish_coverage_without_model_failure(tmp_path,monkeypatch):
    from workbench.controller import Controller
    from workbench.store import Store
    from workbench.dossiers import finish
    c=Controller(Store(tmp_path/'empty.db'),tmp_path);cid=c.create('Empty','','standard')['id']
    e=c.store.add('evidence',cid,connected=True,signature='fixture')
    t=c.store.add('task',cid,evidence_id=e['id'])
    def forbidden(*a,**k):raise AssertionError('no source to review')
    monkeypatch.setattr('workbench.dossiers.Provider.generate',forbidden)
    result=finish(c,cid,e,t)
    assert result['status']=='partial' and result['unavailable_baselines']==3
    assert result['judgment_counts']=={'확인':0,'유력':0,'미확인':0}
    assert finish(c,cid,e,t)==result and len(c.store.list('dossier',cid))==3


def test_direct_review_rejects_model_change_before_adoption(tmp_path,monkeypatch):
    import pytest
    from test_dossiers import setup
    from test_review_repair import response
    from workbench.dossiers import finish
    c,cid,e,t,o=setup(tmp_path)
    def change(self,question,pack,role):
        config=c.store.list('config')[-1]
        c.store.update(config['id'],provider={**config['provider'],'model':'changed'})
        output=response(pack);return output,{'output':output}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',change)
    with pytest.raises(ValueError,match='실행 조합'):finish(c,cid,e,t)
    assert not c.store.list('judgment',cid)
    assert not any(d.get('finding') for d in c.store.list('dossier',cid))


def test_current_reviewed_dossier_is_visible_before_queue_finishes(tmp_path):
    from test_dossiers import setup
    from workbench.judgment import current
    c,cid,e,t,o=setup(tmp_path)
    f={'title':'inert fact','judgment':'유력','reason':'source','observation_ids':[o['id']]}
    d=c.store.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],generation=0,status='reviewed',finding=f)
    c.store.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],generation=0,status='model_failed',finding=f)
    first=current(c,cid)
    assert first[0]['findings']==[f] and not c.store.list('judgment',cid)
    c.store.update(d['id'],finding={**f,'timeline_role':'반증됨'})
    second=current(c,cid)
    assert second[0]['id']!=first[0]['id'] and second[0]['findings'][0]['timeline_role']=='반증됨'
    c.store.update(t['id'],retry_generation=1)
    assert current(c,cid)==[]


def test_model_change_during_pack_is_rejected_before_reservation(tmp_path,monkeypatch):
    import pytest
    from test_dossiers import setup
    from workbench.dossiers import finish
    from workbench.review_context import fit
    c,cid,e,t,o=setup(tmp_path)
    def changed(pack):
        fit(pack)
        config=c.store.list('config')[-1]
        c.store.update(config['id'],provider={**config['provider'],'model':'changed'})
    monkeypatch.setattr('workbench.review_context.fit',changed)
    def forbidden(*a,**k):raise AssertionError('model transmission must not occur')
    monkeypatch.setattr('workbench.dossiers.Provider.generate',forbidden)
    with pytest.raises(ValueError,match='실행 조합'):finish(c,cid,e,t)
    assert not c.store.list('review_input',cid)


def test_refuted_finding_is_separate_in_html_and_retained_in_json(tmp_path):
    from test_dossiers import setup
    from workbench.reporting import report_document,render
    c,cid,e,t,o=setup(tmp_path)
    f={'title':'거절된 해석 표식','judgment':'유력','timeline_role':'반증됨','reason':'반대 근거',
       'observation_ids':[o['id']],'alternatives':[],'remaining_checks':[]}
    c.store.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],generation=0,status='reviewed',
        finding=f,observation_ids=[o['id']],group_key='fixture',baseline=False)
    doc=report_document(c,cid);html=render(doc)
    current,history=html.split('<h2>반증된 해석</h2>')
    assert f['title'] not in current and f['title'] in history
    assert doc['judgments'][0]['findings'][0]==f
