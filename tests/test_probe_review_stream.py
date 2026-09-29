import json

import pytest

from test_dossiers import setup
from scripts import probe_review_stream as probe


def _database_with_batch(tmp_path):
    controller, case_id, evidence, task, observation = setup(tmp_path)
    store = controller.store
    dossier = store.add('dossier', case_id, task_id=task['id'], evidence_id=evidence['id'],
                        generation=0, status='pending', title='Replay', baseline=False,
                        observation_ids=[observation['id']], total_records=1, group_key='replay')
    batch = store.add('dossier_batch', case_id, task_id=task['id'], evidence_id=evidence['id'],
                      generation=0, status='pending', round=0, attempts=0,
                      dossier_ids=[dossier['id']], job_ids=[], output=None, deferred_checks=[])
    stream = store.add('review_stream', case_id, task_id=task['id'], batch_id=batch['id'], round=0,
                       canonical={'observations': [observation], 'allowed_observation_ids': [observation['id']]},
                       frontier=[], status='active')
    store.add('review_page', case_id, task_id=task['id'], batch_id=batch['id'], stream_id=stream['id'],
              index=0, status='reviewed', included_ids=[observation['id']],
              pack={'observations': [observation]})
    store.update(batch['id'], review_stream_id=stream['id'])
    store.add('receipt', case_id, task_id=task['id'], evidence_id=evidence['id'],
              receipt_type='prior', batch_id=batch['id'])
    controller.store.db.close()
    return tmp_path / 'case.db', case_id, batch['id'], stream['id']


def test_checkpoint_out_and_resume_preserve_stream_and_count_only_new_receipts(tmp_path, monkeypatch):
    database, case_id, batch_id, stream_id = _database_with_batch(tmp_path)

    def fake_finish(controller, cid, evidence, task):
        store = controller.store
        batch = next(b for b in store.list('dossier_batch', cid) if b['id'] == batch_id)
        store.add('receipt', cid, task_id=task['id'], evidence_id=evidence['id'],
                  receipt_type='new', batch_id=batch_id)
        store.update(batch_id, status='input_projection_blocked', review_stream_id=stream_id)

    monkeypatch.setattr('workbench.dossiers.finish', fake_finish)
    checkpoint = tmp_path / 'replay.sqlite3'
    first = probe.run(database, case_id, batch_id, tmp_path / 'first', 1, False,
                      checkpoint_out=checkpoint)
    assert first['source_unchanged'] is True
    assert checkpoint.is_file()
    manifest_path = probe.checkpoint_manifest_path(checkpoint)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest['source_sha256'] == first['source_sha256_before']
    assert manifest['case_id'] == case_id and manifest['batch_id'] == batch_id

    second = probe.run(database, case_id, batch_id, tmp_path / 'second', 1, False,
                       resume_checkpoint=checkpoint)
    assert second['source_unchanged'] is True
    assert second['resumed_from_checkpoint'] == str(checkpoint.resolve())
    assert second['counts']['pages_total'] == 1
    assert second['counts']['pages_reviewed'] == 1
    assert len(second['receipts']) == 1
    assert second['receipts'][0]['type'] == 'new'
    assert probe.digest_file(checkpoint) == manifest['checkpoint_sha256']


def test_resume_checkpoint_manifest_mismatch_fails_closed(tmp_path):
    database, case_id, batch_id, _ = _database_with_batch(tmp_path)
    checkpoint = tmp_path / 'checkpoint.sqlite3'
    source_hash = probe.digest_file(database)
    probe._write_checkpoint(database, checkpoint, database, source_hash,
                            case_id, batch_id, 'input_projection_blocked')
    manifest_path = probe.checkpoint_manifest_path(checkpoint)
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    manifest['source_sha256'] = 'mismatch'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='does not match source/case/batch'):
        probe._validate_checkpoint(checkpoint, database, source_hash, case_id, batch_id)


@pytest.mark.parametrize('category',['output_budget','unsupported_inference'])
def test_output_failure_retry_is_explicit_preserves_stream_and_rejection(tmp_path, monkeypatch, category):
    database, cid, bid, stream_id = _database_with_batch(tmp_path)
    def fail(c, cid, evidence, task):
        c.store.add('review_diagnostic',cid,batch_id=bid,failure_category=category,error='rejected')
        c.store.update(bid,status='failed',review_stream_id=stream_id)
    monkeypatch.setattr('workbench.dossiers.finish',fail)
    checkpoint=tmp_path/'checkpoint.sqlite3'
    probe.run(database,cid,bid,tmp_path/'before',1,False,checkpoint_out=checkpoint)
    with pytest.raises(ValueError,match='explicit --retry-model-failure'):
        probe.run(database,cid,bid,tmp_path/'unapproved',1,False,resume_checkpoint=checkpoint)
    def retry(c,cid,evidence,task):
        assert c.store.get(bid)['review_stream_id']==stream_id
        assert len(c.store.list('review_diagnostic',cid))==1
        record=c.store.list('replay_retry',cid)[0]
        assert record['failure_category']==category and record['test_only']
        c.store.update(bid,status='input_projection_blocked')
    monkeypatch.setattr('workbench.dossiers.finish',retry)
    result=probe.run(database,cid,bid,tmp_path/'after',1,False,resume_checkpoint=checkpoint,retry_model_failure=True)
    assert result['source_unchanged'] and result['external_worker_calls']==0
    assert result['explicit_model_failure_retry']


def test_default_mode_does_not_require_checkpoint(tmp_path, monkeypatch):
    database, case_id, batch_id, stream_id = _database_with_batch(tmp_path)

    def fake_finish(controller, cid, evidence, task):
        controller.store.update(batch_id, status='input_projection_blocked', review_stream_id=stream_id)

    monkeypatch.setattr('workbench.dossiers.finish', fake_finish)
    result = probe.run(database, case_id, batch_id, tmp_path / 'default', 1, False)
    assert result['resumed_from_checkpoint'] is None
    assert result['checkpoint_manifest'] is None
    assert result['model_transport']=='disabled'


def test_local_live_replay_never_installs_external_luna_adapter(tmp_path,monkeypatch):
    database,case_id,batch_id,stream_id=_database_with_batch(tmp_path)
    monkeypatch.setattr('scripts.run_assisted_e2e.install_assisted_provider',
        lambda *a,**kw:pytest.fail('External adapter must never be installed for Ollama-only replay'))
    monkeypatch.setattr('workbench.dossiers.finish',lambda c,cid,e,t:
        c.store.update(batch_id,status='input_projection_blocked',review_stream_id=stream_id))
    result=probe.run(database,case_id,batch_id,tmp_path/'local',1,True,model_transport='ollama')
    assert result['model_transport']=='ollama'
    assert result['model_calls']==0


def test_local_live_replay_rejects_an_inherited_relay(tmp_path,monkeypatch):
    database,case_id,batch_id,_=_database_with_batch(tmp_path)
    monkeypatch.setenv('MODEL_RELAY_URL','https://not-authorized.invalid')
    with pytest.raises(ValueError,match='inherited model relay'):
        probe.run(database,case_id,batch_id,tmp_path/'blocked',1,True,model_transport='ollama')


def test_measured_pressure_checkpoint_can_resume_with_smaller_scope(tmp_path,monkeypatch):
    database,cid,bid,stream_id=_database_with_batch(tmp_path)
    def fail(c,cid,e,t):
        c.store.add('review_diagnostic',cid,batch_id=bid,failure_category='input_context_pressure',
            model_metadata={'prompt_budget':{'exact_input_tokens':32000,'context_tokens_requested':32768,'output_tokens_reserved':4000}})
        c.store.update(bid,status='failed',review_stream_id=stream_id)
    monkeypatch.setattr('workbench.dossiers.finish',fail)
    checkpoint=tmp_path/'pressure.sqlite3'
    probe.run(database,cid,bid,tmp_path/'before',1,False,checkpoint_out=checkpoint)
    def resumed(c,cid,e,t):
        batch=c.store.get(bid)
        assert batch['status']=='pending' and batch['attempts']==0
        assert batch['review_stream_id'] is None and 0<batch['review_input_maximum']<36000
        c.store.update(bid,status='input_projection_blocked')
    monkeypatch.setattr('workbench.dossiers.finish',resumed)
    result=probe.run(database,cid,bid,tmp_path/'after',1,False,resume_checkpoint=checkpoint)
    assert result['source_unchanged'] and result['external_worker_calls']==0
