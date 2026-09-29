import json
import os
import sqlite3
import pytest

from workbench.controller import Controller
from workbench.models import ProviderConfig
from workbench.store import Store


def make_source(tmp_path,target_os='linux'):
    db=tmp_path/'source.sqlite3'; store=Store(db)
    controller=Controller(store,tmp_path)
    case=controller.create('probe','','standard',target_os=target_os); cid=case['id']
    config=ProviderConfig(protocol='ollama',base_url='http://127.0.0.1:11434',model='fixture').model_dump()
    store.update(cid,status='paused',runtime_binding={'provider':config,'model_digest':'digest-fixture','fingerprint':'fixture'})
    evidence=store.add('evidence',cid,path='fixture.E01',signature='fixture',connected=True)
    task=store.add('task',cid,evidence_id=evidence['id'],cell_id='CELL',action=f'{target_os}_investigate',status='queued',retry_generation=0)
    store.add('investigation_run',cid,task_id=task['id'],evidence_id=evidence['id'],version='investigation-graph-2',
              signature='fixture',source_run='RUN-'+'a'*32,model_calls=1,max_model_calls=12,tool_calls=0,
              max_tool_calls=36,challenge_calls=0,max_challenge_calls=10,review_calls=0,max_review_calls=5,
              domain_cursor=1,plan_domain_batch_size=3,consecutive_plan_failures=0,stop_reason=None)
    store.add('investigation_plan',cid,task_id=task['id'],run_id=store.list('investigation_run',cid)[0]['id'],
              revision=0,assessed=True,valid_ids=[],focus=[],output={'summary':'old','claims':[],
              'hypotheses':[],'remaining_questions':[],'tool_calls':[]})
    store.add('config','',provider=config)
    return db,cid


def rows_snapshot(path):
    db=sqlite3.connect('file:'+str(path.resolve())+'?mode=ro',uri=True)
    try:
        return db.execute('select id,body from records order by id').fetchall()
    finally: db.close()


@pytest.mark.parametrize('target_os',['linux','windows'])
def test_probe_is_copy_only_and_preserves_provider_settings(tmp_path,monkeypatch,target_os):
    db,cid=make_source(tmp_path,target_os); out=tmp_path/'probe-out'; before=rows_snapshot(db)
    import scripts.probe_investigator_recovery as probe
    seen={}
    def fake_consult(config,question,pack,role,provider_factory):
        seen.update(config=config,question=question,pack=pack,role=role,
                    digest=os.environ.get('FRONTIER_MODEL_DIGEST'))
        return ({'summary':'fixture proposal','tool_calls':[],'claims':[],
                 'remaining_questions':['미확인'],'hypotheses':[]}, {'usage':{'eval_count':1}})
    monkeypatch.setattr('workbench.investigator.consult',fake_consult)
    monkeypatch.setattr('workbench.investigation_graph.worker_request',
                        lambda *args,**kwargs: pytest.fail('probe must not dispatch worker jobs'))
    monkeypatch.setenv('FRONTIER_MODEL_DIGEST','outer-digest')
    assert probe.main(str(db),cid,str(out))==0
    assert rows_snapshot(db)==before
    assert seen['role']=='investigator' and seen['digest']=='digest-fixture'
    assert seen['config']['num_predict']==4000
    assert (out/'manifest.json').exists() and (out/'proposal.json').exists()
    assert os.environ['FRONTIER_MODEL_DIGEST']=='outer-digest'
    with pytest.raises(ValueError,match='신규 폴더'):
        probe.main(str(db),cid,str(out))


def test_probe_fails_closed_without_verified_original_digest(tmp_path):
    db,cid=make_source(tmp_path)
    store=Store(db); case=store.get(cid); store.update(cid,runtime_binding={'provider':case['runtime_binding']['provider'],'model_digest':'unverified'})
    with pytest.raises(ValueError,match='model_digest'):
        import scripts.probe_investigator_recovery as probe
        probe.main(str(db),cid,str(tmp_path/'out'))
