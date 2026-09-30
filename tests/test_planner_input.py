"""No-model planner envelope/reselection regressions; synthetic local sources.

The retained 28th input body was not saved, so these are contract regressions
alongside the separate read-only ledger reconstruction, not an exact body replay.
"""
from copy import deepcopy

import pytest

from workbench.controller import Controller
from workbench.investigation_graph import Investigation
from workbench.planner_input import (PlannerInputBlocked, fit_planner_input,
                                    material_binding)
from workbench.request_compiler import compile_request, request_spec
from workbench.review_context import serialize
from workbench.review_stream import resolved
from workbench.store import Store
from workbench.test_context_projection import attach
from workbench.test_contract_v2 import POLICY, object_ref


CONFIG = {'protocol': 'ollama', 'model': 'planner-fixture',
          'num_ctx': 32768, 'num_predict': 4000}
QUESTION = 'Compare the available source with the open question; no invented success.'


@pytest.fixture(autouse=True)
def no_external_requests(monkeypatch):
    monkeypatch.setattr('httpx.Client.request',
                        lambda *a, **k: pytest.fail('No external request allowed'))
    monkeypatch.setattr('workbench.investigation_graph.worker_request',
                        lambda *a, **k: pytest.fail('No worker request allowed'))
    monkeypatch.setattr('workbench.investigation_graph.Provider.generate',
                        lambda *a, **k: pytest.fail('No model request allowed'))
    for name in ('FRONTIER_MODEL_DIGEST', 'MODEL_RELAY_URL', 'MODEL_API_KEY',
                 'MODEL_SECONDARY_API_KEY'):
        monkeypatch.delenv(name, raising=False)


def fixture(tmp_path, *, count=6, chars=4500):
    store = Store(tmp_path / 'fixture.sqlite3')
    cid = 'CASE-planner-fixture'
    evidence = store.add('evidence', cid, path='fixture.E01', connected=True,
                         signature='synthetic-preparation-only')
    task = store.add('task', cid, evidence_id=evidence['id'], retry_generation=2,
                     test_contract_policy=POLICY)
    question = store.add('case_question', cid, task_id=task['id'],
        evidence_id=evidence['id'], generation=2, question='Which record was collected?',
        definition_revision='definition-1', dependency_revision='dependency-1',
        source_ids=['fixture-controller-question'])
    observations = [store.add('observation', cid, task_id=task['id'],
        evidence_id=evidence['id'], generation=2, source_run='RUN-fixture',
        type='linux_command', fields={'path': f'/fixture/record-{index}',
            'excerpt': (f'fixture-{index}: ' +
                        (chr(0xAC00 + index) + chr(0xB098 + index)) * (chars // 2)),
            'source_complete': True, 'source_sha256': str(index + 1) * 64,
            'partition_offset': 2048, 'inode': 10 + index})
        for index in range(count)]
    pack = {'target_os': 'linux', 'available_tools': ['search', 'read_file'],
        'observations': deepcopy(observations), 'included_observations': count,
        'total_observations': count, 'hypotheses': [], 'dynamic_hypotheses': [],
        'question_memory': {'questions': [question]},
        'open_objections': [{'id': 'OBJECTION-fixture',
            'text': 'Collection alone does not establish execution or authorization.'}],
        'coverage_checklist': [{'number': 1, 'question': 'Unresolved source question'}]}
    rebind = lambda candidate: attach(candidate, store, cid, task, evidence, 'RUN-fixture')
    audit = lambda candidate: {'available': count,
        'included': len(candidate['observations']),
        'omitted': count - len(candidate['observations']),
        'selected': [{'id': o['id'], 'reason': 'fixture-scope'}
                     for o in candidate['observations']]}
    return store, cid, task, evidence, question, observations, pack, rebind, audit


def fitted(pack, rebind, audit, *, config=None, protected=(), rejected=()):
    return fit_planner_input(pack, config or CONFIG, QUESTION, rebind=rebind,
        audit=audit, claims=lambda candidate: [], protected_ids=protected,
        rejected_material_bindings=rejected)


def test_full_schema_output_reserve_not_raw_pack_size_controls_selection(tmp_path):
    _, _, _, _, _, observations, pack, rebind, audit = fixture(tmp_path)
    rebind(pack)
    initial = compile_request(CONFIG, QUESTION, pack, 'investigator')
    assert len(serialize(pack)) < 36000
    assert initial.budget['estimated_headroom'] < 0
    assert initial.budget['output_tokens_reserved'] == 4000
    selected, compiled = fitted(pack, rebind, audit, protected=[observations[0]['id']])
    assert compiled.budget['estimated_headroom'] >= 0
    assert compiled.output_schema == initial.output_schema
    # Lossless table/run codecs can legitimately add/remove decoder guidance
    # when the presentation changes; the semantic protocol/schema may not.
    from workbench.request_compiler import test_contract_protocol
    assert test_contract_protocol() in compiled.messages[0]['content']
    assert test_contract_protocol() in initial.messages[0]['content']
    assert compiled.procedures == initial.procedures
    assert compiled.budget['context_tokens_requested'] == CONFIG['num_ctx']
    assert compiled.budget['output_tokens_reserved'] == CONFIG['num_predict']
    assert compiled.matches(CONFIG, QUESTION, selected, 'investigator')
    assert compiled.payload_json == compile_request(CONFIG, QUESTION, selected, 'investigator').payload_json
    assert selected['planner_input_projection']['deferred_count'] > 0


def test_whole_records_protected_source_bytes_and_unreviewed_lane_are_preserved(tmp_path):
    _, _, _, _, _, observations, pack, rebind, audit = fixture(tmp_path)
    before = deepcopy(pack)
    selected, _ = fitted(pack, rebind, audit, protected=[observations[0]['id']])
    visible = resolved(selected)
    original = {o['id']: o for o in observations}
    ids = [o['id'] for o in visible['observations']]
    projection = selected['planner_input_projection']
    assert observations[0]['id'] in ids
    assert projection['selected_observation_ids'] == ids
    assert projection['selection_attempts'] <= len(observations) + 1
    assert set(projection['deferred_observation_ids']).isdisjoint(ids)
    assert set(projection['deferred_observation_ids']) | set(ids) == set(original)
    assert 'not absent or refuted' in projection['scope']
    assert all(o == original[o['id']] for o in visible['observations'])
    assert visible['open_objections'] == before['open_objections']
    assert visible['coverage_checklist'] == before['coverage_checklist']
    assert pack == before  # Caller and retained source records were not rewritten.


def test_final_manifest_has_exact_owner_question_source_versions_and_visible_bodies(tmp_path):
    store, cid, task, evidence, question, observations, pack, rebind, audit = fixture(tmp_path)
    selected, _ = fitted(pack, rebind, audit, protected=[observations[0]['id']])
    manifest = resolved(selected)['test_contract_context']
    assert manifest['scope'] == {'case_id': cid, 'task_id': task['id'],
        'evidence_id': evidence['id'], 'generation': 2, 'source_run': 'RUN-fixture'}
    question_items = [item for item in manifest['objects'] if item['ref']['kind'] == 'case_question']
    assert len(question_items) == 1 and question_items[0]['ref'] == object_ref(question)
    selected_ids = set(selected['planner_input_projection']['selected_observation_ids'])
    source_items = [item for item in manifest['objects'] if item['ref']['kind'] == 'observation']
    assert {item['ref']['id'] for item in source_items} == selected_ids
    assert all(item['ref'] == object_ref(store.get(item['ref']['id'])) for item in source_items)
    assert all(item['view'] == 'full_body' for item in source_items)
    assert not set(selected['planner_input_projection']['deferred_observation_ids']) & {
        item['ref']['id'] for item in manifest['objects']}


def test_minimum_floor_blocks_before_transport_without_dropping_obligations(tmp_path):
    _, _, _, _, question, observations, pack, rebind, audit = fixture(tmp_path, chars=200)
    before = deepcopy(pack)
    low = {**CONFIG, 'num_ctx': 4096, 'num_predict': 1000}
    with pytest.raises(PlannerInputBlocked) as caught:
        fitted(pack, rebind, audit, config=low, protected=[observations[0]['id']])
    metadata = caught.value.metadata
    assert metadata['failure_category'] == 'input_budget'
    assert metadata['phase'] == 'input_preparation'
    assert metadata['request_attempted'] is False
    assert metadata['delivery_state'] == 'not_sent'
    assert metadata['automatic_same_input_retry'] is False
    assert metadata['protected_observation_ids'] == [observations[0]['id']]
    assert set(metadata['deferred_observation_ids']) == {o['id'] for o in observations[1:]}
    assert metadata['prompt_budget']['estimated_headroom'] < 0
    assert pack == before and question['id'] in serialize(pack)
    assert observations[0]['fields']['excerpt'] not in serialize(metadata)


@pytest.mark.parametrize('change', ['feedback', 'model_counter', 'selection_counter'])
def test_bookkeeping_is_not_a_material_change_and_rejected_input_is_reselected(tmp_path, change):
    _, _, _, _, _, observations, pack, rebind, audit = fixture(tmp_path, count=2, chars=200)
    first, _ = fitted(pack, rebind, audit)
    rejected = first['planner_input_projection']['material_binding']
    if change == 'feedback': pack['output_validation_feedback'] = {'error': 'unchanged budget error'}
    elif change == 'model_counter': pack['remaining_model_calls'] = 3
    else: pack['selection_audit'] = {'included': 2, 'new_bookkeeping': True}
    second, _ = fitted(pack, rebind, audit, rejected=[rejected])
    assert second['planner_input_projection']['material_binding'] != rejected
    assert second['planner_input_projection']['deferred_observation_ids'] == [observations[-1]['id']]
    assert len(resolved(second)['observations']) == 1


def test_rejected_protected_floor_is_typed_not_an_identical_retry(tmp_path):
    _, _, _, _, _, observations, pack, rebind, audit = fixture(tmp_path, count=1, chars=200)
    first, _ = fitted(pack, rebind, audit, protected=[observations[0]['id']])
    with pytest.raises(PlannerInputBlocked) as caught:
        fitted(pack, rebind, audit, protected=[observations[0]['id']],
               rejected=[first['planner_input_projection']['material_binding']])
    assert caught.value.metadata['reason'] == 'unchanged_rejected_material'
    assert caught.value.metadata['prompt_budget']['estimated_headroom'] >= 0
    assert caught.value.metadata['deferred_observation_ids'] == []


@pytest.mark.parametrize('change', ['source', 'partition', 'question', 'source_run', 'generation', 'capability'])
def test_actual_changed_material_is_not_blocked_by_prior_rejection(tmp_path, change):
    _, _, _, _, _, _, pack, rebind, _ = fixture(tmp_path, count=1, chars=200)
    rebind(pack)
    spec = request_spec(CONFIG, QUESTION, 'investigator')
    original = material_binding(spec, pack)
    if change == 'source': pack['observations'][0]['fields']['excerpt'] += ' newly retained content'
    elif change == 'partition': pack['observations'][0]['fields']['partition_offset'] += 2048
    elif change == 'question': spec['question'] += ' a different requested question'
    elif change == 'source_run': pack['test_contract_context']['scope']['source_run'] = 'RUN-new-source'
    elif change == 'generation': pack['test_contract_context']['scope']['generation'] = 3
    else: pack['tool_capabilities'] = {'read_file': {'body_view': 'unavailable'}}
    assert material_binding(spec, pack) != original


def planner_fixture(tmp_path):
    controller = Controller(Store(tmp_path / 'controller.sqlite3'), tmp_path)
    cid = controller.create('Synthetic envelope test', '', 'standard')['id']
    controller.store.update(cid, status='running')
    evidence = controller.store.add('evidence', cid, path='fixture.E01', connected=True,
                                    signature='fixture')
    task = controller.store.add('task', cid, action='linux_investigate', cell_id='CELL',
        evidence_id=evidence['id'], retry_generation=0, test_contract_policy=POLICY)
    controller.store.add('receipt', cid, task_id=task['id'], evidence_id=evidence['id'],
                         result={'run_id': 'RUN-fixture'})
    controller.store.add('config', '', provider={**CONFIG, 'num_ctx': 32768})
    from workbench.investigation import seed
    seed(controller, cid, evidence['id'])
    inv = Investigation(controller, cid, evidence, task)
    state = inv.preflight({})
    controller.store.update(controller.store.list('investigation_plan', cid)[0]['id'], assessed=True)
    return controller, cid, evidence, task, inv, state


def test_planner_passes_exact_compilation_to_model_boundary(tmp_path):
    c, cid, _, _, inv, state = planner_fixture(tmp_path)
    captured = []
    def capture(question, pack, run, purpose, *, compiled_request=None):
        captured.append(compiled_request)
        config = c.store.list('config')[-1]['provider']
        assert compiled_request is not None
        assert compiled_request.matches(config, question, pack, 'investigator')
        compiled_request.assert_fits()
        return None
    inv.model = capture
    assert inv.plan(state) == {'route': 'plan'}
    assert len(captured) == 1
    assert not c.store.list('model_reservation', cid)


def test_successful_plan_retains_small_selection_manifest_not_a_source_body_spool(tmp_path):
    c, cid, _, _, inv, state = planner_fixture(tmp_path)
    captured = []
    def capture(question, pack, run, purpose, *, compiled_request=None):
        captured.append(pack['planner_input_projection'])
        return {'summary': 'No-model proposal fixture', 'claims': [], 'hypotheses': [],
                'remaining_questions': ['Sources not presented remain unreviewed'], 'tool_calls': []}
    inv.model = capture
    planned = inv.plan(state)
    saved = c.store.get(planned['plan_id'])
    assert saved['planner_input_projection'] == captured[0]
    assert saved['valid_ids'] == captured[0]['selected_observation_ids']
    assert not {'pack', 'evidence_json', 'messages', 'raw_source'} & set(saved)
    assert not c.store.list('model_reservation', cid)


def test_planner_minimum_floor_never_reserves_a_model_call(tmp_path):
    c, cid, _, _, inv, state = planner_fixture(tmp_path)
    config = c.store.list('config')[-1]
    c.store.update(config['id'], provider={**CONFIG, 'num_ctx': 4096, 'num_predict': 1000})
    inv.model = lambda *a, **k: pytest.fail('Minimum floor called a model boundary')
    with pytest.raises(PlannerInputBlocked): inv.plan(state)
    receipts = [r for r in c.store.list('receipt', cid)
                if r.get('receipt_type') == 'planner_input_blocked']
    assert len(receipts) == 1 and receipts[0]['request_attempted'] is False
    assert c.store.get(state['run_id'])['stop_reason'] == 'planner_input_projection_blocked'
    assert c.store.get(state['run_id'])['model_calls'] == 0
    assert not c.store.list('model_reservation', cid)
    assert not c.store.list('request_lifecycle', cid)


@pytest.mark.parametrize('category', ['input_budget', 'output_schema', 'output_budget'])
def test_planner_feedback_distinguishes_input_reselection_from_output_repair(tmp_path, category):
    c, cid, evidence, task, inv, state = planner_fixture(tmp_path)
    c.store.add('receipt', cid, task_id=task['id'], evidence_id=evidence['id'],
        generation=0, receipt_type='model_error', reservation_id='MODEL_RESERVATION-fixture',
        error='fixture error', failure_category=category)
    c.store.update(state['run_id'], consecutive_plan_failures=1)
    captured = []
    def capture(question, pack, run, purpose, **kwargs):
        captured.append(pack)
        return None
    inv.model = capture
    inv.plan(state)
    feedback = captured[0].get('output_validation_feedback')
    if category == 'input_budget': assert feedback is None
    else: assert feedback['category'] == category and 'not evidence' in feedback['instruction']


def test_late_native_budget_error_preserves_binding_and_requests_reselection_not_json_repair(tmp_path, monkeypatch):
    from workbench.request_compiler import RequestBudgetError
    from workbench.request_lifecycle import RequestAttempt
    c, cid, _, _, inv, state = planner_fixture(tmp_path)
    captured = []
    original_model = inv.model
    def capture(question, pack, run, purpose, *, compiled_request=None):
        captured.append((question, pack, run, compiled_request))
        return None
    inv.model = capture
    inv.plan(state)
    question, pack, run, compiled = captured[0]
    # A boundary-time configuration drift is mocked, not dispatched. Preserve
    # the explicit rejected-material identity so the next selection cannot
    # merely add JSON feedback/counters and resend the same sources.
    low = {**CONFIG, 'num_ctx': 4096, 'num_predict': 1000}
    rejected = compile_request(low, question, pack, 'investigator')
    error = RequestBudgetError(rejected)
    def fail_before_dispatch(*args, **kwargs):
        assert kwargs['compiled_request'] is compiled
        raise error
    monkeypatch.setattr(RequestAttempt, 'consult', fail_before_dispatch)
    assert original_model(question, pack, run, 'plan', compiled_request=compiled) is None
    errors = [r for r in c.store.list('receipt', cid) if r.get('receipt_type') == 'model_error']
    assert len(errors) == 1
    assert errors[0]['recovery']['action'] == 'reselect_compiled_input'
    assert errors[0]['recovery']['automatic_same_input_retry'] is False
    binding = pack['planner_input_projection']['material_binding']
    assert errors[0]['recovery']['material_binding'] == binding
    assert errors[0]['compiled_request'] == rejected.identity
    assert errors[0]['prompt_budget'] == rejected.budget
    assert errors[0]['request_attempted'] is False
    assert errors[0]['delivery_state'] == 'not_sent'
    assert c.store.get(run['id'])['rejected_planner_material_bindings'] == [binding]
    assert c.store.get(run['id'])['plan_domain_batch_size'] == 3
    assert not any(row['phase'] == 'dispatch_attempted'
                   for row in c.store.list('request_lifecycle', cid))
