import copy
import pytest
from test_dossiers import setup
from workbench.dossiers import seed, finish, belongs
from workbench.review_validation import errors


def response(pack):
    return {'summary':'원문 범위 검토','findings':[{'dossier_id':d['id'],'title':d['title'],
        'judgment':'미확인','reason':'추가 근거 필요','observation_ids':d['observation_ids'],
        'alternatives':[],'remaining_checks':[]} for d in pack['required_dossiers']],'next_checks':[]}


def test_invalid_citations_are_isolated_and_feedback_uses_existing_attempt_budget(tmp_path,monkeypatch):
    c,cid,e,t,o=setup(tmp_path);packs=[]
    def model(self,question,pack,role):
        packs.append(copy.deepcopy(pack));out=response(pack)
        out['findings'][0]['observation_ids']=['OBSERVATION-not-supplied']
        out['findings'][0]['reason']='rejected private interpretation'
        out['next_checks']=[{'tool':'search','query':'probe','path':'','reason':'check','hypothesis_id':out['findings'][0]['dossier_id'],'success_condition':'find source'}]
        return out,{'output':out,'model':'local','usage':{'eval_count':42}}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    finish(c,cid,e,t);finish(c,cid,e,t);finish(c,cid,e,t)
    assert len(packs)==2
    assert packs[1]['validation_feedback']['errors'][0]['code']=='unknown_observation'
    assert packs[0]['allowed_observation_ids']==[o['id'] for o in packs[0]['observations']]
    receipts=c.store.list('receipt',cid)
    assert len(receipts)==2 and all(r['receipt_type']=='dossier_model_error' for r in receipts)
    diagnostic=c.store.get(receipts[0]['diagnostic_id'])
    assert diagnostic['rejected_output']['findings'][0]['observation_ids']==['OBSERVATION-not-supplied']
    assert 'output' not in receipts[0] and len(receipts[0]['input_sha256'])==64
    assert 'rejected_output' not in receipts[0]
    assert diagnostic['model_metadata']['usage']['eval_count']==42
    assert c.store.get(receipts[0]['input_record_id'])['pack']==packs[0]
    assert c.store.list('dossier_batch',cid)[0]['status']=='failed'
    assert all(not d.get('finding') for d in c.store.list('dossier',cid))
    assert not any(s['id']=='OBSERVATION-not-supplied' for card in c.snapshot(cid)['visual_timeline']['cards'] for s in card['sources'])
    from workbench.reporting import report_document
    import json
    assert 'rejected private interpretation' not in json.dumps(report_document(c,cid))
    assert not c.store.list('investigation_job',cid)


def test_valid_second_response_is_new_judgment_not_rewritten_first_response(tmp_path,monkeypatch):
    c,cid,e,t,o=setup(tmp_path);calls=[]
    def model(self,question,pack,role):
        out=response(pack);calls.append(out)
        if len(calls)==1:out['findings'][0]['observation_ids']=['foreign']
        return out,{'output':out}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    finish(c,cid,e,t);finish(c,cid,e,t)
    assert c.store.list('dossier_batch',cid)[0]['status']=='pending'
    finish(c,cid,e,t)
    assert c.store.list('dossier_batch',cid)[0]['status']=='done'
    assert c.store.get(c.store.list('receipt',cid)[0]['diagnostic_id'])['rejected_output']==calls[0]
    assert c.store.list('receipt',cid)[1]['output']==calls[1]


def failed_case(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    for kind in ('linux_authentication','linux_persistence','linux_command'):
        c.store.add('observation',cid,evidence_id=e['id'],type=kind,timestamp=None,source_location='fixture',fields={'path':'/var/log/fixture','excerpt':'inert'})
    seed(c,cid,e,t)
    ds=c.store.list('dossier',cid)
    for d in ds:
        c.store.update(d['id'],status='model_failed')
    c.store.update(t['id'],status='partial',attempts=1)
    return c,cid,e,t,o,ds


def test_limited_recovery_has_three_targets_and_preserves_all_old_records(tmp_path):
    c,cid,e,t,o,ds=failed_case(tmp_path)
    old=copy.deepcopy(c.store.list('dossier',cid));old_batches=copy.deepcopy(c.store.list('dossier_batch',cid))
    c.retry(t['id'],failed_dossiers_only=True)
    new=c.store.get(t['id']);seed(c,cid,e,new)
    batches=[b for b in c.store.list('dossier_batch',cid) if belongs(b,new)]
    assert len(batches)==1 and len(batches[0]['dossier_ids'])==3
    assert new['repair_budget']['jobs']==4 and new['repair_source_generation']==0
    assert [c.store.get(d['id']) for d in old]==old
    assert [c.store.get(b['id']) for b in old_batches]==old_batches
    outside=[d for d in c.store.list('dossier',cid) if belongs(d,new) and d['status']=='deferred']
    assert len(outside)==1 and outside[0]['deferred_reason']=='outside_repair_scope'
    with pytest.raises(ValueError,match='이미 요청'):c.retry(t['id'],failed_dossiers_only=True)
    assert c.store.get(t['id'])['retry_generation']==1


@pytest.mark.parametrize('block',['running','pause_requested','disconnected','superseded','no_failures'])
def test_repair_refuses_ineligible_scope_without_consuming_allowance(tmp_path,block):
    c,cid,e,t,o,ds=failed_case(tmp_path)
    if block in ('running','pause_requested'):c.store.update(cid,status=block)
    elif block=='disconnected':c.store.update(e['id'],connected=False)
    elif block=='superseded':c.store.update(t['id'],superseded=True)
    else:
        for d in ds:c.store.update(d['id'],status='deferred')
    with pytest.raises(ValueError):c.retry(t['id'],failed_dossiers_only=True)
    assert not c.store.get(t['id']).get('dossier_repair_used')


def test_structured_errors_reject_dossier_and_support_scope():
    out={'findings':[{'dossier_id':'a','observation_ids':[],'judgment':'확인'},
                    {'dossier_id':'a','observation_ids':['foreign'],'judgment':'유력'}],
         'next_checks':[{'hypothesis_id':'foreign'}]}
    issues=errors(out,['a','b'],['valid'])
    assert {i['code'] for i in issues}=={'dossier_membership','missing_support','unknown_observation','unknown_check_dossier'}
    assert issues[0]['missing']==['b'] and issues[0]['duplicates']==['a']


def test_other_dossier_source_requires_explicit_shared_scope():
    out={'findings':[{'dossier_id':'a','observation_ids':['source-b'],'judgment':'확인'}]}
    assert errors(out,['a'],['source-a','source-b'],{'a':['source-a']})[0]['code']=='unrelated_observation'
    assert not errors(out,['a'],['source-a','source-b'],{'a':['source-a','source-b']})


@pytest.mark.parametrize('change',['superseded','active_attempt'])
def test_model_completion_after_scope_change_is_not_adopted(tmp_path,monkeypatch,change):
    c,cid,e,t,o=setup(tmp_path)
    def model(self,question,pack,role):
        if change=='superseded':c.store.update(t['id'],superseded=True)
        else:
            batch=c.store.list('dossier_batch',cid)[0]
            c.store.update(batch['id'],active_attempt_id='new-attempt')
        out=response(pack);return out,{'output':out}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    finish(c,cid,e,t)
    assert not any(d.get('finding') for d in c.store.list('dossier',cid))
    assert c.store.list('review_diagnostic',cid)[0]['rejection']=='stale_review_scope'
    assert not c.store.list('investigation_job',cid)


def test_repair_extra_jobs_are_capped_at_four(tmp_path,monkeypatch):
    c,cid,e,t,o,ds=failed_case(tmp_path);c.retry(t['id'],failed_dossiers_only=True);t=c.store.get(t['id'])
    def model(self,question,pack,role):
        out=response(pack)
        out['next_checks']=[{'tool':'search','query':str(i),'path':'','reason':'check','hypothesis_id':out['findings'][0]['dossier_id'],'success_condition':'find source'} for i in range(6)]
        return out,{'output':out}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    finish(c,cid,e,t)
    assert len(c.store.list('investigation_job',cid))==4
    b=next(b for b in c.store.list('dossier_batch',cid) if belongs(b,t))
    assert len(b['deferred_checks'])==2


def test_repair_cannot_reserve_model_after_absolute_task_budget(tmp_path,monkeypatch):
    from workbench.controller import Controller
    from workbench.store import Store
    c,cid,e,t,o,ds=failed_case(tmp_path);c.retry(t['id'],failed_dossiers_only=True)
    with c.store.tx():
        for i in range(582):c.store.add('review_input',cid,task_id=t['id'],generation=0,pack={})
    c.store.db.close();c=Controller(Store(tmp_path/'case.db'),tmp_path);t=c.store.get(t['id'])
    def unexpected(*args,**kwargs):raise AssertionError('model must not run')
    monkeypatch.setattr('workbench.dossiers.Provider.generate',unexpected)
    finish(c,cid,e,t)
    assert len(c.store.list('review_input',cid))==582
    assert next(b for b in c.store.list('dossier_batch',cid) if belongs(b,t))['status']=='failed'


def test_repair_job_admission_respects_lifetime_reserve(tmp_path,monkeypatch):
    c,cid,e,t,o,ds=failed_case(tmp_path);c.retry(t['id'],failed_dossiers_only=True);t=c.store.get(t['id'])
    with c.store.tx():
        for i in range(75):c.store.add('investigation_job',cid,task_id=t['id'],generation=0,status='done')
    def model(self,question,pack,role):
        out=response(pack)
        out['next_checks']=[{'tool':'search','query':str(i),'reason':'check',
            'hypothesis_id':out['findings'][0]['dossier_id'],'success_condition':'find source'} for i in range(3)]
        return out,{'output':out}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    finish(c,cid,e,t)
    assert len(c.store.list('investigation_job',cid))==76
    b=next(b for b in c.store.list('dossier_batch',cid) if belongs(b,t))
    assert len(b['job_ids'])==1 and len(b['deferred_checks'])==2


def test_recovery_allowance_survives_store_reopen_and_generation_change(tmp_path):
    from workbench.controller import Controller
    from workbench.store import Store
    c,cid,e,t,o,ds=failed_case(tmp_path);c.retry(t['id'],failed_dossiers_only=True)
    c.store.update(t['id'],status='partial',retry_generation=2)
    c.store.db.close()
    reopened=Controller(Store(tmp_path/'case.db'),tmp_path)
    with pytest.raises(ValueError,match='이미 요청'):reopened.retry(t['id'],failed_dossiers_only=True)
    assert reopened.store.get(t['id'])['retry_generation']==2


@pytest.mark.parametrize('kind',['review_input','review_diagnostic'])
def test_review_diagnostics_are_immutable_after_reopen(tmp_path,kind):
    from workbench.store import Store
    path=tmp_path/'immutable.db';store=Store(path)
    original=store.add(kind,'case',pack={'observations':['original']},rejected_output={'reason':'original'})
    store.db.close();store=Store(path)
    with pytest.raises(ValueError,match='불변 기록'):
        store.update(original['id'],pack={'observations':['replacement']},rejected_output=None)
    assert store.get(original['id'])==original
