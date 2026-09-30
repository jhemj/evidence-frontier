"""Isolated, no-model recovery contracts. The live/frozen engine has no hook."""
from dataclasses import asdict, replace
import hashlib
import json

import pytest

from workbench.recovery_router import (
    CostReceipt, ExactRef, ExecutionObservation, FailureEpisode, LoopMaterial,
    OriginalObligation, PresentedBody, RecoveryBudget, RecoveryRouter,
    RequiredBody, ResolutionProof, ScopeFence, design_condition_satisfied,
    digest, progress_changes, required_bodies_present,
)


def ref(kind, identity, letter='a'):
    return ExactRef(kind, identity, letter * 64)


def fixture(*, category='empty_discriminating_condition', delivery='not_sent', generation=0):
    fence = ScopeFence('CASE-fixture', 'TASK-fixture', generation, 'EVIDENCE-fixture',
        'source/run-fixture', ref('observation', 'OBSERVATION-fixture'),
        ref('dossier', 'DOSSIER-fixture'), ref('receipt', 'RESULT-fixture'))
    target = ref('claim', 'CLAIM-fixture', 'b')
    question = ref('case_question', 'QUESTION-fixture', 'c')
    obligation = OriginalObligation('OBLIGATION-fixture', {
        'goal': 'evaluate_this_exact_returned_scope', 'target_ref': asdict(target),
        'question_ref': asdict(question), 'required_counterevidence': ['objection-fixture'],
    }, required_purpose='discriminate')
    coordinate = {'partition_offset': 2048, 'inode': 51, 'path': '/fixture/source',
                  'byte_start': 0, 'byte_end': 16}
    material = LoopMaterial(obligation, coordinate, {'view': 'metadata', 'body_included': False},
        'resolver-fixture-1', 'parser-fixture-1', 'validator-fixture-1', 'repair-fixture-1',
        preconditions={'same_source': True}, harness={'nonce': 'first', 'clock': 'first', 'pid': 1})
    episode = FailureEpisode(fence, 'MODEL_ATTEMPT-fixture', obligation.id, obligation.sha256,
        'controller_validation', category, delivery, 'transport-fixture', material,
        ref('receipt', 'FAILURE-fixture', 'd'))
    return fence, material, episode


def router(**limits):
    budget = RecoveryBudget(**dict({'max_actions': 3, 'max_model_requests': 3,
        'max_total_tokens': 5000, 'max_wall_seconds': 120}, **limits))
    return RecoveryRouter(enabled=True, budget=budget)


def design(fence, material, *, purpose='discriminate', side='supports'):
    rules = {k: 'unsupported_by_this_test' for k in (
        'supports', 'refutes', 'inconclusive', 'found', 'no_match_in_scope', 'partial', 'unavailable')}
    if purpose == 'discover':
        rules['found'] = 'Retain matching source observations only.'
        rules['partial'] = 'Preserve unsearched coverage.'
        allowed = ['found', 'partial']
    else:
        rules[side] = 'A bounded positive record with the supplied target scope.'
        rules['inconclusive'] = 'Missing or partial scope cannot decide the target.'
        allowed = [side, 'inconclusive']
    return {'version': 2, 'purpose': purpose, 'immediate_observable': 'source_content',
        'owner_ref': asdict(fence.owner_ref),
        'question_ref': material.obligation.canonical_required['question_ref'],
        'target_ref': None if purpose == 'discover' else material.obligation.canonical_required['target_ref'],
        'target_scope': {k: getattr(fence, k) for k in (
            'case_id', 'task_id', 'generation', 'evidence_id', 'source_run')},
        'target_proposition': '' if purpose == 'discover' else 'The exact source records the bounded condition.',
        'required_inputs': [], 'baseline_ref': None, 'required_result_view': 'body_excerpt',
        'outcome_rules': rules, 'allowed_outcomes': allowed,
        'design_timing': 'before_result', 'uses_existing_result': False, 'lineage': None,
        'expected_update': 'Evaluate only this returned source scope.',
        'reopen_on': 'Changed object, content or resolution context.'}


def repaired(material):
    return replace(material, input_presentation={'view': 'body_excerpt',
        'body_included': True, 'presented_content_sha256': 'f' * 64})


def progress(material, accepted):
    return {'accepted_assessment': [{'accepted': True, 'ref': asdict(accepted),
        'target_coordinate': material.canonical['target_coordinate'], 'outcome': 'supports',
        'support_content_digests': ['f' * 64], 'remaining_obligation_digests': []}]}


def proof(fence, material):
    return ResolutionProof(fence, ref('receipt', 'VALIDATION-fixture', 'e'),
        (ref('claim', 'ACCEPTED-fixture', 'f'),), (), material.obligation.sha256,
        material.input_digest, material.validator_version, False, True,
        (material.obligation.id,), ('OBLIGATION-authorization-still-unknown',))


def execution(episode, **changes):
    return replace(ExecutionObservation(episode.fence, episode.request_attempt_id,
        ref('receipt', 'EXECUTION-fixture'), True, True, episode.fence.result_ref), **changes)


def cost(identity='COST-fixture', **changes):
    return replace(CostReceipt(ref('receipt', identity), model_requests=1,
        input_tokens=100, output_tokens=20, wall_seconds=2,
        resource_wait_seconds=1, retry_count=0), **changes)


def test_default_off_has_no_callback_receipt_or_execution():
    fence, material, episode = fixture()
    emitted = []
    r = RecoveryRouter(emit=emitted.append)
    assert r.route(episode, fence=fence)['status'] == 'disabled'
    assert r.verify(episode, fence=fence, material=repaired(material), proof=None)['status'] == 'disabled'
    assert emitted == r.receipts == []
    with pytest.raises(ValueError, match='explicit finite'):
        RecoveryRouter(enabled=True)


def test_only_explicit_harness_nonce_clock_pid_are_volatile():
    _, material, _ = fixture()
    assert material.loop_key == replace(material,
        harness={'nonce': 'another', 'clock': 'another', 'pid': 987}).loop_key
    for field in ('nonce', 'clock', 'pid', 'time'):
        changed = {**material.input_presentation, 'evidence': {field: 'actual-source-value'}}
        assert material.loop_key != replace(material, input_presentation=changed).loop_key
        assert material.loop_key != replace(material, harness={field: 'unknown'}).loop_key or field in ('nonce', 'clock', 'pid')
    assert material.loop_key != replace(material, harness={'request_id': 'changed'}).loop_key
    assert material.loop_key != replace(material, preconditions={'same_source': False}).loop_key
    for version in ('resolver_version', 'parser_version', 'validator_version', 'repair_policy_version'):
        assert material.loop_key != replace(material, **{version: 'version-fixture-2'}).loop_key


def test_captured_loop_material_and_obligation_cannot_be_mutated_by_caller_alias():
    _, material, episode = fixture()
    loop, obligation, budget = material.loop_key, material.obligation.sha256, episode.budget_key
    material.input_presentation['body_included'] = True
    material.target_coordinate['inode'] = 999
    material.obligation.required['target_ref']['version'] = '0' * 64
    returned = material.canonical
    returned['input_presentation']['view'] = 'full_body'
    assert material.loop_key == loop and material.obligation.sha256 == obligation
    assert episode.budget_key == budget
    assert material.obligation.canonical_required['target_ref']['version'] == 'b' * 64


@pytest.mark.parametrize('side', ['supports', 'refutes'])
def test_one_sided_redesign_is_allowed_but_is_not_recovered(side):
    fence, material, episode = fixture()
    r = router()
    result = r.route(episode, fence=fence)
    assert result['action'] == 'redesign_purpose_and_one_sided_conditions'
    assert result['status'] == 'repair_proposed' and not result['retry_permitted']
    after = repaired(material)
    assert design_condition_satisfied(design(fence, after, side=side), material.obligation, fence)
    candidate = r.route(episode, fence=fence, material=after, design=design(fence, after, side=side))
    assert candidate['retry_permitted'] and candidate['status'] == 'repair_proposed'
    assert candidate['resolved_obligation_ids'] == []
    assert candidate['remaining_obligation_ids'] == [episode.obligation_id]
    assert not candidate['executes_request']


def test_both_sides_unsupported_and_purpose_downgrade_do_not_repair_original_goal():
    fence, material, _ = fixture()
    after = repaired(material)
    bad = design(fence, after)
    bad['outcome_rules']['supports'] = 'unsupported_by_this_test'
    bad['allowed_outcomes'] = ['inconclusive']
    assert not design_condition_satisfied(bad, material.obligation, fence)
    assert not design_condition_satisfied(design(fence, after, purpose='discover'), material.obligation, fence)


def test_design_scope_owner_target_and_question_cannot_be_borrowed():
    fence, material, _ = fixture()
    valid = design(fence, material)
    for field in ('owner_ref', 'target_ref', 'question_ref'):
        candidate = json.loads(json.dumps(valid))
        candidate[field]['version'] = '0' * 64
        assert not design_condition_satisfied(candidate, material.obligation, fence)
    candidate = json.loads(json.dumps(valid))
    candidate['target_scope']['case_id'] = 'CASE-foreign'
    assert not design_condition_satisfied(candidate, material.obligation, fence)


def test_only_actual_validated_adoption_of_same_obligation_is_resolved():
    fence, material, episode = fixture()
    after = repaired(material)
    accepted = proof(fence, after)
    r = router()
    result = r.verify(episode, fence=fence, material=after, proof=accepted,
        design=design(fence, after), after_progress=progress(after, accepted.accepted_refs[0]))
    assert result['status'] == 'resolved_for_scope'
    assert result['resolved_obligation_ids'] == [episode.obligation_id]
    assert result['remaining_obligation_ids'] == ['OBLIGATION-authorization-still-unknown']
    assert result['accepted_refs'] == [asdict(accepted.accepted_refs[0])]
    assert result['validation_ref'] == asdict(accepted.validation_ref)
    assert result['progress_axes']['accepted_assessment']
    assert not result['executes_request']


@pytest.mark.parametrize('change', [
    {'accepted_refs': ()}, {'original_condition_before': True}, {'original_condition_after': False},
    {'resolved_obligation_ids': ()}, {'remaining_obligation_ids': ('OBLIGATION-fixture',)},
    {'input_digest': '0' * 64}, {'obligation_digest': '0' * 64}, {'validator_version': 'other-validator'},
])
def test_receipt_or_new_contract_without_original_condition_resolution_is_not_recovery(change):
    fence, material, episode = fixture()
    after = repaired(material)
    accepted = replace(proof(fence, after), **change)
    result = router().verify(episode, fence=fence, material=after, proof=accepted,
        design=design(fence, after), after_progress=progress(after, ref('claim', 'ACCEPTED-fixture', 'f')))
    assert result['status'] == 'unresolved'
    assert result['resolved_obligation_ids'] == [] and episode.obligation_id in result['remaining_obligation_ids']


def test_same_validated_output_rejected_and_accepted_ref_cannot_be_resolved():
    fence, material, episode = fixture()
    after = repaired(material)
    accepted = proof(fence, after)
    accepted = replace(accepted, rejected_refs=accepted.accepted_refs)
    result = router().verify(episode, fence=fence, material=after, proof=accepted,
        design=design(fence, after), after_progress=progress(after, accepted.accepted_refs[0]))
    assert result['status'] == 'unresolved'


def test_unrelated_accepted_assessment_cannot_resolve_failed_owner():
    fence, material, episode = fixture()
    after = repaired(material)
    accepted = proof(fence, after)
    for change in ('coordinate', 'ref'):
        state = progress(after, accepted.accepted_refs[0])
        if change == 'coordinate':state['accepted_assessment'][0]['target_coordinate']['inode'] = 99
        else:state['accepted_assessment'][0]['ref']['version'] = '0' * 64
        result = router().verify(episode, fence=fence, material=after, proof=accepted,
            design=design(fence, after), after_progress=state)
        assert result['status'] == 'unresolved'


def test_metadata_filename_and_hash_are_not_required_body_and_scope_must_match():
    fence, material, episode = fixture(category='required_body_not_presented')
    text = 'bounded fixture source body'
    need = RequiredBody(fence.source_ref, 'body_excerpt', hashlib.sha256(text.encode()).hexdigest(), (0, len(text)))
    supplied = PresentedBody(fence.source_ref, 'body_excerpt', text, (0, len(text)))
    assert required_bodies_present((need,), (supplied,))
    assert not required_bodies_present((), (supplied,))
    for candidate in (replace(supplied, view='metadata'), replace(supplied, body=need.payload_sha256),
            replace(supplied, body='/fixture/filename'), replace(supplied, span=(1, len(text) + 1)),
            replace(supplied, source_ref=ref('observation', 'OBSERVATION-foreign'))):
        assert not required_bodies_present((need,), (candidate,))
    after = repaired(material)
    accepted = proof(fence, after)
    result = router().verify(episode, fence=fence, material=after, proof=accepted,
        required_bodies=(need,), presented_bodies=(replace(supplied, view='metadata'),),
        after_progress=progress(after, accepted.accepted_refs[0]))
    assert result['status'] == 'unresolved'
    result = router().verify(episode, fence=fence, material=after, proof=accepted,
        required_bodies=(need,), presented_bodies=(supplied,),
        after_progress=progress(after, accepted.accepted_refs[0]))
    assert result['status'] == 'resolved_for_scope'


def test_full_body_and_each_counterevidence_body_remain_required():
    source = ref('observation', 'SOURCE-fixture')
    other = ref('observation', 'COUNTEREVIDENCE-fixture', 'b')
    body = 'fixture content'
    need = RequiredBody(source, 'full_body', hashlib.sha256(body.encode()).hexdigest())
    supplied = PresentedBody(source, 'full_body', body, full_body_complete=True)
    assert required_bodies_present((need,), (supplied,))
    assert not required_bodies_present((need,), (replace(supplied, full_body_complete=False),))
    assert not required_bodies_present((need,), (replace(supplied, view='body_excerpt'),))
    counter = replace(need, source_ref=other)
    assert not required_bodies_present((need, counter), (supplied,))


@pytest.mark.parametrize('delivery', ['attempted', 'unknown', 'response_received'])
def test_delivery_without_actual_exact_execution_reconciliation_never_retries(delivery):
    fence, material, episode = fixture(delivery=delivery)
    after = repaired(material)
    r = router()
    assert r.route(episode, fence=fence, material=after, design=design(fence, after))['status'] == 'reconciliation_required'
    for observed in (execution(episode, terminal=False), execution(episode, execution_known=False),
            execution(episode, request_attempt_id='MODEL_ATTEMPT-other'),
            execution(episode, observed_result_ref=ref('receipt', 'RESULT-other'))):
        result = r.route(episode, fence=fence, material=after, execution=observed, design=design(fence, after))
        assert result['status'] == 'reconciliation_required' and not result['retry_permitted']
    result = r.route(episode, fence=fence, material=after, execution=execution(episode), design=design(fence, after))
    assert result['status'] == 'repair_proposed' and result['retry_permitted']
    adopted = proof(fence, after)
    assert router().verify(episode, fence=fence, material=after, proof=adopted,
        design=design(fence, after), after_progress=progress(after, adopted.accepted_refs[0]))['status'] == 'reconciliation_required'


@pytest.mark.parametrize('field,value', [
    ('case_id', 'CASE-other'), ('task_id', 'TASK-other'), ('generation', 1),
    ('evidence_id', 'EVIDENCE-other'), ('source_run', 'source/other'),
    ('source_ref', ref('observation', 'OBSERVATION-fixture', 'b')),
    ('owner_ref', ref('dossier', 'DOSSIER-fixture', 'b')),
    ('result_ref', ref('receipt', 'RESULT-fixture', 'b')),
])
def test_exact_scope_fencing_blocks_stale_route_and_adoption(field, value):
    fence, material, episode = fixture()
    foreign = replace(fence, **{field: value})
    after = repaired(material)
    assert router().route(episode, fence=foreign, material=after)['status'] == 'scope_rejected'
    accepted = replace(proof(fence, after), fence=foreign)
    assert router().verify(episode, fence=fence, material=after, proof=accepted,
        design=design(fence, after), after_progress=progress(after, accepted.accepted_refs[0]))['status'] == 'unresolved'


def test_target_or_obligation_change_requires_new_scope_not_original_recovery():
    fence, material, episode = fixture()
    target = {**material.canonical['target_coordinate'], 'inode': 1000}
    changed = replace(material, target_coordinate=target)
    assert router().route(episode, fence=fence, material=changed)['status'] == 'scope_rejected'
    changed = replace(material, obligation=OriginalObligation(episode.obligation_id, {'weaker_goal': True}))
    assert router().route(episode, fence=fence, material=changed)['status'] == 'scope_rejected'
    fresh = replace(episode, before=replace(material, target_coordinate=target))
    assert fresh.budget_key != episode.budget_key  # new object is not globally blocked


@pytest.mark.parametrize('category', ['schema', 'capability_mismatch', 'target_locator_conflict',
    'transient_io', 'model_service_unavailable', 'internal_reference_error', 'input_budget'])
def test_nonbranch_failures_defer_to_existing_typed_paths(category):
    fence, material, episode = fixture(category=category, delivery='unknown')
    result = router().route(episode, fence=fence)
    assert result['status'] == 'deferred' and result['action'] == 'existing_typed_path'
    assert not result['action_counted'] and not result['retry_permitted']


def test_unchanged_invalid_input_isolated_then_terminated_without_model():
    fence, material, episode = fixture(category='unchanged_invalid_input')
    r = router()
    first = r.route(episode, fence=fence)
    assert first['status'] == 'isolated'
    second = r.route(episode, fence=fence)
    assert second['status'] == 'terminated'
    assert all(not row['executes_request'] and not row['retry_permitted'] for row in r.receipts)
    assert second['remaining_obligation_ids'] == [episode.obligation_id]


def test_same_input_rejected_twice_does_not_keep_redesigning_forever():
    fence, material, episode = fixture()
    r = router()
    assert r.route(episode, fence=fence)['status'] == 'repair_proposed'
    assert r.route(episode, fence=fence)['status'] == 'terminated'
    assert r.route(episode, fence=fence)['status'] == 'terminated'


def test_request_id_wording_or_revision_only_neither_progress_nor_retry():
    fence, material, episode = fixture()
    before = {'accepted_assessment': [{'accepted': True, 'target_coordinate': {},
        'outcome': 'inconclusive', 'id': 'old', 'reason': 'old wording', 'revision': 1}]}
    after = json.loads(json.dumps(before))
    after['accepted_assessment'][0].update(id='new', reason='new wording', revision=99)
    assert not any(progress_changes(before, after).values())
    changed = replace(material, input_presentation={**material.canonical['input_presentation'],
        'request_id': 'new-id', 'instruction': 'Try accurately again.', 'revision': 99})
    result = router().route(episode, fence=fence, material=changed,
        before_progress=before, after_progress=after)
    assert result['status'] == 'repair_proposed' and not result['retry_permitted']


def test_progress_axes_remain_separate_and_new_collection_does_not_resolve_goal():
    fence, material, episode = fixture()
    after = repaired(material)
    collected = {'collection': [{'target_coordinate': material.canonical['target_coordinate'],
        'content_sha256': 'e' * 64, 'coverage_scope': {'exact_bytes': [0, 16]}}]}
    changes = progress_changes({}, collected)
    assert changes['collection'] and sum(changes.values()) == 1
    accepted = proof(fence, after)
    result = router().verify(episode, fence=fence, material=after, proof=accepted,
        design=design(fence, after), after_progress=collected)
    assert result['status'] == 'unresolved' and result['progress_axes']['collection']
    unadopted = progress(after, accepted.accepted_refs[0])
    unadopted['accepted_assessment'][0]['accepted'] = False
    assert not any(progress_changes({}, unadopted).values())


def test_generation_reconnect_attempt_policy_change_and_new_body_keep_failure_budget():
    fence, material, episode = fixture()
    r = router(max_actions=1)
    assert r.route(episode, fence=fence)['status'] == 'repair_proposed'
    new_fence = replace(fence, generation=1, task_id='TASK-regenerated',
        owner_ref=ref('dossier', 'DOSSIER-fixture', 'b'))
    changed = replace(repaired(material), validator_version='validator-fixture-2', repair_policy_version='repair-fixture-2')
    new_episode = replace(episode, fence=new_fence, request_attempt_id='MODEL_ATTEMPT-new', before=changed)
    assert new_episode.budget_key == episode.budget_key
    restored = RecoveryRouter(enabled=True, budget=r.budget, receipts=r.receipts)
    result = restored.route(new_episode, fence=new_fence)
    assert result['status'] == 'terminated'
    assert result['reason'] == 'finite_recovery_budget_exhausted'


def test_observed_cost_limits_include_failures_and_same_physical_receipt_is_not_double_charged():
    fence, material, episode = fixture()
    r = router(max_total_tokens=200)
    c = cost()
    r.route(episode, fence=fence, material=repaired(material), cost=c)
    r.route(episode, fence=fence, material=repaired(material), cost=c)
    assert r._spent(episode)['model_requests'] == 1
    assert r._spent(episode)['total_tokens'] == 120
    result = r.route(episode, fence=fence, material=repaired(material), cost=cost('COST-second'))
    assert result['status'] == 'terminated'
    assert r._spent(episode)['total_tokens'] == 240


def test_unknown_cost_is_preserved_and_cannot_authorize_model_retry():
    fence, material, episode = fixture()
    after = repaired(material)
    r = router()
    result = r.route(episode, fence=fence, material=after,
        cost=cost(input_tokens=None, output_tokens=None), design=design(fence, after))
    assert r._spent(episode)['cost_unknown']
    assert result['status'] == 'repair_proposed' and not result['retry_permitted']
    assert r.receipts[0]['cost']['input_tokens'] is None


def test_receipt_lineage_is_restored_and_tampering_or_cost_contradiction_is_rejected():
    fence, material, episode = fixture()
    r = router()
    r.route(episode, fence=fence, cost=cost())
    assert r.receipts[1]['parent_receipt_sha256'] == r.receipts[0]['receipt_sha256']
    rows = json.loads(json.dumps(r.receipts))
    rows[0]['action_counted'] = True
    with pytest.raises(ValueError, match='restored'):
        RecoveryRouter(enabled=True, budget=r.budget, receipts=rows)
    with pytest.raises(ValueError, match='restored'):
        RecoveryRouter(enabled=True, budget=r.budget, receipts=list(reversed(r.receipts)))
    with pytest.raises(ValueError, match='contradictory cost'):
        r.route(episode, fence=fence, cost=cost(input_tokens=999))


def test_audit_metadata_excludes_source_text_raw_output_and_credentials():
    fence, material, episode = fixture()
    secret = 'private-fixture-body-and-auth-marker'
    changed = replace(material, input_presentation={'view': 'metadata', 'body': secret},
        preconditions={'retained_secret_context': secret})
    captured = []
    r = RecoveryRouter(enabled=True, budget=router().budget, emit=captured.append)
    result = r.route(episode, fence=fence, material=changed)
    serialized = json.dumps(result)
    assert secret not in serialized and 'input_presentation' not in result
    assert result['changed_dimensions'] == ['input_presentation', 'preconditions']
    assert result['before_input_digest'] != result['after_input_digest']
    assert result['obligation_sha256'] == episode.obligation_digest
    captured[0]['status'] = 'tampered callback copy'
    assert r.receipts[0]['status'] == 'repair_proposed'


@pytest.mark.parametrize('value', [-1, float('inf'), float('nan'), 'many'])
def test_finite_duration_limits_cannot_be_invalid(value):
    with pytest.raises(ValueError):
        router(max_wall_seconds=value)
