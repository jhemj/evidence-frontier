from copy import deepcopy

import pytest

from workbench.review_context import InputBudgetError, model_view_size
from workbench.review_stream import fingerprint, reduction_pack, resolved


@pytest.mark.parametrize('final',[False,True])
def test_paged_synthesis_replaces_derived_history_not_sources_or_obligations(final):
    sources=[{'id':'o','fields':{'excerpt':'original support and contrary text'}}]
    history=[{'dossier_id':f'd{i}','reason':'old narrative',
        'stages':[{'temporal_scope':{'anchors':['derived time narrative '*1000]}}],
        'alternatives':[f'normal alternative {i}'],'remaining_checks':[f'unresolved check {i}'],
        'counterevidence_ids':['o']} for i in range(12)]
    checks=[{'id':'c','observation_ids':['o'],'predicate':'unmodified test predicate'}]
    objections=[{'id':'ob','observation_ids':['o'],'span_ids':[],'reason':'unresolved contrary source'}]
    canonical={'observations':sources,'prior_findings_untrusted':history,'prior_findings_total':12,
        'required_dossiers':[],'allowed_observation_ids_by_dossier':{},'executed_checks':checks}
    before=deepcopy(canonical)
    stream={'id':'s','canonical':canonical,'canonical_sha256':fingerprint(canonical),
        'page_count':1,'maximum':15952,'open_objections':objections}
    page={'id':'p','receipt_id':'r','included_ids':['o'],'pack':{'observations':sources},
        'objection_ids':['ob'],'output':{'findings':[{'observation_ids':['o']}],
        'check_assessments':[{'check_id':'c','observation_ids':['o'],'outcome':'inconclusive'}]}}
    pack=resolved(reduction_pack(stream,[page],final=final))
    assert model_view_size(pack)<15952
    assert pack['observations']==sources
    assert pack['executed_checks']==[{**checks[0],'omitted_observations':0}]
    assert pack['open_objections']==objections
    context=pack['prior_findings_context']
    assert context['retrieval']=='canonical_pack.prior_findings_untrusted'
    assert context['canonical_input_sha256']==fingerprint(before)
    assert context['findings_retained']==12
    assert context['unresolved_context']==[{k:f[k] for k in (
        'dossier_id','alternatives','remaining_checks','counterevidence_ids')} for f in history]
    assert 'prior_findings_untrusted' not in pack
    assert canonical==before
    # Mandatory counterevidence is still an irreducible floor, not compressed.
    canonical['observations'][0]['fields']['excerpt']='contrary source '*20000
    with pytest.raises(InputBudgetError):reduction_pack(stream,[page],final=final)


@pytest.mark.parametrize('final',[False,True])
def test_comparison_literal_hints_use_selected_views_not_canonical_candidates(final):
    from workbench.semantic_contract import assertion_errors
    sources=[{'id':f'o{i}','fields':{'path':f'/source/{i}','excerpt':'original hidden body'}} for i in range(20)]
    selected={'id':'o19','fields':{'path':'/source/19','excerpt':'selected text'}}
    canonical={'observations':sources,'required_dossiers':[{'id':'d','observation_ids':[o['id'] for o in sources]}],
        'allowed_observation_ids_by_dossier':{'d':[o['id'] for o in sources]},'executed_checks':[],
        'literal_fact_candidates':[{'observation_id':'o0','pointer':'/fields/excerpt','operator':'equals','value':'original hidden body'}]}
    before=deepcopy(canonical)
    stream={'id':'s','canonical':canonical,'canonical_sha256':fingerprint(canonical),'page_count':1,'maximum':36000}
    page={'id':'p','receipt_id':'r','included_ids':['o19'],'pack':{'observations':[selected]},
        'output':{'findings':[{'observation_ids':['o19']}],'check_assessments':[]}}
    pack=resolved(reduction_pack(stream,[page],final=final))
    assert pack['observations']==[selected]
    assert pack['literal_fact_candidates']==[{'observation_id':'o19','pointer':'/fields/path','operator':'equals','value':'/source/19'}]
    assert pack['allowed_observation_ids_by_dossier']=={'d':['o19']}
    assert 'original hidden body' not in str(pack)
    assert not assertion_errors({'observation_ids':['o19'],'fact_assertions':pack['literal_fact_candidates']},{'o19':selected},['o19'])
    assert canonical==before
