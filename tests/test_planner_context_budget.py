from copy import deepcopy
from workbench.review_context import fit, serialize
from workbench.investigation_graph import completed_result_memory
import pytest


def test_completed_history_is_aggregate_bounded_without_dropping_observations():
    observations=[{'id':f'o{i}','type':'linux_command','source_location':'location/'+('x'*1700),
                   'fields':{'excerpt':'exact retained fact','path':f'/source/{i}'}} for i in range(16)]
    history=[{'id':f'j{i}','request':{'tool':'read_file','path':f'/exact/{i}'},
              'result_scope':{'detail':'x'*400,'scope':'y'*400,'status':'partial','omitted':57}} for i in range(12)]
    pack={'observations':deepcopy(observations),'completed_tools':deepcopy(history),'completed_tools_total':15,
          'completed_tools_omitted':3,'selection_audit':{'included':16,'omitted':2000},'allowed_observation_ids':[o['id'] for o in observations]}
    assert len(serialize(pack))>36000
    fit(pack)
    assert len(serialize(pack))<=36000
    assert pack['observations']==observations
    assert pack['completed_tools_omitted']+len(pack['completed_tools'])==15
    assert pack['completed_tools_total']==15
    assert pack['allowed_observation_ids']==[o['id'] for o in observations]
    assert all(row['request']==next(old['request'] for old in history if old['id']==row['id']) for row in pack['completed_tools'])
    assert len(history)==12  # original input/ledger history unchanged


def test_result_memory_retains_failed_empty_and_incomplete_rechecks():
    jobs = [
        {'id': 'search-fresh', 'status': 'ingested', 'request': {'tool': 'search'},
         'observation_ids': ['new-search'], 'result_status': 'succeeded',
         'result_scope': {'status': 'succeeded', 'complete': True}},
        {'id': 'static-empty', 'status': 'ingested', 'request': {'tool': 'static_file',
         'path': '/arbitrary/retained'}, 'observation_ids': [], 'result_status': 'failed',
         'result_scope': {'status': 'failed', 'complete': False, 'error': 'not found'}},
        {'id': 'cron-recheck', 'status': 'ingested', 'request': {'tool': 'read_file',
         'path': '/arbitrary/log'}, 'observation_ids': ['already-seen'],
         'result_status': 'partial', 'result_scope': {'status': 'partial', 'complete': False}},
    ]
    memory = completed_result_memory(jobs, {'already-seen'})
    rows = {row['job_id']: row for row in memory['jobs']}
    assert rows['static-empty']['memory_reason'] == 'failed_or_empty'
    assert rows['cron-recheck']['memory_reason'] == 'incomplete_recheck'
    assert memory['failed_or_empty_retained'] == 1
    assert rows['static-empty']['request_scope']['path'] == '/arbitrary/retained'


@pytest.mark.parametrize('content',['\x00'*6000,'\x01\x02\n\t"\\'*1000,'ordinary text '*1000,'한글 원문 '*2000])
def test_serialized_excerpt_budget_preserves_source_identity(content):
    from workbench.investigation import compact_observation
    original={'id':'o1','type':'linux_tool_result','timestamp':None,'source_location':'original byte range',
              'fields':{'path':'/synthetic/renamed-file','artifact_path':'RUN-fixture/objects/source.bin',
                        'source_sha256':'a'*64,'excerpt':content,'byte_offset':123,'image_file_byte_offset':123,
                        'inode':47,'partition_offset':4096}}
    before=deepcopy(original);o=compact_observation(original)
    assert len(serialize(o['fields']['excerpt']))<=6000
    assert content.startswith(o['fields']['excerpt']) and o['fields']['excerpt_truncated']
    assert original==before
    for key in ('path','artifact_path','source_sha256','byte_offset','image_file_byte_offset','inode','partition_offset'):
        assert o['fields'][key]==original['fields'][key]
    assert o['context_request']['path']==original['fields']['path']
    assert o['context_request']['inode']==47 and o['context_request']['partition_offset']==4096


def test_escaped_source_does_not_evict_other_leads_from_planner_pack(tmp_path):
    from workbench.controller import Controller
    from workbench.store import Store
    from workbench.investigation import evidence_pack,seed
    c=Controller(Store(tmp_path/'case.db'),tmp_path);s=c.store
    cid=c.create('Encoded source budget','','standard')['id']
    e=s.add('evidence',cid,path='fixture.E01',signature='fixture',connected=True);seed(c,cid,e['id'])
    binary=s.add('observation',cid,evidence_id=e['id'],type='linux_tool_result',timestamp=None,
        source_location='retained source',fields={'path':'/fixture/random-name','excerpt':'\x00'*6000,
                                                'artifact_path':'objects/source.bin','source_sha256':'a'*64})
    related=[]
    for i in range(8):
        o=s.add('observation',cid,evidence_id=e['id'],type='linux_command',timestamp=None,
            source_location=f'event {i}',fields={'path':f'/fixture/event-{i}','excerpt':f'recorded event {i}',
                                                'rule_id':'synthetic_pattern','stage':'record'})
        related.append(o['id'])
    pack=evidence_pack(c,cid,preferred=[binary['id']]+related,evidence_id=e['id'],focus=[4])
    ids=[o['id'] for o in pack['observations']]
    assert binary['id'] in ids and set(related).issubset(ids)
    fit(pack)
    assert len(serialize(pack))<=36000
    assert [o['id'] for o in pack['observations']]==ids
    assert s.get(binary['id'])['fields']['excerpt']=='\x00'*6000
