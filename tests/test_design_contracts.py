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
