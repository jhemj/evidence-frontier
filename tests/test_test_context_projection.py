from copy import deepcopy
import pytest
from test_purpose_contract_v2 import fixture,job
from workbench.test_context_projection import attach,fit_presented
from workbench.review_context import InputBudgetError


def pack(f):
    return {'required_dossiers':[f[4]],'question_context':{'questions':[f[5]]},'observations':[deepcopy(f[6])]}


def test_legacy_input_stays_unchanged(fixture):
    s,c,e,t,*_=fixture;p=pack(fixture);original=deepcopy(p)
    assert attach(p,s,c,{**t,'test_contract_policy':'legacy'},e,'run')==original


def test_manifest_tracks_actual_post_fit_body_view(fixture):
    s,c,e,t,*_=fixture;p=pack(fixture);calls=[]
    def fit(value):
        calls.append(1);value['observations'][0]['fields'].pop('excerpt',None);return {'bound':True}
    assert fit_presented(p,fit,s,c,t,e,'run')=={'bound':True}
    source=next(x for x in p['test_contract_context']['objects'] if x['ref']['id']==fixture[6]['id'])
    assert source['view']=='metadata' and len(calls)==2


def test_nonconvergent_view_never_transmits(fixture):
    s,c,e,t,*_=fixture;p=pack(fixture);calls=[]
    def fit(value):
        fields=value['observations'][0]['fields'];calls.append(1)
        if 'excerpt' in fields:fields.pop('excerpt')
        else:fields['excerpt']='setting=value'
    with pytest.raises(InputBudgetError):fit_presented(p,fit,s,c,t,e,'run')
    assert len(calls)==3


def test_result_lineage_from_exact_shown_jobs_only(fixture):
    s,c,e,t,*_=fixture;intent,physical,condition=job(fixture);p=pack(fixture)
    p['executed_checks']=[{'id':physical['id'],'contracts':[condition]}]
    attach(p,s,c,t,e,'run')
    assert p['test_contract_context']['result_lineages'][0]['parent_contract_id']==condition['contract_id']
    p['executed_checks']=[];attach(p,s,c,t,e,'run')
    assert p['test_contract_context']['result_lineages']==[]
