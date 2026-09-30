"""S2 deterministic acceptance boundaries, without a model/service/incident DB.

These tests cover current hard gates, not the semantic quality or economic
effect of conditional review. Gaps discovered by this audit are reported
separately; they are not hidden by xfail/skip or invented producer flags.
"""
from copy import deepcopy

import pytest

from workbench import objection_ledger
from workbench.claim_scope import qualify
from workbench.judgment_snapshot import claim_version
from workbench.models import JudgmentReport, ProviderConfig
from workbench.review_policy import second_pass
from workbench.review_validation import check_errors, errors


POLICY = {'second_review_policy': 'conditional-v1'}


def background():
    """A legal, literal-bound narrow configuration record, not normality."""
    source = {'id': 'fixture-source', 'type': 'linux_configuration', 'evidence_id': 'fixture-evidence',
        'source_location': 'fabricated fixture', 'fields': {'path': '/fixtures/config',
        'partition_offset': 0, 'inode': 7}}
    finding = {'dossier_id': 'fixture-dossier', 'timeline_role': '참고',
        'incident_relevance': {'level': 'context', 'reason': 'Narrow record only.',
                              'observation_ids': [source['id']]},
        'title': 'A retained fixture configuration path', 'card_summary': 'The path is recorded.',
        'judgment': '확인', 'reason': 'Exact retained field; not evidence of execution or approval.',
        'observation_ids': [source['id']], 'counterevidence_ids': [], 'basis': 'positive_evidence',
        'fact_assertions': [{'observation_id': source['id'], 'pointer': '/fields/path',
                             'operator': 'equals', 'value': '/fixtures/config'}],
        'stages': [{'stage': 'configuration', 'judgment': '확인',
                    'statement': 'The retained path names the configuration source.',
                    'observation_ids': [source['id']]}]}
    output = JudgmentReport.model_validate({'summary': 'Fabricated contract fixture.',
                                           'findings': [finding]}).model_dump()
    assert not errors(output, ['fixture-dossier'], [source['id']],
        {'fixture-dossier': [source['id']]}, {source['id']: source},
        require_literals=True, canonical_observations={source['id']: source})
    return source, output


def test_policy_is_explicit_and_default_or_always_remains_two_passes():
    _, output = background()
    before = deepcopy(output)
    assert second_pass({}, output, {}, {})
    assert second_pass({'second_review_policy': 'always'}, output, {}, {})
    assert second_pass({'second_review_policy': 'unrecognized-policy'}, output, {}, {})
    assert not second_pass(POLICY, output, {}, {})
    assert output == before
    assert output['findings'][0]['judgment'] == '확인'
    assert 'incident_assessment' not in output


def test_only_second_review_option_changes_in_the_controlled_config():
    baseline = ProviderConfig(model='fabricated-generation-model').model_dump()
    conditional = ProviderConfig.model_validate({**baseline,
        'second_review_policy': 'conditional-v1'}).model_dump()
    assert baseline['second_review_policy'] == 'always'
    assert {k for k in baseline if baseline[k] != conditional[k]} == {'second_review_policy'}
    assert conditional['review_queue_policy'] == baseline['review_queue_policy']
    assert conditional['review_concurrency'] == baseline['review_concurrency'] == 1
    assert conditional['secondary'] is baseline['secondary'] is None
    with pytest.raises(ValueError):
        ProviderConfig.model_validate({**baseline, 'second_review_policy': 'unrecognized-policy'})


@pytest.mark.parametrize('key,value', [
    ('timeline_role', '핵심'),
    ('incident_relevance', {'level': 'direct'}),
    ('incident_relevance', {'level': 'indirect'}),
    ('incident_relevance', {'level': 'undetermined'}),
    ('incident_relevance', {}),
    ('judgment', '유력'),
    ('basis', 'absence'),
    ('alternatives', ['Authorized operation remains a competing explanation.']),
    ('remaining_checks', ['Execution outcome remains unverified.']),
    ('open_objections', [{'id': 'fabricated-open-objection'}]),
    ('fact_assertions', []),
])
def test_each_existing_finding_hard_gate_requires_comparison(key, value):
    _, output = background()
    output['findings'][0][key] = value
    assert second_pass(POLICY, output, {}, {})


@pytest.mark.parametrize('stage,judgment', [
    ('configuration', '유력'),
    ('configuration', '미확인'),
    ('invocation', '확인'),
    ('invocation', '유력'),
    ('execution', '확인'),
    ('connection', '확인'),
    ('objective', '확인'),
    ('intent', '미확인'),
])
def test_tentative_or_behavioral_stage_on_a_background_card_keeps_comparison(stage, judgment):
    source, output = background()
    output['findings'][0]['stages'] = [{'stage': stage, 'judgment': judgment,
        'statement': 'This stage goes beyond a confirmed literal configuration record.',
        'observation_ids': [source['id']]}]
    assert second_pass(POLICY, output, {}, {})


def test_empty_result_and_objection_dispositions_never_qualify_for_single_pass():
    source, output = background()
    assert second_pass(POLICY, {'findings': []}, {}, {})
    output['objection_assessments'] = [{'objection_id': 'fabricated-objection', 'outcome': 'resolved',
        'basis': 'positive_evidence', 'reason': 'Disposition requires comparison.',
        'observation_ids': [source['id']]}]
    assert second_pass(POLICY, output, {}, {})


@pytest.mark.parametrize('phase', ['source_page', 'focus_page', 'comparison_page', 'synthesis'])
def test_every_paged_review_phase_requires_comparison(phase):
    source, output = background()
    pack = {'observations': [source], 'review_stream': {'phase': phase}}
    assert second_pass(POLICY, output, pack, {})


def test_new_candidate_explanation_never_qualifies_for_literal_only_skip():
    source, output = background()
    output['explanation_proposals'] = [{
        'question_id': '', 'explanation': 'An alternative remains possible.',
        'discriminating_question': 'Can supplied records distinguish this alternative?',
        'trigger_observation_ids': [source['id']],
        'next_discriminator': 'Compare a supplied outcome record.'}]
    assert second_pass(POLICY, output, {}, {})


def test_omitted_mandatory_objection_survives_and_requires_comparison():
    source, output = background()
    obligation = {'id': 'fabricated-objection', 'dossier_id': 'fixture-dossier',
        'statement': 'A contrary fixture record has not been resolved.',
        'observation_ids': ['fabricated-counter-source'], 'span_ids': [], 'status': 'open',
        'assessment_receipt_id': 'fabricated-receipt'}
    # No objection disposition was supplied. Qualifying still carries the
    # existing obligation; neither a literal fact nor silence resolves it.
    qualified = objection_ledger.qualify(output, [obligation])
    assert qualified['findings'][0]['open_objections'][0]['id'] == obligation['id']
    assert qualified['findings'][0]['publication_status'] == 'qualified_open_objections'
    assert second_pass(POLICY, qualified, {}, {})
    assert second_pass(POLICY, output, {'open_objections': [obligation]}, {})
    assert source['id'] in qualified['findings'][0]['observation_ids']


def test_unpresented_contrary_source_cannot_be_silently_resolved():
    source, output = background()
    obligation = {'id': 'fabricated-objection', 'dossier_id': 'fixture-dossier',
        'observation_ids': ['fabricated-counter-source']}
    output['objection_assessments'] = [{'objection_id': obligation['id'], 'outcome': 'resolved',
        'basis': 'positive_evidence', 'reason': 'Claims the dispute was resolved.',
        'observation_ids': [source['id']]}]
    issues = objection_ledger.errors(output, [obligation], {'fixture-dossier': [source['id']]})
    assert 'objection_source_not_represented' in {i['code'] for i in issues}
    assert second_pass(POLICY, output, {'open_objections': [obligation]}, {})


@pytest.mark.parametrize('status,failure', [
    ('partial', None),
    ('failed', {'code': 'path_not_resolved', 'retryable': False}),
    ('failed', {'code': 'transient_io', 'retryable': True}),
])
def test_incomplete_or_typed_failed_returned_result_keeps_second_review(status, failure):
    source, output = background()
    scope = {'status': status, 'complete': False, 'truncated': status == 'partial'}
    if failure:
        scope['failure'] = failure
    check = {'id': 'fabricated-job', 'status': status, 'scope': scope,
        'observation_ids': [source['id']],
        'contracts': [{'dossier_id': 'fixture-dossier', 'contract_id': 'fabricated-contract',
                       'refutation_condition': 'A positive incompatible record is present.'}]}
    output['check_assessments'] = [{'check_id': check['id'], 'dossier_id': 'fixture-dossier',
        'contract_id': 'fabricated-contract', 'outcome': 'inconclusive', 'reason': 'Result scope is incomplete.',
        'observation_ids': []}]
    assert check_errors(output, [check], {'fixture-dossier': [source['id']]}) == []
    before = deepcopy(output)
    assert second_pass(POLICY, output, {'executed_checks': [check]}, {'job_ids': [check['id']]})
    assert output == before
    assert output['check_assessments'][0]['outcome'] == 'inconclusive'


def test_partial_zero_match_is_not_a_refutation_even_under_conditional_policy():
    _, output = background()
    check = {'id': 'fabricated-job', 'status': 'partial', 'observation_ids': [],
        'scope': {'complete': False}, 'contracts': []}
    output['check_assessments'] = [{'check_id': check['id'], 'outcome': 'refutes',
        'basis': 'absence', 'reason': 'No matches in a partial search.', 'observation_ids': []}]
    codes = {issue['code'] for issue in check_errors(output, [check], {})}
    assert 'absence_preconditions_unverified' in codes
    assert 'partial_search_not_refutation' in codes
    assert second_pass(POLICY, output, {'executed_checks': [check]}, {'job_ids': [check['id']]})


@pytest.mark.parametrize('reason', ['required_input_unavailable', 'unsupported_tool', 'assessment_budget'])
def test_deferred_dependency_keeps_comparison_and_is_not_an_absence_result(reason):
    _, output = background()
    deferred = [{'request': {'tool': 'search', 'query': 'fabricated query'}, 'reason': reason}]
    assert second_pass(POLICY, output, {}, {'deferred_checks': deferred})
    assert 'check_assessments' in output and output['check_assessments'] == []


def test_conditional_skip_does_not_bypass_canonical_literal_validation():
    source, output = background()
    corrected_source = {**source, 'fields': {**source['fields'], 'path': '/fixtures/corrected'}}
    issues = errors(output, ['fixture-dossier'], [source['id']],
        {'fixture-dossier': [source['id']]}, {source['id']: source}, require_literals=True,
        canonical_observations={source['id']: corrected_source})
    assert any(i['code'] == 'source_literal_mismatch' and i.get('scope') == 'canonical_source' for i in issues)
    # The caller validates BEFORE this policy. A scheduling eligibility flag
    # is not a fact-validation or adoption operation and cannot repair issues.
    before = deepcopy(output)
    second_pass(POLICY, output, {}, {})
    assert output == before


def test_correction_versions_change_and_do_not_resolve_a_pending_objection():
    source, output = background()
    finding = output['findings'][0]
    corrected = {**finding, 'reason': 'The retained setting does not establish actual execution.',
        'change_reason': 'The previous interpretation overstated execution.',
        'open_objections': [{'id': 'fabricated-open-objection'}]}
    row = {'id': 'fixture-dossier', 'status': 'reviewed'}
    assert claim_version(row, finding, {source['id']: source}) != claim_version(row, corrected, {source['id']: source})
    assert second_pass(POLICY, {'findings': [corrected]}, {}, {})


@pytest.mark.parametrize('different_object', [False, True])
def test_source_count_never_becomes_independence_or_intrusion_confidence(different_object):
    source, output = background()
    another = {**source, 'id': 'fabricated-source-copy', 'fields': deepcopy(source['fields'])}
    if different_object:
        another['fields'].update(path='/fixtures/other', inode=8)
    finding = {**output['findings'][0], 'observation_ids': [source['id'], another['id']]}
    qualified = qualify(finding, {source['id']: source, another['id']: another})
    assert qualified['source_scope']['observation_count'] == 2
    assert qualified['source_scope']['origin_count'] == (2 if different_object else 1)
    assert qualified['source_scope']['independence_established'] is False
    assert qualified['judgment'] == finding['judgment']
    assert 'incident_assessment' not in qualified


def test_optional_decision_cannot_override_a_mandatory_review_at_current_boundary():
    # second_pass has no decision/provider argument. Keep the obligation as
    # the authority: a future D1 opinion must not replace this hard gate.
    _, output = background()
    assert second_pass(POLICY, output, {'open_objections': [{'id': 'fabricated-obligation'}]}, {})
    with pytest.raises(TypeError):
        second_pass(POLICY, output, {'open_objections': [{'id': 'fabricated-obligation'}]}, {},
            decision='skip_comparison')
