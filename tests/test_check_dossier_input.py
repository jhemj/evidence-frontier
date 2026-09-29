import json
import sqlite3
from pathlib import Path
import pytest

from workbench.controller import Controller
from workbench.store import Store


def source_db(tmp_path):
    db=tmp_path/'source.sqlite3'; s=Store(db); c=Controller(s,tmp_path)
    case=c.create('dossier probe','','standard',target_os='linux');cid=case['id']
    e=s.add('evidence',cid,path='fixture.E01',signature='fixture',connected=True)
    task=s.add('task',cid,evidence_id=e['id'],cell_id='CELL',action='ai_judgment',status='queued')
    b=s.add('dossier_batch',cid,task_id=task['id'],evidence_id=e['id'],dossier_ids=[],generation=0,
            status='pending',round=0,attempts=0,output=None,job_ids=[],deferred_checks=[])
    return db,cid,b['id']


def snapshot(path):
    db=sqlite3.connect(path);
    try:return db.execute('select id,body from records order by id').fetchall()
    finally:db.close()


def test_dossier_probe_is_copy_only_and_does_not_cross_model_worker_boundary(tmp_path,monkeypatch):
    db,cid,bid=source_db(tmp_path); before=snapshot(str(db)); out=tmp_path/'diagnostic'
    import scripts.check_dossier_input as probe
    called=[]
    monkeypatch.setattr('workbench.dossiers.finish',lambda controller,cid,evidence,task: called.append((cid,task['id'])) or None)
    assert probe.main(str(db),cid,bid,str(out))==0
    assert called and snapshot(str(db))==before
    result=json.loads((out/'diagnostic.json').read_text())
    assert result['model_boundary_reached'] is False
    assert result['external_calls']==0
    assert probe.observation_metrics({'observations':[]})['count']==0
    with pytest.raises(ValueError,match='신규 폴더'):
        probe.main(str(db),cid,bid,str(out))


def test_frozen_fit_loader_uses_isolated_module_alias_and_locator_identity(tmp_path):
    import scripts.check_dossier_input as probe
    runtime=Path(__file__).resolve().parents[1]
    first=probe.load_frozen_fit(runtime,tmp_path)
    second=probe.load_frozen_fit(runtime,tmp_path)
    assert first.__name__!=second.__name__
    pack={'observations':[{'id':'o','source_location':'p','timestamp':'t',
        'fields':{'path':'/x','inode':'7','source_sha256':'sha','partition_offset':0,
                  'snapshot_id':'snap','volume_id':'vol','os_instance':'linux'}}]}
    loc=probe.locators(pack)
    assert ('source_sha256','sha') in loc[0][2] and ('snapshot_id','snap') in loc[0][2]
