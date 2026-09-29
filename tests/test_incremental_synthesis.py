from workbench.case_synthesis import review_revision, tick
from workbench.controller import Controller
from workbench.hypothesis_ledger import apply
from workbench.store import Store
import pytest


def _case(tmp_path):
    c=Controller(Store(tmp_path/'case.db'),tmp_path)
    case=c.create('incremental','','standard');cid=case['id']
    c.store.update(cid,status='running')
    e=c.store.add('evidence',cid,path='synthetic',signature='sig',connected=True)
    t=c.store.add('task',cid,evidence_id=e['id'],action='ai_judgment',retry_generation=0,status='running')
    o=c.store.add('observation',cid,evidence_id=e['id'],type='linux_detection',timestamp=None,
                  source_location='synthetic:1',fields={'rule_id':'candidate','path':'/tmp/x'})
    c.store.add('config','',provider={'model':'local','base_url':'http://localhost:11434','protocol':'ollama'})
    plan=c.store.add('investigation_plan',cid,task_id=t['id'],generation=0)
    h=apply(c.store,cid,e['id'],{'action':'create','title':'dynamic question','judgment':'유력',
        'reasoning':'new reviewed source','supporting_evidence_ids':[o['id']]},[o['id']],
        task_id=t['id'],source_plan_id=plan['id'])
    d=c.store.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],generation=0,status='reviewed',
        observation_ids=[o['id']],finding={'dossier_id':'placeholder','observation_ids':[o['id']],
        'judgment':'미확인','reason':'bounded'})
    return c,cid,e,t,o,h,d


def test_incremental_dynamic_only_marks_record_and_reuses_current_revision(tmp_path,monkeypatch):
    c,cid,e,t,o,h,d=_case(tmp_path)
    d=c.store.update(d['id'],finding={'dossier_id':d['id'],'observation_ids':[o['id']],
        'judgment':'유력','reason':'new source'})
    calls=[]
    def model(*args,**kwargs):
        calls.append(1)
        return {'findings':[{'dossier_id':h['id'],'title':'bounded','judgment':'미확인',
            'reason':'execution unknown','observation_ids':[o['id']],'stages':[],
            'alternatives':[],'remaining_checks':[]}],
            'supporting_evidence_ids':[o['id']],'refuting_evidence_ids':[],'next_checks':[]},{}
    monkeypatch.setattr('workbench.case_synthesis.consult',model)
    assert tick(c,cid,e,t,[d],dynamic_only=True,incremental=True) is False
    record=c.store.list('case_synthesis',cid)[0]
    assert record['incremental'] is True
    assert c.store.get(h['id'])['text']=='dynamic question'
    assert record['review_revision']==review_revision([d],[o['id']])
    before=len(calls)
    assert tick(c,cid,e,t,[d],dynamic_only=True,incremental=True) is True
    assert len(calls)==before


def test_non_dynamic_only_checkpoint_does_not_call_model(tmp_path,monkeypatch):
    c,cid,e,t,o,h,d=_case(tmp_path)
    c.store.update(h['id'],hypothesis_kind='coverage_domain')
    monkeypatch.setattr('workbench.case_synthesis.consult',lambda *a,**k: (_ for _ in ()).throw(AssertionError('unexpected model call')))
    assert tick(c,cid,e,t,[d],dynamic_only=True,incremental=True) is True
    assert not c.store.list('case_synthesis',cid)


def test_review_revision_changes_when_adopted_finding_changes(tmp_path):
    c,cid,e,t,o,h,d=_case(tmp_path)
    first=review_revision([d],[o['id']])
    changed=c.store.update(d['id'],finding={**d['finding'],'reason':'counterevidence'})
    assert review_revision([changed],[o['id']])!=first


def test_wave_hook_private_lock_only_after_new_review_and_not_paused(tmp_path,monkeypatch):
    from workbench.dossiers import _incremental_checkpoint, digest
    c,cid,e,t,o,h,d=_case(tmp_path)
    t=c.store.update(t['id'],review_policy='autonomous-v1')
    calls=[]
    def checkpoint(worker,*args,**kwargs):
        assert c.model_lock.locked() and not worker.model_lock.locked()
        assert kwargs=={'dynamic_only':True,'incremental':True}
        calls.append(1)
    monkeypatch.setattr('workbench.case_synthesis.tick',checkpoint)
    with c.model_lock:
        _incremental_checkpoint(c,cid,e,t,{},None)
        _incremental_checkpoint(c,cid,e,t,{d['id']:digest(d['finding'])},None)
        _incremental_checkpoint(c,cid,e,t,{}, {'complete':True})
        c.store.update(cid,status='pause_requested')
        _incremental_checkpoint(c,cid,e,t,{},None)
    assert len(calls)==1


def test_dynamic_without_related_reviewed_sources_is_not_summarized(tmp_path,monkeypatch):
    c,cid,e,t,o,h,d=_case(tmp_path)
    d=c.store.update(d['id'],status='pending')
    monkeypatch.setattr('workbench.case_synthesis.consult',lambda *a,**k: (_ for _ in ()).throw(AssertionError('unexpected call')))
    assert tick(c,cid,e,t,[d],dynamic_only=True,incremental=True) is True


def test_concurrent_review_change_rejects_synthesis_adoption(tmp_path,monkeypatch):
    c,cid,e,t,o,h,d=_case(tmp_path)
    def model(*args,**kwargs):
        c.store.update(d['id'],finding={**d['finding'],'reason':'new contrary context'})
        return {'findings':[{'dossier_id':h['id'],'title':'old','judgment':'미확인','reason':'old interpretation',
            'observation_ids':[o['id']],'stages':[],'alternatives':[],'remaining_checks':[]}],
            'supporting_evidence_ids':[o['id']],'refuting_evidence_ids':[],'next_checks':[]},{}
    monkeypatch.setattr('workbench.case_synthesis.consult',model)
    assert tick(c,cid,e,t,[d],dynamic_only=True,incremental=True) is False
    assert not c.store.list('case_synthesis',cid)
    assert c.store.list('receipt',cid)[-1]['failure_category']=='stale_scope'


def test_current_report_view_qualifies_changed_review_without_mutating_ledger():
    from workbench.case_synthesis import current_view
    d={'id':'d','task_id':'t','status':'reviewed','observation_ids':['o'],'finding':{'reason':'before'}}
    row={'id':'s','task_id':'t','generation':0,'hypothesis_id':'h','source_ids':['o'],'status':'reviewed',
        'review_revision':review_revision([d],['o']),'finding':{'title':'old title','judgment':'확인','reason':'old reason'}}
    changed={**d,'finding':{'reason':'contrary explanation'}}
    result=current_view([row],dossiers=[changed])[0]
    assert result['status']=='review_changed' and result['finding']['judgment']=='미확인'
    assert row['finding']['judgment']=='확인' and row['status']=='reviewed'


@pytest.mark.parametrize('status,called',[('input_projection_blocked',True),('model_failed',True),('pending',False)])
def test_terminal_followup_is_an_explicit_gap_not_an_infinite_dependency(tmp_path,monkeypatch,status,called):
    c,cid,e,t,o,h,d=_case(tmp_path)
    follow=c.store.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],generation=0,
        status=status,origin_hypothesis_id=h['id'],observation_ids=[o['id']],finding=None)
    calls=[]
    def model(config,question,pack,**kwargs):
        calls.append(pack)
        return {'findings':[{'dossier_id':h['id'],'title':'bounded','judgment':'미확인',
            'reason':'Unreviewed scope remains','observation_ids':[o['id']],'stages':[],
            'alternatives':[],'remaining_checks':['Unreviewed followup']}],
            'supporting_evidence_ids':[o['id']],'refuting_evidence_ids':[],'next_checks':[]},{}
    monkeypatch.setattr('workbench.case_synthesis.consult',model)
    tick(c,cid,e,t,[d,follow],dynamic_only=True,incremental=True)
    assert bool(calls)==called
    assert c.store.get(follow['id'])['status']==status
    if called:
        assert calls[0]['scope']['unreviewed_units']==1
        assert calls[0]['scope']['terminal_followup_review_gaps']==[{'dossier_id':follow['id'],'status':status}]
        assert o['id'] in [row['id'] for row in calls[0]['observations']]
        assert c.store.list('case_synthesis',cid)[0]['finding']['judgment']=='미확인'
