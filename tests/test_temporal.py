"""Time-search leads are useful context, never fabricated event timestamps."""
from copy import deepcopy
from pathlib import Path
import pytest
from workbench.temporal import TimeContext, file_anchors
from workbench.evidence_semantics import exact_utc_ns


def obs(oid='file', typ='filesystem_entry', timestamp=None, **fields):
    return {'id':oid,'evidence_id':'image','type':typ,'timestamp':timestamp,'source_location':'retained source',
            'fields':{'path':'/opt/example/item','partition_offset':0,'inode':321,**fields}}


def test_precise_ctime_is_reference_not_execution_and_does_not_mutate():
    o=obs(ctime_ns=1782104669000000001,mtime_epoch=1747132834,crtime_epoch=1782104668)
    before=deepcopy(o);t=TimeContext(iter([o])).event(['file'])
    assert o==before and o['timestamp'] is None
    assert t['time_kind']=='file_metadata' and t['confidence']=='context_only'
    assert t['epoch_nanoseconds']=='1782104669000000001'
    assert t['raw']=='2026-06-22T05:04:29.000000001Z'
    assert t['needs_time_followup'] and '행위 시각 미확인' in t['time_label']
    assert [(a['time_type'],a['pointer']) for a in t['anchors']]==[
        ('ctime','/fields/ctime_ns'),('mtime','/fields/mtime_epoch'),('crtime','/fields/crtime_epoch')]


def test_event_and_estimated_clocks_take_priority_without_promoting_metadata():
    exact=obs('exact','linux_command','2026-01-03T00:00:00Z')
    estimated=obs('estimated','linux_cron_call','2026-01-02T00:00:00Z',time_basis='year inferred from rotation filename')
    file=obs(ctime_epoch=1747132834)
    ctx=TimeContext([file,estimated,exact])
    t=ctx.event(['file','exact','estimated'])
    assert t['time_kind']=='occurred' and t['observation_id']=='exact'
    assert ctx.event(['estimated'])['time_kind']=='estimated'
    assert ctx.event(['estimated'])['confidence']=='provisional'
    assert '추정' in ctx.event(['estimated'])['time_label']


def test_saved_assumption_is_not_promoted_to_exact_event_time():
    o=obs(typ='linux_command',timestamp='2026-01-03T00:00:00Z',time_record={'timezone_assumed':True})
    assert TimeContext([o]).event(['file'])['time_kind']=='estimated'


@pytest.mark.parametrize('field,value', [('partition_offset',4096),('os_instance','another'),('volume_id','another'),
    ('snapshot_id','another'),('inode',322),('path','/other/item'),('evidence_id','another')])
def test_same_name_never_crosses_source_identity(field,value):
    clue=obs('clue','linux_detection');file=obs(ctime_epoch=1782104669)
    if field=='evidence_id':file[field]=value
    else:file['fields'][field]=value
    assert TimeContext([clue,file]).event(['clue'])['time_kind']=='unknown'


def test_same_file_link_is_citable_but_does_not_change_claim_or_source():
    clue=obs('clue','linux_detection');file=obs(ctime_epoch=1782104669)
    before=deepcopy([clue,file]);t=TimeContext([clue,file]).event(['clue'])
    assert t['anchors'][0]['observation_id']=='file' and t['anchors'][0]['linked_from']=='clue'
    assert 'inode 대조' in t['anchors'][0]['basis']
    assert t['time_kind']=='file_metadata' and [clue,file]==before
    del clue['fields']['partition_offset'];del file['fields']['partition_offset']
    assert TimeContext([clue,file]).event(['clue'])['time_kind']=='unknown'


@pytest.mark.parametrize('value',[0,'0','NaN','Infinity',-1,'2026-01-01T00:00:00','invalid',True])
def test_invalid_unqualified_or_zero_metadata_does_not_invent_time(value):
    o=obs(ctime=value);o['created_at']='2026-01-01T00:00:00Z'
    assert TimeContext([o]).event(['file'])['time_kind']=='unknown'


def test_host_ingestion_and_generic_metadata_timestamp_are_not_fallbacks():
    o=obs(typ='file_metadata',timestamp='2026-01-01T00:00:00Z',created_at='2026-01-01T00:00:00Z')
    assert TimeContext([o]).event(['file'])['raw'] is None
    arbitrary=obs(typ='linux_detection',ctime_epoch=1782104669)
    assert file_anchors(arbitrary)==[]


def test_windows_change_creation_and_write_stay_distinct():
    file=obs(typ='windows_file',path='C:\\Example\\item',ChangeTime='2026-01-03T00:00:00Z',
             CreationTime='2026-01-02T00:00:00Z',LastWriteTime='2025-01-01T00:00:00Z')
    clue=obs('clue','windows_correlation',path='c:/example/item')
    anchors=TimeContext([clue,file]).event(['clue'])['anchors']
    assert [a['time_type'] for a in anchors]==['ctime','mtime','crtime']
    assert len({a['raw'] for a in anchors})==3


def test_read_file_context_and_old_expanded_metadata_are_supported():
    tool=obs('tool','linux_tool_result',file_context={'ctime':1782104669,'mtime':1747132834})
    assert len(file_anchors(tool))==2
    expanded=obs('expanded','filesystem_time','2026-06-22T05:04:29Z',time_type='ctime_epoch')
    assert file_anchors(expanded)[0]['time_type']=='ctime'
    assert TimeContext([tool,expanded]).event(['tool','expanded'])['anchor_count']==2


def test_new_worker_timeline_retains_identity_nanoseconds_and_limit(monkeypatch):
    from workbench import worker
    source=obs(ctime_ns=1782104669000000001,mtime_epoch=1747132834)
    rows=worker.filesystem_timeline([source])
    assert len(rows)==2
    assert rows[0]['fields']['partition_offset']==0 and rows[0]['fields']['inode']==321
    assert rows[0]['fields']['time_kind']=='file_metadata'
    assert exact_utc_ns(rows[0]['timestamp'])==1782104669000000001
    assert TimeContext([{**r,'id':str(i)} for i,r in enumerate(rows)]).event(['0'])['time_kind']=='file_metadata'
    monkeypatch.setattr(worker,'MAX_EVENTS',1)
    with pytest.raises(ValueError,match='예산'):worker.filesystem_timeline([source])


def test_tsk_partition_byte_identity_is_preserved(monkeypatch):
    from workbench import worker
    monkeypatch.setattr(worker,'partition_inventory',lambda p:{'partitions':[{'offset':4096,'offset_sectors':8}]})
    monkeypatch.setattr(worker,'command',lambda *a,**k:'0|/opt/example/item|321|r/rrwx|0|0|32|1782104669|1782104669|1782104669|0')
    assert worker.filesystem_events(Path('sample.e01'))[0]['fields']['partition_offset']==4096


def test_large_model_context_retains_time_sources_and_locator():
    from workbench.investigation import compact_observation
    o=obs(typ='linux_tool_result',file_context={'ctime_ns':1782104669000000001},
          ctime_epoch=1782104669,os_instance='os',volume_id='vol',snapshot_id='snap',
          time_basis='filesystem metadata',huge={str(i):'x'*100 for i in range(120)})
    before=deepcopy(o);compact=compact_observation(o)
    for key in ('file_context','ctime_epoch','partition_offset','inode','time_basis','volume_id','snapshot_id'):
        assert compact['fields'][key]==o['fields'][key]
    assert 'huge' not in compact['fields'] and o==before


def test_guided_roles_receive_temporal_followup_procedure():
    from workbench.procedures import instructions
    for platform in ('linux','windows'):
        for role in ('investigator','judgment','synthesis','falsifier'):
            text=instructions(platform,role=role)
            assert 'Skill Temporal reconstruction' in text and 'NOT creation time' in text


def test_disconnected_metadata_cannot_date_a_live_card():
    from workbench.triage import project
    clue=obs('clue','linux_detection');metadata=obs(ctime_epoch=1782104669);metadata['evidence_id']='gone'
    doc={'case':{},'evidence':[{'id':'image'},{'id':'gone','connected':False}],
         'task':[{'id':'task'}],'observation':[clue,metadata],
         'dossier':[{'id':'d','task_id':'task','status':'reviewed','finding':{'observation_ids':['clue']}}]}
    assert project(doc)['cards'][0]['event_time']['time_kind']=='unknown'


def test_observation_display_preserves_unsafe_javascript_integer(tmp_path):
    from fastapi.testclient import TestClient
    from workbench.api import create_app
    app=create_app(tmp_path/'api',tmp_path,start_worker=False)
    with TestClient(app) as client:
        controller=app.state.controller;s=controller.store
        cid=controller.create('Synthetic timestamp','','standard')['id']
        o=s.add('observation',cid,type='filesystem_entry',timestamp=None,source_location='synthetic',
                fields={'ctime_ns':1782104669000000001,'excerpt':'<script>not executed</script>'})
        row=client.get(f'/api/cases/{cid}/observations?q='+o['id']).json()['items'][0]
        assert '1782104669000000001' in row['fields_display_json']
        assert s.get(o['id'])['fields']['ctime_ns']==1782104669000000001
        assert 'fields_display_json' not in s.get(o['id'])
    assert 'esc(o.fields_display_json ?? JSON.stringify(o.fields, null, 2))' in Path('dist/app.js').read_text()
