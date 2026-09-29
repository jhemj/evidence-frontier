import json

from scripts.evaluate_semantic_release import evaluate, load_cases, pack_for


def test_synthetic_release_cases_only_assert_local_contracts():
    cases=load_cases('tests/fixtures/semantic_release_cases.json')
    result=evaluate(cases)
    assert result['passed'] is True
    assert result['semantic_quality_claim'] is False
    assert result['model_calls']==0
    assert len(result['cases'])==9
    assert all(row['deterministic']['passed'] for row in result['cases'])


def test_pack_is_source_bound_and_not_finalizable():
    case=load_cases('tests/fixtures/semantic_release_cases.json')[0]
    pack=pack_for(case)
    assert pack['finalization_allowed'] is False
    assert pack['allowed_observation_ids']==[o['id'] for o in pack['observations']]
    assert pack['allowed_observation_ids_by_dossier'][case['id']]==pack['allowed_observation_ids']


def test_live_requires_explicit_endpoint_and_model(tmp_path):
    from scripts.evaluate_semantic_release import main
    try:
        main(['--live','--output',str(tmp_path/'out.json')])
    except SystemExit as exc:
        assert exc.code==2
    else:
        raise AssertionError('live mode must fail closed without endpoint/model')


def test_wrong_semantic_output_fails_contract_without_model_call():
    case=next(c for c in load_cases('tests/fixtures/semantic_release_cases.json') if c['id']=='static_vs_execution')
    from scripts.evaluate_semantic_release import _semantic_contract, pack_for
    ids=[o['id'] for o in pack_for(case)['observations']]
    wrong={'summary':'execution confirmed', 'findings':[{'dossier_id':case['id'],'title':'x',
        'judgment':'확인','reason':'x','observation_ids':ids,'stages':[{'stage':'execution','judgment':'확인',
        'statement':'x','observation_ids':ids}]}], 'next_checks':[], 'check_assessments':[]}
    issues,_=_semantic_contract(case,wrong,pack_for(case))
    assert 'unsupported_stage:execution' in issues
    assert 'missing_remaining_check' in issues
