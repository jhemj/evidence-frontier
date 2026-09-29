from copy import deepcopy
import pytest
from workbench.presentation_claims import bind,representatives


def fixture(typ='linux_cron_call',name='/synthetic/service'):
    obs={'o':{'id':'o','evidence_id':'e','type':typ,'fields':{'command':name,'path':'/log','inode':17,'partition_offset':0}}}
    f={'dossier_id':'d','title':'InventedBrand 악성 프로그램이 성공적으로 실행됨','card_summary':'A made-up attribution.',
       'judgment':'확인','observation_ids':['o'],'stages':[],
       'fact_assertions':[{'observation_id':'o','pointer':'/fields/command','operator':'equals','value':name}]}
    return f,obs


@pytest.mark.parametrize('name',['/unknown/tool','/familiar-brand/service','10.11.12.13:3210'])
def test_names_do_not_supply_product_actor_malice_or_network_boundary(name):
    f,obs=fixture(name=name);original=deepcopy(f)
    out=bind(f,obs)
    assert name in out['title'] and '예약작업 호출 기록' in out['title']
    assert 'InventedBrand' not in out['title'] and '악성' not in out['title']
    assert '성공·등록 주체·승인을 입증하지 않습니다' in out['card_summary']
    assert out['model_narrative']['title']==original['title']
    assert out['fact_assertions']==original['fact_assertions'] and f==original


def test_documentation_literal_is_not_execution_record():
    f,obs=fixture('linux_literal_match')
    out=bind(f,obs)
    assert '인용 원문' in out['title'] and '호출 기록' not in out['title']


def test_cannot_attach_unselected_or_foreign_subject():
    f,obs=fixture();f['fact_assertions']=[]
    assert bind(f,obs)['display_binding']['status']=='unbound'
    f,obs=fixture();f['fact_assertions'][0]['observation_id']='foreign'
    with pytest.raises(ValueError):bind(f,obs)


def test_representatives_keep_occurrences_objections_and_conflicts():
    f,obs=fixture();a=bind(f,obs)
    b={**deepcopy(a),'dossier_id':'d2','judgment':'미확인','open_objections':[{'id':'ob','observation_ids':['o']}]}
    rows=representatives([a,b],obs)
    assert len(rows)==1 and len(rows[0]['occurrence_claims'])==2
    assert rows[0]['judgment']=='미확인' and rows[0]['open_objections']
    c=deepcopy(obs['o']);c.update(id='different',evidence_id='another');obs['different']=c
    alternate=deepcopy(a);alternate['dossier_id']='d3';alternate['display_binding']['fact']['observation_id']='different'
    assert len(representatives([a,alternate],obs))==2


def test_no_forced_three_highlights_and_no_title_based_merge():
    f,obs=fixture();a=bind(f,obs)
    assert len(representatives([a],obs))==1
    legacy={'dossier_id':'other','title':a['title']}
    assert len(representatives([a,legacy],obs))==2


@pytest.mark.parametrize('command',['/etc/init.d/splx status','/renamed/unknown status'])
@pytest.mark.parametrize('kind',['linux_authentication','linux_session'])
def test_selected_auth_excerpt_yields_literal_command_not_product_identity(command,kind):
    f,obs=fixture(kind)
    literal='Sep 1 10:00:00 host sudo: agent : PWD=/ ; USER=root ; COMMAND='+command
    obs['o']['fields']['excerpt']=literal
    f['fact_assertions']=[{'observation_id':'o','pointer':'/fields/excerpt','operator':'equals','value':literal}]
    f['title']='Splunk 상태 확인 완료'
    out=bind(f,obs)
    assert out['title']=='sudo 명령 기록 · '+command
    assert out['display_binding']['projection']['account']=='agent'
    assert 'Splunk' not in out['title'] and out['fact_assertions']==f['fact_assertions']
    obs['o']['type']='linux_configuration'
    assert not bind(f,obs)['title'].startswith('sudo 명령 기록')
