"""No-model regressions for the retained revision-zero V2 seed failure."""
from copy import deepcopy

import pytest

from workbench.controller import Controller
from workbench.investigation_graph import Investigation
from workbench.store import Store
from workbench.test_contract_v2 import POLICY, UNSUPPORTED, object_ref


SEED_CALL = {'tool': 'correlate', 'reason': 'AI 선택과 관계없이 원문 연결 분석'}


def setup(tmp_path, monkeypatch, *, policy=POLICY, platform='linux', generation=0):
    monkeypatch.setattr('workbench.investigation_graph.worker_request',
                        lambda *args, **kwargs: pytest.fail('No worker request allowed'))
    monkeypatch.setattr('workbench.investigation_graph.Provider.generate',
                        lambda *args, **kwargs: pytest.fail('No model request allowed'))
    c = Controller(Store(tmp_path / 'case.sqlite3'), tmp_path)
    cid = c.create('Anonymous initial collection', '', 'standard')['id']
    c.store.update(cid, status='running', target_os=platform)
    evidence = c.store.add('evidence', cid, path='fixture.E01', signature='fixture', connected=True)
    task = c.store.add('task', cid, evidence_id=evidence['id'],
                       action=platform + '_investigate', cell_id='CELL', status='queued',
                       retry_generation=generation, test_contract_policy=policy)
    c.store.add('receipt', cid, evidence_id=evidence['id'],
                result={'run_id': 'RUN-' + 'a' * 32})
    return c, cid, evidence, task, Investigation(c, cid, evidence, task)


def initial_plan(inv):
    state = inv.preflight({})
    state.update(inv.plan(state))
    return state, inv.s.get(state['plan_id'])


def test_revision_zero_v2_seed_admitted_as_controller_discovery(tmp_path, monkeypatch):
    c, cid, evidence, task, inv = setup(tmp_path, monkeypatch)
    state, plan = initial_plan(inv)
    call = plan['output']['tool_calls'][0]
    assert {k: call[k] for k in SEED_CALL} == SEED_CALL
    inv.admit(state, [call], 'exploration')
    intent = c.store.list('test_intent', cid)[0]
    assert intent['admission']['eligible'], intent['admission']
    assert intent['question_id'] == call['question_id']
    q = c.store.get(call['question_id'])
    design = call['test_design']
    assert design['purpose'] == 'discover'
    assert design['owner_ref'] == design['question_ref'] == object_ref(q)
    assert q['source_kind'] == 'case_question'
    assert design['target_ref'] is None and design['target_proposition'] == ''
    assert design['target_scope'] == plan['test_contract_context']['scope']
    assert design['immediate_observable'] == 'record_association'
    assert design['required_result_view'] == 'metadata'
    assert all(design['outcome_rules'][kind] == UNSUPPORTED
               for kind in ('supports', 'refutes', 'inconclusive'))
    assert set(design['allowed_outcomes']) == {'found', 'no_match_in_scope', 'partial', 'unavailable'}
    jobs = c.store.list('investigation_job', cid)
    assert len(jobs) == 1
    assert c.store.get(state['run_id'])['model_calls'] == 0
    assert not c.store.list('check_assessment', cid)
    # A completed collection may now lead to a model plan. It must not take
    # the zero-model/zero-tool no_new_check_scope exit from the retained trace.
    c.store.update(jobs[0]['id'], status='ingested')
    assert inv.stop_gate(state)['route'] == 'plan'
    assert c.store.get(state['run_id'])['stop_reason'] is None


def test_raw_retained_failure_shape_still_fails_v2_without_producer_contract(tmp_path, monkeypatch):
    c, cid, _, _, inv = setup(tmp_path, monkeypatch)
    state, _ = initial_plan(inv)
    inv.admit(state, [deepcopy(SEED_CALL)], 'exploration')
    intent = c.store.list('test_intent', cid)[0]
    assert intent['admission']['reason'] == 'contract_encoding_error'
    assert intent['status'] == 'blocked'
    assert not c.store.list('investigation_job', cid)
    assert c.store.get(state['run_id'])['model_calls'] == 0


def test_legacy_initial_call_is_unchanged(tmp_path, monkeypatch):
    c, cid, _, _, inv = setup(tmp_path, monkeypatch, policy='legacy')
    state, plan = initial_plan(inv)
    assert plan['output']['tool_calls'] == [SEED_CALL]
    assert 'test_contract_context' not in plan
    inv.admit(state, plan['output']['tool_calls'], 'exploration')
    assert c.store.list('test_intent', cid)[0]['admission']['eligible']


@pytest.mark.parametrize('platform', ['linux', 'windows'])
def test_initial_contract_exact_scope_and_generation(tmp_path, monkeypatch, platform):
    c, cid, evidence, task, inv = setup(tmp_path, monkeypatch, platform=platform, generation=3)
    state, plan = initial_plan(inv)
    call = plan['output']['tool_calls'][0]
    assert plan['generation'] == 3
    assert call['test_design']['target_scope'] == {
        'case_id': cid, 'task_id': task['id'], 'evidence_id': evidence['id'],
        'generation': 3, 'source_run': 'RUN-' + 'a' * 32}
    inv.admit(state, [call], 'exploration')
    assert c.store.list('test_intent', cid)[0]['admission']['eligible']


def test_critical_gap_read_is_collection_not_discriminator(tmp_path, monkeypatch):
    c, cid, evidence, _, inv = setup(tmp_path, monkeypatch)
    c.store.add('observation', cid, evidence_id=evidence['id'], type='linux_environment',
        fields={'coverage_map': {'critical_gaps': [{'path': '/fixture/config',
            'partition_offset': 2048, 'status': 'partial', 'bytes_scanned': 8192}]}})
    state, plan = initial_plan(inv)
    read = next(call for call in plan['output']['tool_calls'] if call['tool'] == 'read_file')
    assert read['byte_offset'] == 8192 and read['byte_length'] == 8192
    assert read['test_design']['immediate_observable'] == 'source_content'
    assert read['test_design']['required_result_view'] == 'body_excerpt'
    assert read['test_design']['purpose'] == 'discover'
    inv.admit(state, plan['output']['tool_calls'], 'exploration')
    assert all(row['admission']['eligible'] for row in c.store.list('test_intent', cid))


def test_changed_question_version_is_not_silently_substituted(tmp_path, monkeypatch):
    c, cid, _, _, inv = setup(tmp_path, monkeypatch)
    state, plan = initial_plan(inv)
    call = plan['output']['tool_calls'][0]
    c.store.update(call['question_id'], dependency_revision='changed-after-seed')
    inv.admit(state, [call], 'exploration')
    intent = c.store.list('test_intent', cid)[0]
    assert not intent['admission']['eligible']
    assert 'unoffered_or_stale_object_ref' in intent['admission']['diagnostics']
    assert not c.store.list('investigation_job', cid)


@pytest.mark.parametrize('change', ['generation', 'evidence_path', 'disconnected', 'source_run'])
def test_initial_discovery_rejects_unknown_or_changed_scope(tmp_path, monkeypatch, change):
    c, cid, evidence, task, _ = setup(tmp_path, monkeypatch)
    source_run = 'RUN-' + 'a' * 32
    if change == 'generation':
        c.store.update(task['id'], retry_generation=1)
    elif change == 'evidence_path':
        c.store.update(evidence['id'], path='different.E01')
    elif change == 'disconnected':
        c.store.update(evidence['id'], connected=False)
    else:
        source_run = 'RUN-' + 'b' * 32
    with pytest.raises(ValueError, match='current task/evidence/source scope'):
        c.initial_discovery_plan(cid, evidence, task, source_run, [SEED_CALL])
    assert not c.store.list('test_intent', cid)
    assert not c.store.list('case_question', cid)


def test_initial_owner_absence_does_not_fabricate_hypothesis_or_question(tmp_path, monkeypatch):
    c, cid, evidence, task, _ = setup(tmp_path, monkeypatch)
    monkeypatch.setattr('workbench.question_engine.refresh',
                        lambda *args: {'questions': [], 'corpus_revision': 'unknown'})
    with pytest.raises(ValueError, match='one exact case question'):
        c.initial_discovery_plan(cid, evidence, task, 'RUN-' + 'a' * 32, [SEED_CALL])
    assert not c.store.list('hypothesis', cid)
    assert not c.store.list('case_question', cid)
