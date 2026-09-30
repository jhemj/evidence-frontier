"""Pure D1 display gates; synthetic inputs only, no model or source DB."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from workbench.decision_contracts import DecisionResult, digest
from workbench.decision_view import project_decision
from workbench.openjev_readout import evaluate_fixture
from test_decision_d1 import compiled, profile, ref, request, response


def context(req):
    refs = [req.question_ref, req.logical_work_ref, req.candidate_producer_ref,
            *req.counterevidence_refs, *req.required_review_refs, *req.omitted_refs,
            *(e.ref for e in req.evidence), *(r for c in req.candidates for r in c.refs)]
    return {
        'current_envelope': {'case_id': req.case_id, 'run_id': req.run_id,
            'snapshot_revision': req.snapshot_revision, 'ledger_position': req.ledger_position},
        'current_versions': {(r.kind, r.id): r.version for r in refs},
    }


def fixture(req=None, p=None):
    req = req or request()
    c = compiled(req, p)
    result = evaluate_fixture(req, c, response(c))
    return req, result, {**context(req), 'expected_cache_key': c.cache_key, 'data_mode': 'replay'}


def assert_withheld(view):
    assert view['status'] == 'stale'
    assert not view['distribution_available']
    assert view['selected_id'] is None
    assert all(c['probability'] is None for c in view['candidates'])
    assert not view['policy_applied'] and not view['can_skip_required_review']
    assert not view['can_discard_evidence'] and not view['changes_intrusion_color']


def test_current_offline_fixture_is_explicit_advisory_not_case_judgment():
    req, result, inputs = fixture()
    view = project_decision(req, result, **inputs)
    assert view['distribution_available'] and view['selected_id'] == result.chosen_id
    assert view['provenance'] == 'fixture' and view['data_mode'] == 'replay'
    assert '시험용 예시' in view['title'] and view['display_only']
    assert not view['policy_applied'] and not view['changes_intrusion_color']


@pytest.mark.parametrize('field,value', [
    ('case_id', 'another-case'), ('run_id', 'another-run'),
    ('snapshot_revision', 'another-snapshot'), ('ledger_position', 2),
    ('ledger_position', True),
])
def test_other_case_run_snapshot_or_position_never_uses_old_distribution(field, value):
    req, result, inputs = fixture()
    inputs['current_envelope'][field] = value
    assert_withheld(project_decision(req, result, **inputs))


@pytest.mark.parametrize('field', ['case_id', 'run_id', 'snapshot_revision', 'ledger_position'])
def test_missing_current_envelope_field_is_unknown_not_current(field):
    req, result, inputs = fixture()
    del inputs['current_envelope'][field]
    assert_withheld(project_decision(req, result, **inputs))


@pytest.mark.parametrize('envelope', [None, {}, 'unknown'])
def test_unavailable_envelope_is_fail_closed(envelope):
    req, result, inputs = fixture()
    inputs['current_envelope'] = envelope
    assert_withheld(project_decision(req, result, **inputs))


def test_current_envelope_and_compiled_key_are_mandatory_arguments():
    req, result, inputs = fixture()
    for field in ('current_envelope', 'expected_cache_key'):
        missing = {k: v for k, v in inputs.items() if k != field}
        with pytest.raises(TypeError):
            project_decision(req, result, **missing)


@pytest.mark.parametrize('key', [None, '', 'unknown', '0' * 64])
def test_unknown_or_changed_compiled_key_is_not_a_cache_hit(key):
    req, result, inputs = fixture()
    inputs['expected_cache_key'] = key
    assert_withheld(project_decision(req, result, **inputs))


def test_profile_revision_temperature_change_invalidates_display_even_with_same_refs():
    req, result, inputs = fixture()
    new_profile = profile().model_copy(update={'version': 'profile-v2', 'readout_temperature': 1.2})
    inputs['expected_cache_key'] = compiled(req, new_profile).cache_key
    assert result.cache_key != inputs['expected_cache_key']
    assert_withheld(project_decision(req, result, **inputs))


@pytest.mark.parametrize('missing', [False, True])
def test_omitted_evidence_ref_is_also_a_freshness_dependency(missing):
    req, result, inputs = fixture(request(omitted_refs=[ref('observation', 'omitted')],
                                        input_completeness='partial'))
    if missing:
        del inputs['current_versions'][('observation', 'omitted')]
    else:
        inputs['current_versions'][('observation', 'omitted')] = 'v2'
    view = project_decision(req, result, **inputs)
    assert_withheld(view)
    assert ref('observation', 'omitted') in view['missing_refs']


@pytest.mark.parametrize('kind,ident', [
    ('question', 'q'), ('review', 'r'), ('controller', 'c'),
    ('objection', 'obj'), ('mandatory_review', 'm'),
    ('observation', 'o'), ('test_intent', 't'),
])
def test_each_referenced_object_requires_its_exact_current_version(kind, ident):
    req, result, inputs = fixture()
    inputs['current_versions'][(kind, ident)] = 'v2'
    assert_withheld(project_decision(req, result, **inputs))


@pytest.mark.parametrize('versions', [None, {}, 'unknown'])
def test_missing_reference_map_is_unknown(versions):
    req, result, inputs = fixture()
    inputs['current_versions'] = versions
    assert_withheld(project_decision(req, result, **inputs))


@pytest.mark.parametrize('change', ['request_id', 'digest', 'model', 'order', 'completeness'])
def test_result_must_bind_exact_request_model_order_and_input_scope(change):
    req, result, inputs = fixture()
    body = result.model_dump(mode='json')
    if change == 'request_id':
        body['request_id'] = 'other-request'
    if change == 'digest':
        body['request_digest'] = '0' * 64
    if change == 'model':
        body['model_identity']['weights_sha256'] = '8' * 64
    if change == 'order':
        body['candidate_order'].reverse()
    if change == 'completeness':
        body['input_completeness'] = 'unknown'
    assert_withheld(project_decision(req, DecisionResult.model_validate(body), **inputs))


def test_changed_request_cannot_reuse_old_result_or_cached_choice():
    req, result, inputs = fixture()
    changed = request(attempt_id='attempt-2')
    assert digest(changed) != result.request_digest
    assert_withheld(project_decision(changed, result, **inputs))


@pytest.mark.parametrize('provenance', ['fixture', 'unverified_runtime', 'disabled'])
def test_d1_has_no_live_attested_score_producer(provenance):
    req, result, inputs = fixture()
    body = result.model_dump(mode='json')
    body['provenance'] = provenance
    inputs['data_mode'] = 'live'
    assert_withheld(project_decision(req, DecisionResult.model_validate(body), **inputs))


def test_relabelled_runtime_is_not_offline_fixture_proof():
    req, result, inputs = fixture()
    body = result.model_dump(mode='json')
    body['provenance'] = 'unverified_runtime'
    assert_withheld(project_decision(req, DecisionResult.model_validate(body), **inputs))


@pytest.mark.parametrize('field,value', [
    ('score_basis', 'unverified'), ('readout_profile_version', None),
])
def test_unknown_readout_provenance_is_not_displayable(field, value):
    req, result, inputs = fixture()
    body = result.model_dump(mode='json')
    body[field] = value
    assert_withheld(project_decision(req, DecisionResult.model_validate(body), **inputs))


def test_mutating_nested_model_scores_is_revalidated_before_display():
    req, result, inputs = fixture()
    result = deepcopy(result)
    result.probabilities['review-result'] = float('nan')
    with pytest.raises(ValidationError):
        project_decision(req, result, **inputs)
