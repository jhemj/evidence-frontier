from copy import deepcopy
from test_dossiers import setup
from workbench.dossiers import finish, input_projection_failure
from workbench.review_context import InputBudgetError


def test_single_source_input_failure_never_consumes_model_attempt_or_changes_source(tmp_path, monkeypatch):
    c,cid,e,t,o=setup(tmp_path)
    monkeypatch.setattr('workbench.review_context.fit',lambda *a,**k: (_ for _ in ()).throw(InputBudgetError('synthetic identity floor')))
    monkeypatch.setattr('workbench.review_stream.fit',lambda *a,**k: (_ for _ in ()).throw(InputBudgetError('synthetic identity floor')))
    monkeypatch.setattr('workbench.dossiers.consult',lambda *a,**k: (_ for _ in ()).throw(AssertionError('model must not run')))
    before=deepcopy(c.store.get(o['id']))
    assert finish(c,cid,e,t) is None
    batch=c.store.list('dossier_batch',cid)[0]
    assert batch['status']=='input_projection_blocked' and batch['attempts']==0
    assert c.store.get(batch['dossier_ids'][0])['status']=='input_projection_blocked'
    assert not c.store.list('review_input',cid)
    assert c.store.get(o['id'])==before
    diagnostic=c.store.list('review_diagnostic',cid)[0]
    assert diagnostic['model_called'] is False
    assert diagnostic['failure_category']=='input_projection'
    result=finish(c,cid,e,t)
    assert result['complete'] is False and result['status']=='partial'
    assert 'input_projection_incomplete' in result['termination_reasons']
    assert len(c.store.list('review_diagnostic',cid))==1


def test_input_split_is_idempotent_and_keeps_logical_scope_and_attempts(tmp_path):
    c,cid,e,t,o=setup(tmp_path);s=c.store
    ds=[s.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],status='pending',observation_ids=[o['id']]) for _ in range(2)]
    parent=s.add('dossier_batch',cid,task_id=t['id'],evidence_id=e['id'],generation=0,
        status='pending',round=1,attempts=0,dossier_ids=[d['id'] for d in ds],job_ids=[],output=None,deferred_checks=[])
    input_projection_failure(s,cid,t,parent,ValueError('budget'))
    input_projection_failure(s,cid,t,parent,ValueError('budget'))
    assert s.get(parent['id'])['attempts']==0
    assert s.get(parent['id'])['termination']=='input_projection_split'
    children=[b for b in s.list('dossier_batch',cid) if b.get('parent_batch_id')==parent['id']]
    assert len(children)==2 and all(b['attempts']==0 and b['round']==1 for b in children)
    assert {b['dossier_ids'][0] for b in children}=={d['id'] for d in ds}
    assert len(s.list('review_diagnostic',cid))==1
