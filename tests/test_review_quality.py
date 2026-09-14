from fastapi.testclient import TestClient
from test_dossiers import setup
from workbench.api import create_app
from workbench.coverage_map import summarize, gap_checks
from workbench.dossiers import finish
from workbench.review_validation import errors, check_errors


def test_final_pack_is_bounded_even_when_all_sources_are_protected():
    import json
    from workbench.review_context import fit
    observations=[{'id':str(i),'source_location':'image:/log','fields':{'excerpt':'x'*6000},
        'context_request':{'tool':'read_file','path':'/log','byte_offset':i*6000}} for i in range(22)]
    pack={'observations':observations,'allowed_observation_ids':[str(i) for i in range(22)]}
    fit(pack)
    assert len(json.dumps(pack,ensure_ascii=False))<=36000
    assert len(pack['observations'])==22
    assert all(o['fields']['excerpt_truncated'] and o['context_request'] for o in pack['observations'])


def test_static_configuration_cannot_confirm_execution():
    f={'dossier_id':'d','judgment':'확인','observation_ids':['o'],'stages':[
        {'stage':'execution','judgment':'확인','statement':'ran','observation_ids':['o']}]}
    result=errors({'findings':[f]},['d'],['o'],{'d':['o']},{'o':{'type':'linux_configuration'}})
    assert any(i['code']=='static_facts_not_behavior' for i in result)
    result=errors({'findings':[f]},['d'],['o'],{'d':['o']},{'o':{'type':'linux_tool_result'}})
    assert any(i['code']=='static_facts_not_behavior' for i in result)


def test_replacement_decoding_never_advances_byte_locator_from_text_length():
    from workbench.investigation import compact_observation
    o={'id':'o','type':'linux_literal_match','timestamp':None,'source_location':'image:/log',
        'fields':{'path':'/log','artifact_path':'RUN-a/source','byte_offset':10,
            'locator_basis':'gzip decompressed bytes','excerpt':'\ufffd'*7000}}
    compact=compact_observation(o)
    assert compact['context_request']['tool']=='read_source'
    assert compact['context_request']['byte_offset']==10
    assert compact['context_request']['byte_length']>8192


def test_failed_empty_search_cannot_refute_and_unassessed_is_unknown():
    checks=[{'id':'job','status':'partial','observation_ids':[]}]
    out={'check_assessments':[{'check_id':'job','outcome':'refutes','reason':'no hits','observation_ids':[]}]}
    assert check_errors(out,checks,{})
    out={};assert not check_errors(out,checks,{})
    assert out['check_assessments'][0]['outcome']=='inconclusive'


def test_blind_review_then_comparison_preserves_distinct_inputs(tmp_path,monkeypatch):
    c,cid,e,t,o=setup(tmp_path);packs=[]
    def model(self,question,pack,role):
        packs.append(pack)
        fs=[{'dossier_id':d['id'],'title':'source fact','judgment':'미확인','reason':'limited','observation_ids':d['observation_ids']} for d in pack['required_dossiers']]
        return {'summary':'review','findings':fs,'next_checks':[]},{'output':{'findings':fs}}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    for _ in range(8):
        if finish(c,cid,e,t):break
    assert packs[0]['review_mode']=='blind_source_review' and packs[0]['previous_assessment'] is None
    assert packs[1]['review_mode']=='compare_and_falsify' and packs[1]['previous_assessment']
    assert len(c.store.list('review_input',cid))==len(packs)


def test_coverage_keeps_parser_gaps_separate_and_bounds_followups():
    files=[{'path':'/etc/cron.d/job'+str(i),'partition_offset':0,'category':'config','status':'deferred','size':20,'bytes_scanned':0} for i in range(20)]
    cm=summarize(files,[])
    assert cm['critical_gap_count']==20 and cm['families']['config']['read']==0
    assert len(gap_checks([{'type':'linux_environment','fields':{'coverage_map':cm}}]))==8


def test_unchanged_poll_skips_heavy_snapshot_but_message_invalidates(tmp_path,monkeypatch):
    app=create_app(tmp_path/'data',tmp_path,start_worker=False)
    with TestClient(app) as client:
        cid=client.post('/api/cases',headers={'X-Requested-With':'frontier'},json={'name':'poll'}).json()['id']
        first=client.get('/api/cases/'+cid).json()
        revision=first['view_revision'];controller=app.state.controller
        original=controller.snapshot
        monkeypatch.setattr(controller,'snapshot',lambda *a:(_ for _ in ()).throw(AssertionError('heavy snapshot')))
        small=client.get('/api/cases/'+cid,params={'since':revision})
        assert small.json()['unchanged'] and len(small.content)<4000
        monkeypatch.setattr(controller,'snapshot',original)
        controller.store.add('message',cid,role='assistant',text='updated')
        assert client.get('/api/cases/'+cid,params={'since':revision}).json()['view_revision']!=revision
