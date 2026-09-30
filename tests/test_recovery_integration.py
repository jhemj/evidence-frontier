"""Admission hook tests use only an anonymous local Store and retained text."""
from copy import deepcopy
import json

import pytest

from test_purpose_contract_v2 import fixture, discriminate
from workbench import question_engine
from workbench.recovery_integration import (admission_failure, supply_pending_body_views,
    rejected_schema_hints, bind_recovery_feedback)
from workbench.test_contract_v2 import build_manifest, object_ref


def setup(fixture, *, category='empty'):
    store, cid, evidence, task, owner, question, observation, context, call = fixture
    task = store.update(task['id'], recovery_policy='bounded-v1')
    shown = deepcopy(observation)
    if category == 'body':
        shown['fields'].pop('excerpt')
        context = build_manifest(store, cid, task, evidence, 'run', owners=[owner],
            questions=[question], observations=[shown])
        call = deepcopy(call)
        call['test_design']['required_inputs'] = [{'ref': object_ref(observation),
            'role': 'counterevidence', 'required_view': 'body_excerpt',
            'trust_basis': 'retained_source', 'scope': context['scope']}]
    else:
        call = discriminate(call, owner, support=False, refute=False)
    source = store.add('review_input', cid, task_id=task['id'], evidence_id=evidence['id'],
        generation=0, pack={'observations': [shown], 'test_contract_context': context})
    from workbench.test_admission import assess
    from workbench.test_contract_v2 import current_manifest
    admission = assess(call, policy='purpose-outcomes-v2', context=context,
        current_context=current_manifest(store, cid, context))
    intent = store.add('test_intent', cid, admission=admission, status='blocked')
    return store, cid, evidence, task, owner, question, observation, context, call, source, intent


def test_local_admission_failure_is_bounded_and_preserves_original_gap(fixture):
    store, cid, evidence, task, _, _, _, _, call, source, intent = setup(fixture)
    first = admission_failure(store, cid, task, evidence, 'run', call, intent, source)
    assert first['status'] == 'repair_proposed'
    assert first['action'] == 'redesign_purpose_and_one_sided_conditions'
    second = admission_failure(store, cid, task, evidence, 'run', call, intent, source)
    assert second['status'] == 'terminated'
    assert not second['original_obligation_resolved'] and not second['retry_permitted']
    routed = [r for r in store.list('receipt', cid) if r.get('receipt_type') == 'recovery_router']
    assert len(routed) == 2
    assert routed[1]['recovery']['parent_receipt_sha256'] == routed[0]['recovery']['receipt_sha256']
    assert not store.list('investigation_job', cid) and not store.list('request_lifecycle', cid)
    with pytest.raises(ValueError, match='불변'):
        store.update(routed[0]['id'], recovery={})


def test_default_off_and_other_typed_failures_have_no_new_receipts(fixture):
    values = setup(fixture)
    store, cid, evidence, task, _, _, _, _, call, source, intent = values
    disabled = {**task, 'recovery_policy': 'disabled'}
    before = len(store.list('receipt', cid))
    assert admission_failure(store, cid, disabled, evidence, 'run', call, intent, source) is None
    for reason in ('contract_encoding_error', 'capability_mismatch', 'internal_reference_error',
            'target_locator_conflict', 'material_unavailable', 'model_service_unavailable'):
        other = {**intent, 'admission': {'eligible': False, 'reason': reason}}
        assert admission_failure(store, cid, task, evidence, 'run', call, other, source) is None
    assert len(store.list('receipt', cid)) == before


def test_invalid_design_missing_target_is_not_reclassified_as_empty_condition(fixture):
    store, cid, evidence, task, _, _, _, _, call, source, intent = setup(fixture)
    call['test_design']['target_ref'] = None
    assert admission_failure(store, cid, task, evidence, 'run', call, intent, source) is None
    assert not store.list('receipt', cid)


def test_source_owner_generation_and_body_versions_are_exact(fixture):
    store, cid, evidence, task, _, _, _, _, call, source, intent = setup(fixture, category='body')
    for field, value in (('case_id', 'CASE-other'), ('task_id', 'TASK-other'), ('generation', 9)):
        assert admission_failure(store, cid, task, evidence, 'run', call, intent,
            {**source, field: value}) is None
    assert admission_failure(store, cid, task, evidence, 'other-run', call, intent, source) is None
    store.update(task['id'], retry_generation=1)
    assert admission_failure(store, cid, task, evidence, 'run', call, intent, source) is None
    assert not store.list('receipt', cid)


def test_retained_body_view_repair_is_not_a_model_request_or_recovery_success(fixture):
    store, cid, evidence, task, _, _, observation, _, call, source, intent = setup(fixture, category='body')
    result = admission_failure(store, cid, task, evidence, 'run', call, intent, source)
    assert result['action'] == 'complete_required_input_view'
    pack = deepcopy(source['pack'])
    assert 'excerpt' not in pack['observations'][0]['fields']
    supply_pending_body_views(pack, store, cid, task, evidence, 'run')
    assert pack['observations'][0]['fields']['excerpt'] == observation['fields']['excerpt']
    assert len(pack['observations']) == 1
    assert not result['original_obligation_resolved']
    assert not store.list('request_lifecycle', cid) and not store.list('investigation_job', cid)


def test_missing_acquired_body_is_not_a_presentation_repair(fixture):
    store, cid, evidence, task, owner, question, _, context, call, source, intent = setup(fixture, category='body')
    missing = store.add('observation', cid, evidence_id=evidence['id'], source_run='run',
        type='linux_configuration', fields={'path': '/fixture/missing', 'source_complete': False})
    shown = deepcopy(missing)
    context = build_manifest(store, cid, task, evidence, 'run', owners=[owner], questions=[question], observations=[shown])
    call['test_design']['required_inputs'][0]['ref'] = object_ref(missing)
    source = store.add('review_input', cid, task_id=task['id'], generation=0,
        pack={'observations': [shown], 'test_contract_context': context})
    assert admission_failure(store, cid, task, evidence, 'run', call, intent, source) is None
    assert not store.list('receipt', cid)


def test_body_repair_does_not_append_hidden_sources_or_replace_paged_coordinates(fixture):
    store, cid, evidence, task, _, _, _, _, call, source, intent = setup(fixture, category='body')
    admission_failure(store, cid, task, evidence, 'run', call, intent, source)
    empty = {'observations': []}
    assert supply_pending_body_views(empty, store, cid, task, evidence, 'run') == empty
    paged = deepcopy(source['pack'])
    paged['observations'][0]['source_span'] = {'byte_start': 5, 'byte_end': 9, 'text': 'tiny'}
    prior = deepcopy(paged)
    assert supply_pending_body_views(paged, store, cid, task, evidence, 'run') == prior
    wrong = deepcopy(source['pack'])
    prior = deepcopy(wrong)
    assert supply_pending_body_views(wrong, store, cid, task, evidence, 'other-run') == prior


def test_new_physical_object_is_not_blocked_by_previous_identical_loop(fixture):
    store, cid, evidence, task, _, _, _, _, call, source, intent = setup(fixture)
    admission_failure(store, cid, task, evidence, 'run', call, intent, source)
    admission_failure(store, cid, task, evidence, 'run', call, intent, source)
    changed = {**call, 'path': '/fixture/new-object', 'partition_offset': 8192}
    assert admission_failure(store, cid, task, evidence, 'run', changed, intent, source)['status'] == 'repair_proposed'


def test_actual_question_engine_hook_keeps_failed_intent_blocked_with_explicit_hint(fixture):
    store, cid, evidence, task, _, question, _, _, call, source, _ = setup(fixture)
    result = question_engine.reserve(store, cid, task, question, call, evidence, 'run', source_record_id=source['id'])
    assert result['status'] == 'blocked' and result['admission']['reason'] == 'invalid_test_design'
    assert result['admission']['recovery']['original_obligation_resolved'] is False
    assert result['admission']['recovery']['action'] == 'redesign_purpose_and_one_sided_conditions'


def schema_failure(fixture, calls=None):
    values = setup(fixture)
    store, cid, evidence, task, _, _, _, _, call, source, _ = values
    call = {**call, 'reason': 'Determine whether this exact scoped source discriminates the target.'}
    raw = json.dumps({'summary': 'Rejected output, not evidence.',
        'next_checks': [call] if calls is None else calls})
    from workbench.provider import ModelOutputError
    error = ModelOutputError('At least one discriminating side must be supported', raw,
        'output_schema', {'delivery_state': 'response_received'})
    diagnostic = store.add('review_diagnostic', cid, task_id=task['id'], generation=0,
        input_record_id=source['id'], raw_output=raw, failure_category='output_schema')
    return values, call, error, diagnostic


def test_schema_rejection_before_reserve_gets_only_blocked_exact_repair_hint(fixture):
    values, call, error, diagnostic = schema_failure(fixture)
    store, cid, evidence, task, _, _, _, _, _, source, _ = values
    from workbench.models import JudgmentCheckV2
    with pytest.raises(ValueError, match='At least one discriminating side'):
        JudgmentCheckV2.model_validate(call)
    hints = rejected_schema_hints(store, cid, task, evidence, 'run', source, error, diagnostic['id'])
    assert len(hints) == 1 and hints[0]['status'] == 'repair_proposed'
    intent = store.get(hints[0]['test_intent_id'])
    assert intent['status'] == 'blocked' and intent['admission']['eligible'] is False
    assert intent['scope']['test_design'] == call['test_design']
    assert not hints[0]['original_obligation_resolved']
    assert not store.list('investigation_job', cid) and not store.list('request_lifecycle', cid)
    second = rejected_schema_hints(store, cid, task, evidence, 'run', source, error, diagnostic['id'])
    assert second[0]['status'] == 'terminated'
    assert second[0]['test_intent_id'] == hints[0]['test_intent_id']
    # The invented shape probe never becomes a retained design or suggestion.
    assert 'shape-only probe' not in json.dumps(store.list('test_intent', cid))
    assert 'shape-only probe' not in json.dumps(store.list('receipt', cid))


@pytest.mark.parametrize('change', ['wrong_owner', 'stale_owner', 'wrong_source', 'wrong_generation',
    'missing_reason', 'unknown_field', 'unsupported_tool', 'wrong_diagnostic', 'duplicate_outcome'])
def test_schema_recovery_does_not_absorb_other_contract_or_scope_failures(fixture, change):
    values, call, error, diagnostic = schema_failure(fixture)
    store, cid, evidence, task, owner, _, _, _, _, source, _ = values
    altered = deepcopy(call)
    if change == 'wrong_owner':altered['test_design']['owner_ref']['id'] = 'DOSSIER-other'
    elif change == 'stale_owner':store.update(owner['id'], revision='changed')
    elif change == 'wrong_source':source = {**source, 'task_id': 'TASK-other'}
    elif change == 'wrong_generation':store.update(task['id'], retry_generation=1)
    elif change == 'missing_reason':altered.pop('reason')
    elif change == 'unknown_field':altered['test_design']['unrecognized'] = True
    elif change == 'unsupported_tool':altered['tool'] = 'static_file'
    elif change == 'wrong_diagnostic':diagnostic = {**diagnostic, 'id': 'DIAGNOSTIC-missing'}
    elif change == 'duplicate_outcome':altered['test_design']['allowed_outcomes'] *= 2
    if altered != call:
        from workbench.provider import ModelOutputError
        raw = json.dumps({'next_checks': [altered]})
        error = ModelOutputError('schema rejection', raw, 'output_schema')
        diagnostic = store.add('review_diagnostic', cid, task_id=task['id'], generation=0,
            input_record_id=source['id'], raw_output=raw, failure_category='output_schema')
    before = len(store.list('receipt', cid))
    assert rejected_schema_hints(store, cid, task, evidence, 'run', source, error, diagnostic['id']) == []
    assert len(store.list('receipt', cid)) == before


def test_schema_recovery_is_opt_in_strict_json_and_bounded_four_checks(fixture):
    values, call, error, diagnostic = schema_failure(fixture)
    store, cid, evidence, task, _, _, _, _, _, source, _ = values
    assert rejected_schema_hints(store, cid, {**task, 'recovery_policy': 'disabled'}, evidence,
        'run', source, error, diagnostic['id']) == []
    from workbench.provider import ModelOutputError
    for raw in ('```json\n' + error.raw_output + '\n```', json.dumps({'next_checks': [call] * 5}),
            json.dumps({'next_checks': {'0': call}}), 'not JSON',
            '{"next_checks": [], "next_checks": [' + json.dumps(call) + ']}'):
        malformed = ModelOutputError('schema rejection', raw, 'output_schema')
        wrong = store.add('review_diagnostic', cid, task_id=task['id'], generation=0,
            input_record_id=source['id'], raw_output=raw, failure_category='output_schema')
        assert rejected_schema_hints(store, cid, task, evidence, 'run', source, malformed, wrong['id']) == []
    assert not store.list('receipt', cid)


def test_schema_hints_reach_existing_repair_feedback_without_new_budget(fixture):
    values, _, error, diagnostic = schema_failure(fixture)
    store, cid, evidence, task, _, _, _, _, _, source, _ = values
    hints = rejected_schema_hints(store, cid, task, evidence, 'run', source, error, diagnostic['id'])
    from workbench.review_diagnostics import repair_feedback, FEEDBACK_LIMIT
    feedback = repair_feedback(error, [], diagnostic['id'])
    bounded = bind_recovery_feedback(feedback, hints)
    assert bounded['diagnostic_id'] == diagnostic['id']
    assert bounded['errors'] == feedback['errors']
    assert bounded['recovery_hints'] == hints
    assert len(json.dumps(bounded, ensure_ascii=False, separators=(',', ':'))) <= FEEDBACK_LIMIT
    prior_size = len(json.dumps(feedback, ensure_ascii=False, separators=(',', ':')))
    large = {**feedback, 'padding': 'x' * (FEEDBACK_LIMIT - prior_size - 200)}
    no_room = bind_recovery_feedback(large, hints)
    assert no_room['recovery_hints_omitted'] == 1 and not no_room['recovery_hints']
    assert no_room['padding'] == large['padding']  # Never shorten source/previous details for hints.
    very_full = {**feedback, 'padding': 'x' * (FEEDBACK_LIMIT - prior_size - 20)}
    assert bind_recovery_feedback(very_full, hints) == very_full


def test_actual_dossier_schema_failure_passes_exact_hint_to_next_bounded_input(tmp_path, monkeypatch):
    from test_dossiers import setup as dossier_setup
    from workbench.dossiers import finish
    from workbench.provider import ModelOutputError
    controller, cid, evidence, task, observation = dossier_setup(tmp_path)
    task = controller.store.update(task['id'], test_contract_policy='purpose-outcomes-v2',
        recovery_policy='bounded-v1')
    seen = []
    def reject(config, question, pack, role, **kwargs):
        seen.append(deepcopy(pack))
        context = pack['test_contract_context']
        owner = next(o for o in context['objects'] if o['ref']['kind'] == 'dossier')
        question_ref = next(o['ref'] for o in context['objects'] if o['ref']['kind'] == 'case_question')
        from workbench.test_contract_v2 import UNSUPPORTED
        rules = {k: UNSUPPORTED for k in ('supports', 'refutes', 'inconclusive',
            'found', 'no_match_in_scope', 'partial', 'unavailable')}
        rules['inconclusive'] = 'This result cannot yet distinguish the target proposition.'
        design = {'version': 2, 'purpose': 'discriminate', 'immediate_observable': 'source_content',
            'owner_ref': owner['ref'], 'question_ref': question_ref, 'target_ref': owner['ref'],
            'target_scope': context['scope'], 'target_proposition': owner['proposition'],
            'required_inputs': [], 'baseline_ref': None, 'required_result_view': 'body_excerpt',
            'outcome_rules': rules, 'allowed_outcomes': ['inconclusive'], 'design_timing': 'before_result',
            'uses_existing_result': False, 'lineage': None,
            'expected_update': 'Only the exact narrow record, not intrusion.', 'reopen_on': 'Changed source scope'}
        call = {'tool': 'read_file', 'path': observation['fields']['path'],
            'hypothesis_id': owner['ref']['id'], 'question_id': question_ref['id'],
            'reason': 'Inspect the scoped retained configuration.', 'success_condition': 'Source is returned',
            'test_design': design}
        raise ModelOutputError('At least one discriminating side must be supported',
            json.dumps({'summary': 'Rejected draft', 'next_checks': [call]}), 'output_schema')
    monkeypatch.setattr('workbench.dossiers.consult', reject)
    finish(controller, cid, evidence, task)
    assert len(seen) == 1
    router = [r for r in controller.store.list('receipt', cid) if r.get('receipt_type') == 'recovery_router']
    assert router and router[-1]['recovery']['status'] == 'repair_proposed'
    finish(controller, cid, evidence, task)
    assert len(seen) == 2
    assert seen[1]['validation_feedback']['recovery_hints'][0]['status'] == 'repair_proposed'
    assert seen[1]['validation_feedback']['recovery_hints'][0]['original_obligation_resolved'] is False
    assert not controller.store.list('investigation_job', cid)
    assert all(i['status'] == 'blocked' for i in controller.store.list('test_intent', cid))
