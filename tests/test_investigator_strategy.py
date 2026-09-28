import copy
import json
import httpx
import pytest
from pydantic import ValidationError
from workbench.models import ProviderConfig
from workbench.provider import Provider
from workbench.investigator import consult
from workbench.evidence_selection import order, audit
from workbench.runtime_contract import binding
from workbench.procedures import identity, instructions, snapshot

CONFIG={'protocol':'ollama','base_url':'http://127.0.0.1:11434','model':'fixture'}


def provider(config=None, metadata=None, content=None):
    result=Provider({**CONFIG,**(config or {})});result.client.close();requests=[]
    def response(request):
        body=json.loads(request.content);requests.append((request.url.path,body))
        if request.url.path=='/api/show':return httpx.Response(200,json=metadata or {})
        return httpx.Response(200,json={'done_reason':'stop','message':{
            'content':json.dumps(content or {'summary':'fixture','claims':[]}),
            'thinking':'Private reasoning must not enter the receipt'},'eval_count':17,'prompt_eval_count':50})
    result.client=httpx.Client(transport=httpx.MockTransport(response))
    return result,requests


def test_defaults_keep_generation_limits_but_enable_guided_strategy():
    config=ProviderConfig(**CONFIG)
    assert (config.think,config.num_ctx,config.num_predict)==('off',32768,4000)
    assert config.investigation_strategy=='guided'


def test_truncated_output_error_retains_generation_metadata():
    p=Provider({**CONFIG,'num_predict':512,'num_ctx':4096})
    p.client.close()
    p.client=httpx.Client(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'done_reason':'length','message':{'content':'{"summary":"partial"}'},
        'prompt_eval_count':123,'eval_count':512,'total_duration':9000})))
    from workbench.provider import ModelOutputError
    with pytest.raises(ModelOutputError) as caught:
        p.generate('fixture',{'target_os':'linux','observations':[]},role='investigator')
    error=caught.value
    assert error.category=='output_budget'
    assert error.metadata['usage']['eval_count']==512
    assert error.metadata['generation_settings']['num_predict']==512
    assert error.metadata['prompt_characters']>0
    assert isinstance(error.metadata['elapsed_seconds'],float)


@pytest.mark.parametrize('change',[
    {'num_ctx':True},{'num_ctx':131073},{'num_predict':0},{'num_predict':32768},
    {'assistant_num_predict':16384},{'temperature':float('nan')},
    {'investigator':'hermes'},{'think':True},{'protocol':'openai_compatible','think':'on'},
])
def test_unsafe_or_unsupported_settings_fail(change):
    with pytest.raises(ValidationError):ProviderConfig(**{**CONFIG,**change})


@pytest.mark.parametrize('mode,value',[('off',False),('on',True),('high','high'),('xhigh','xhigh')])
def test_thinking_settings_are_explicit_and_receipts_do_not_store_trace(mode,value):
    p,requests=provider({'think':mode,'temperature':0.2,'num_ctx':65536,'num_predict':8192},
        metadata={'thinking':{'values':[False,True,'high','xhigh']}},
        content={'summary':'fixture','claims':[],'tool_calls':[]})
    output,receipt=p.generate('fixture',{'target_os':'linux','observations':[]},role='investigator')
    payload=requests[-1][1]
    assert payload['think']==value
    assert payload['options']=={'temperature':0.2,'num_ctx':65536,'num_predict':8192}
    assert receipt['generation_settings']['think']==mode and receipt['usage']['eval_count']==17
    assert 'Private reasoning' not in json.dumps(receipt)
    assert p.client.is_closed and output['tool_calls']==[]


def test_auto_does_not_claim_effective_thinking_mode():
    p,requests=provider({'think':'auto'})
    _,receipt=p.generate('fixture',{'observations':[]})
    assert len(requests)==1 and 'think' not in requests[0][1]
    assert receipt['generation_settings']['think']=='auto'


def test_named_thinking_level_is_not_silently_downgraded():
    p,requests=provider({'think':'high'},metadata={'thinking':{'values':[True,False]}})
    with pytest.raises(ValueError,match='지원'):p.generate('fixture',{})
    assert [path for path,_ in requests]==['/api/show'] and p.client.is_closed


def test_legacy_ollama_thinking_capability_accepts_on():
    p,requests=provider({'think':'on'},metadata={'capabilities':['completion','thinking']})
    p.generate('fixture',{})
    assert requests[-1][1]['think'] is True


def test_assistant_budgets_and_openai_compatible_are_not_misreported():
    p,requests=provider({'assistant_num_ctx':8192,'assistant_num_predict':512})
    _,receipt=p.generate('fixture',{})
    assert requests[-1][1]['options']['num_ctx']==8192
    assert receipt['generation_settings']['num_predict']==512


def test_openai_compatible_does_not_send_ollama_controls():
    p=Provider({**CONFIG,'protocol':'openai_compatible','temperature':0.3});p.client.close();requests=[]
    def respond(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"summary":"fixture","claims":[]}'}}],
            'usage':{'completion_tokens':12,'prompt_tokens':20}})
    p.client=httpx.Client(transport=httpx.MockTransport(respond))
    _,receipt=p.generate('fixture',{})
    assert 'think' not in requests[0] and 'num_ctx' not in requests[0] and 'options' not in requests[0]
    assert requests[0]['temperature']==0.3 and requests[0]['max_tokens']==2500
    assert receipt['generation_settings']['num_ctx'] is None


def test_guidance_is_os_specific_and_baseline_does_not_receive_it():
    assert 'cron/systemd' in instructions('linux') and 'WOW64' not in instructions('linux')
    assert 'WOW64' in instructions('windows') and 'cron/systemd' not in instructions('windows')
    assert instructions('windows','baseline')==''
    assert len(identity()['sha256'])==64 and not identity('baseline')['enabled']
    assert isinstance(snapshot()[0],tuple)
    plan={'summary':'fixture','claims':[],'tool_calls':[]}
    p,requests=provider({'investigation_strategy':'baseline'},content=plan)
    _,receipt=p.generate('fixture',{'observations':[]},role='investigator')
    assert 'Evidence-driven investigation procedure v1' not in requests[-1][1]['messages'][0]['content']
    assert receipt['procedures'] is None


@pytest.mark.parametrize('change',[{'think':'on'},{'num_ctx':65536},{'num_predict':8192},
    {'assistant_num_ctx':32768},{'temperature':0.2},{'investigation_strategy':'baseline'}])
def test_runtime_binding_pins_every_behavior_setting(change):
    assert binding('code',CONFIG)['fingerprint']!=binding('code',{**CONFIG,**change})['fingerprint']
    assert binding('code',CONFIG)['fingerprint']==binding('code',ProviderConfig(**CONFIG).model_dump())['fingerprint']


def test_native_boundary_only_receives_detached_json_and_returns_proposals():
    pack={'target_os':'linux','observations':[{'id':'o','fields':{'path':'/example'}}]}
    original=copy.deepcopy(pack)
    class Fake:
        def __init__(self,config):assert config['investigator']=='native'
        def generate(self,question,pack,role):
            pack['observations'][0]['fields']['path']='mutated copy'
            return {'tool_calls':[{'tool':'search','query':'example'}]}, {'usage':{}}
    result,receipt=consult(CONFIG,'question',pack,'investigator',Fake)
    assert pack==original and result['tool_calls'][0]['tool']=='search'
    assert receipt['investigator_contract']['adapter']=='native'
    assert receipt['investigator_contract']['authority'].startswith('proposals_only')
    with pytest.raises(ValueError,match='외부 조사자'):
        consult({**CONFIG,'investigator':'hermes'},'question',pack,'investigator',Fake)


def test_windows_boundary_rejects_linux_or_shell_tool_proposals():
    class Fake:
        def __init__(self,config):pass
        def generate(self,*args,**kwargs):return {'next_checks':[{'tool':'read_file','path':'/etc/passwd'}]},{}
    with pytest.raises(ValueError,match='허용하지'):
        consult(CONFIG,'question',{'target_os':'windows','observations':[]},'judgment',Fake)


def observation(oid,kind='linux_authentication',path='/var/log/auth',evidence_id='e'):
    return {'id':oid,'evidence_id':evidence_id,'type':kind,'timestamp':None,'source_location':path,
        'fields':{'path':path,'excerpt':'fixture'}}


def test_selection_balances_types_sources_contrary_and_unpresented():
    items=[observation(f'metadata-{i:03}','filesystem_time',f'/boot/{i}') for i in range(500)]
    items += [observation('support'),observation('contrary',path='/var/log/audit'),observation('new',path='/var/log/new')]
    h={'id':'h','expected_source_types':['linux_authentication'],'supporting_evidence_ids':['support'],
       'refuting_evidence_ids':['contrary']}
    ranked,reasons=order(items,[h],preferred=['support'],presented=['support'])
    ids=[o['id'] for o in ranked]
    assert {'contrary','new','support'}.issubset(ids[:8])
    assert len(ids)==len(set(ids))==len(items)
    assert reasons['contrary']=='contrary_reference_unverified'
    assert ids==[o['id'] for o in order(list(reversed(items)),[h],['support'],['support'])[0]]
    info=audit(items,ranked[:8],reasons,[h],['support'])
    assert info['omitted']==len(items)-8 and info['not_previously_presented']==7


def test_selection_rotates_past_previously_presented_not_reviewed():
    items=[observation(f'event-{i:03}') for i in range(250)]
    first=order(items)[0][:10]
    second=order(items,presented=[o['id'] for o in first])[0][:10]
    assert len({o['id'] for o in second}-{o['id'] for o in first})>=5


def test_evidence_pack_keeps_evidence_scope_and_discloses_omissions(tmp_path):
    from workbench.store import Store
    from workbench.controller import Controller
    from workbench.investigation import evidence_pack
    c=Controller(Store(tmp_path/'case.db'),tmp_path);cid=c.create('fixture','','standard')['id']
    e=c.store.add('evidence',cid,connected=True,path='one')
    other=c.store.add('evidence',cid,connected=True,path='two')
    ids=[]
    for i in range(90):
        ids.append(c.store.add('observation',cid,evidence_id=e['id'],type='linux_authentication',timestamp=None,
            source_location=str(i),fields={'path':'/var/log/auth','excerpt':str(i)})['id'])
    foreign=c.store.add('observation',cid,evidence_id=other['id'],type='linux_authentication',timestamp=None,source_location='foreign',fields={})
    pack=evidence_pack(c,cid,preferred=[foreign['id']],evidence_id=e['id'])
    assert foreign['id'] not in {o['id'] for o in pack['observations']}
    assert pack['selection_is_partial'] and pack['selection_audit']['omitted']>0
    assert set(x['id'] for x in pack['selection_audit']['selected'])=={o['id'] for o in pack['observations']}


def test_ranked_baseline_does_not_silently_drop_after_one_hundred():
    from workbench.investigation import ranked
    assert len(ranked([observation(str(i)) for i in range(251)]))==251


def test_settings_roundtrip_normalizes_legacy_and_blocks_active_changes(tmp_path):
    from fastapi.testclient import TestClient
    from workbench.api import create_app
    app=create_app(tmp_path/'data',tmp_path,start_worker=False)
    c=app.state.controller;c.store.add('config','',provider=CONFIG)
    with TestClient(app) as client:
        defaults=client.get('/api/settings').json()
        assert defaults['think']=='off' and defaults['num_ctx']==32768
        updated={**defaults,'think':'on','num_ctx':65536}
        headers={'X-Requested-With':'frontier'}
        assert client.put('/api/settings',json=updated,headers=headers).status_code==200
        assert client.get('/api/settings').json()==updated
        cid=c.create('active fixture','','standard')['id'];c.store.update(cid,status='running')
        assert client.put('/api/settings',json=defaults,headers=headers).status_code==400
        assert client.get('/api/settings').json()==updated


def test_synthesis_selection_includes_explicit_contrary_other_type(tmp_path,monkeypatch):
    from workbench.case_synthesis import tick
    from workbench.investigation import seed
    from test_dossiers import setup
    c,cid,e,t,o=setup(tmp_path);seed(c,cid,e['id'])
    other=c.store.add('observation',cid,evidence_id=e['id'],type='linux_tool_result',timestamp=None,
        source_location='fixture contrary',fields={'path':'/fixture/contrary'})
    h=c.store.list('hypothesis',cid)[0]
    c.store.update(h['id'],refuting_evidence_ids=[other['id']])
    seen=[]
    def fake(self,question,pack,role):
        seen.append(pack)
        return {'summary':'fixture','findings':[{'dossier_id':h['id'],'title':'범위 미확인',
            'judgment':'미확인','reason':'자료 불충분','observation_ids':[other['id']]}],
            'supporting_evidence_ids':[],'refuting_evidence_ids':[other['id']],'next_checks':[]},{}
    monkeypatch.setattr('workbench.case_synthesis.Provider.generate',fake)
    tick(c,cid,e,t,[])
    assert seen and other['id'] in seen[0]['allowed_observation_ids']
    assert seen[0]['selection_audit']['selected'][0]['reason']=='contrary_reference_unverified'


def test_relay_adds_only_readonly_model_metadata_not_management():
    from workbench.model_relay import ALLOWED
    assert ('POST','api/show') in ALLOWED
    assert all(('POST',path) not in ALLOWED for path in ('api/pull','api/create','api/delete','api/copy'))


def test_comparison_dry_run_is_offline_and_does_not_overwrite(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    script=Path(__file__).resolve().parents[1]/'scripts/compare_investigator_contracts.py'
    if not script.exists():pytest.skip('Scripts are not shipped in the runtime/test image')
    command=[sys.executable,str(script),'--model-url','http://127.0.0.1:1','--model','fixture',
             '--dry-run','--out',str(tmp_path/'comparison')]
    first=subprocess.run(command,capture_output=True,text=True,timeout=10)
    assert first.returncode==0
    saved=(tmp_path/'comparison/comparison.json').read_bytes();plan=json.loads(saved)
    assert plan['status']=='planned' and plan['max_generate_calls']==8 and plan['runs']==[]
    assert subprocess.run(command,capture_output=True,text=True,timeout=10).returncode!=0
    assert (tmp_path/'comparison/comparison.json').read_bytes()==saved
