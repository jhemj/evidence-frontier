"""Zero-model tests of logical result ownership and opt-in scheduling."""
from copy import deepcopy

import pytest

from workbench import question_engine
from workbench.review_contracts import contract
from workbench.review_policy import (returned_obligations, returned_rank, order_queue,
    record_selection, result_revision, RETURNED_STREAK_LIMIT, WORKER_POLL_INTERVAL)
from workbench.store import Store


def fixture():
    task = {'id': 'task', 'evidence_id': 'evidence', 'case_id': 'case',
            'retry_generation': 2, 'review_queue_policy': 'returned-first-v1'}
    evidence = {'id': 'evidence', 'connected': True}
    envelope = {'case_id': 'case', 'task_id': 'task', 'evidence_id': 'evidence', 'generation': 2}
    condition = contract({'hypothesis_id': 'dossier-a', 'success_condition': 'matching record',
                          'refutation_condition': 'contrary record', 'inconclusive_condition': 'partial result'})
    job = {**envelope, 'id': 'job', 'status': 'ingested', 'source_run': 'source-run',
           'contracts': [condition], 'test_intent_ids': ['intent'], 'result_status': 'partial',
           'observation_ids': ['returned-record'], 'result_scope': {'status': 'partial', 'complete': False}}
    returned = {'job_id': job['id'], 'observation_ids': job['observation_ids'], 'scope': job['result_scope']}
    intent = {'id': 'intent', 'case_id': 'case', 'status': 'partial', 'assessment_status': 'unassessed',
              'conditions': {k: v for k, v in condition.items() if k.endswith('_condition')},
              'scope': {**envelope, 'source_run': 'source-run', 'logical_contract': condition},
              'result_scope': returned}
    batch = {**envelope, 'id': 'batch', 'dossier_ids': ['dossier-a'], 'job_ids': ['job'],
             'status': 'pending', 'created_at': '2026-01-01', 'round': 1, 'attempts': 0}
    return task, evidence, batch, intent, job


def obligations(batch, intents, jobs, **context):
    return returned_obligations(batch, intents, jobs,
        available_observation_ids={'returned-record'}, **context)


def test_shared_physical_result_does_not_promote_the_other_logical_owner():
    task, evidence, batch, intent, job = fixture()
    other = {**job['contracts'][0], 'dossier_id': 'dossier-b'}
    job['contracts'].append(other)
    sibling = {**batch, 'id': 'sibling', 'dossier_ids': ['dossier-b']}
    assert returned_rank(batch, [intent], [job], task=task, evidence=evidence) == 0
    assert returned_rank(sibling, [intent], [job], task=task, evidence=evidence) == 1
    assert obligations(sibling, [intent], [job]) == []
    second = {**deepcopy(intent), 'id': 'other-intent'}
    second['scope']['logical_contract'] = other
    job['test_intent_ids'].append(second['id'])
    assert returned_rank(sibling, [intent, second], [job], task=task, evidence=evidence) == 0
    assert len(obligations(batch, [intent, second], [job])) == 1


def test_legacy_owner_is_only_recovered_when_condition_owner_is_unique():
    _, _, batch, intent, job = fixture()
    intent['scope'].pop('logical_contract')
    assert returned_rank(batch, [intent], [job]) == 0
    job['contracts'].append({**job['contracts'][0], 'dossier_id': 'dossier-b'})
    assert returned_rank(batch, [intent], [job]) == 1
    assert obligations(batch, [intent], [job]) == []


@pytest.mark.parametrize('field,value', [
    ('task_id', 'old-task'), ('evidence_id', 'other-evidence'), ('generation', 1)])
def test_old_intent_or_physical_scope_cannot_promote_active_batch(field, value):
    _, _, batch, intent, job = fixture()
    intent['scope'][field] = value
    assert obligations(batch, [intent], [job]) == []
    _, _, batch, intent, job = fixture()
    job[field] = value
    assert obligations(batch, [intent], [job]) == []


def test_case_source_run_membership_and_current_task_are_required():
    task, evidence, batch, intent, job = fixture()
    for change in ({'case_id': 'other-case'}, {'id': 'unlinked-intent'}):
        assert obligations(batch, [{**intent, **change}], [job]) == []
    intent['scope']['source_run'] = 'old-run'
    assert obligations(batch, [intent], [job]) == []
    _, _, batch, intent, job = fixture()
    assert obligations(batch, [intent], [job], task={**task, 'superseded': True}) == []
    assert obligations(batch, [intent], [job], task={**task, 'retry_generation': 3}) == []
    assert obligations(batch, [intent], [job], task={**task, 'evidence_id': 'other-evidence'}) == []
    assert obligations(batch, [intent], [job], evidence={**evidence, 'connected': False}) == []
    assert returned_rank({'job_ids': ['job']}, [intent], [job]) == 1


def test_logical_dossier_owner_must_exist_in_active_scope():
    _, _, batch, intent, job = fixture()
    owner = {k: batch[k] for k in ('case_id', 'task_id', 'evidence_id', 'generation')}
    assert returned_rank(batch, [intent], [job], dossiers={'dossier-a': owner}) == 0
    assert returned_rank(batch, [intent], [job], dossiers={}) == 1
    for field, value in (('case_id', 'other-case'), ('task_id', 'old-task'),
                         ('evidence_id', 'other-evidence'), ('generation', 1)):
        assert returned_rank(batch, [intent], [job], dossiers={'dossier-a': {**owner, field: value}}) == 1


def test_waiting_for_another_job_cannot_claim_the_returned_batch_can_advance():
    _, _, batch, intent, job = fixture()
    waiting = {**job, 'id': 'waiting', 'status': 'submitted', 'worker_status': 'running',
               'observation_ids': [], 'result_scope': {}, 'test_intent_ids': []}
    batch.update(status='await_checks', job_ids=['job', 'waiting'])
    row = obligations(batch, [intent], [job, waiting])[0]
    assert not row['can_advance_now'] and row['waiting_reason'] == 'worker_result_pending'
    assert returned_rank(batch, [intent], [job, waiting]) == 1
    waiting['status'] = 'admitted'
    assert returned_rank(batch, [intent], [job, waiting]) == 1
    waiting['status'] = 'ingested'
    assert obligations(batch, [intent], [job, waiting])[0]['can_advance_now']


def test_unusable_scope_or_missing_required_context_has_no_returned_promotion():
    _, _, batch, intent, job = fixture()
    job['result_scope'] = {}
    intent['result_scope']['scope'] = {}
    row = obligations(batch, [intent], [job])[0]
    assert not row['can_advance_now'] and row['waiting_reason'] == 'result_scope_unavailable'
    _, _, batch, intent, job = fixture()
    intent['admission'] = {'design': {'required_observation_ids': ['missing-input']}}
    row = obligations(batch, [intent], [job])[0]
    assert not row['can_advance_now'] and row['waiting_reason'] == 'required_context_unavailable'
    intent['admission']['design'] = {'baseline_observation_ids': ['missing-baseline']}
    assert not obligations(batch, [intent], [job])[0]['can_advance_now']


def test_partial_empty_result_is_reviewable_as_scope_not_absence_or_support():
    _, _, batch, intent, job = fixture()
    job['observation_ids'] = []
    intent['result_scope']['observation_ids'] = []
    job['result_scope'].update(truncated=True, hit_count=0)
    row = obligations(batch, [intent], [job])[0]
    assert row['can_advance_now']
    assert not ({'outcome', 'judgment', 'importance', 'probability'} & set(row))
    job['result_status'] = 'failed'
    job['result_scope'].update(status='failed', failure={'code': 'path_not_resolved', 'retryable': False})
    assert obligations(batch, [intent], [job])[0]['can_advance_now']


def test_stale_assessment_or_result_snapshot_never_satisfies_current_contract():
    _, _, batch, intent, job = fixture()
    owner = job['contracts'][0]
    intent.update(assessment_status='assessed', latest_assessment={
        'check_id': job['id'], 'dossier_id': owner['dossier_id'], 'contract_id': owner['contract_id'],
        'outcome': 'inconclusive', 'evaluation_status': 'assessed'},
        assessment_result_revision=result_revision(intent['result_scope']))
    assert obligations(batch, [intent], [job]) == []
    intent['assessment_result_revision'] = 'old-result-version'
    assert obligations(batch, [intent], [job])[0]['can_advance_now']
    intent['latest_assessment']['dossier_id'] = 'wrong-owner'
    assert obligations(batch, [intent], [job])[0]['can_advance_now']
    job = deepcopy(job)
    job['result_scope']['complete'] = True
    row = obligations(batch, [intent], [job])[0]
    assert not row['can_advance_now'] and row['waiting_reason'] == 'returned_scope_changed'


def test_returned_sources_must_still_be_available():
    _, _, batch, intent, job = fixture()
    rows = returned_obligations(batch, [intent], [job], available_observation_ids=set())
    assert not rows[0]['can_advance_now']
    assert rows[0]['waiting_reason'] == 'returned_sources_unavailable'


def setup_scheduler(tmp_path):
    task, evidence, batch, intent, job = fixture()
    store = Store(tmp_path/'scheduling.db')
    saved_task = store.add('task', 'case', evidence_id='evidence', retry_generation=2,
                           review_queue_policy='returned-first-v1')
    for row in (batch, intent['scope'], job):
        row['task_id'] = saved_task['id']
    task = saved_task
    saved_batch = store.add('dossier_batch', 'case', **{k: v for k, v in batch.items() if k not in ('id', 'case_id', 'created_at')})
    batch = saved_batch
    baseline = store.add('dossier_batch', 'case', task_id=task['id'], evidence_id='evidence', generation=2,
        dossier_ids=['fresh'], job_ids=[], status='pending', round=0, attempts=0, baseline=True)
    return store, task, evidence, batch, baseline, intent, job


def test_finite_returned_streak_preserves_new_exploration_and_records_reason(tmp_path):
    store, task, evidence, batch, baseline, intent, job = setup_scheduler(tmp_path)
    selected = []
    for _ in range(RETURNED_STREAK_LIMIT + 1):
        queue = order_queue([batch, baseline], [intent], [job], store.get(task['id']),
            lambda b: (b['id'],), evidence=evidence, available_observation_ids={'returned-record'})
        record_selection(store, 'case', store.get(task['id']), queue[0])
        selected.append(queue[0]['id'])
    assert selected == [batch['id']]*RETURNED_STREAK_LIMIT + [baseline['id']]
    receipt = store.list('receipt', 'case')[-1]
    assert receipt['reason'] == 'finite_returned_streak'
    assert receipt['streak_limit'] == RETURNED_STREAK_LIMIT
    assert not store.list('review_input', 'case')
    assert not store.list('investigation_job', 'case')
    assert store.get(task['id'])['returned_selection_streak'] == 0


def test_waiting_worker_yields_then_gets_bounded_poll_opportunity(tmp_path):
    store, task, evidence, batch, baseline, intent, job = setup_scheduler(tmp_path)
    batch = store.update(batch['id'], status='await_checks')
    job['status'] = 'submitted'
    selected = []
    for _ in range(WORKER_POLL_INTERVAL + 1):
        task = store.get(task['id'])
        batch = store.get(batch['id'])
        queue = order_queue([batch, baseline], [intent], [job], task,
            lambda b: (b['status'] != 'await_checks', b['id']), evidence=evidence,
            available_observation_ids={'returned-record'})
        record_selection(store, 'case', task, queue[0])
        selected.append(queue[0]['id'])
    assert selected == [baseline['id']]*WORKER_POLL_INTERVAL + [batch['id']]
    assert store.list('receipt', 'case')[-1]['reason'] == 'worker_poll_fairness'


def test_queue_preparation_and_baseline_selection_have_no_scheduling_write(tmp_path):
    store, task, evidence, batch, baseline, intent, job = setup_scheduler(tmp_path)
    for _ in range(3):
        queue = order_queue([batch, baseline], [intent], [job], task, lambda b: (b['id'],), evidence=evidence)
    assert not store.list('receipt', 'case')
    record_selection(store, 'case', {**task, 'review_queue_policy': 'question-priority-v1'}, queue[0])
    assert not store.list('receipt', 'case')


def test_real_queue_hook_keeps_default_order_and_enables_only_explicit_policy(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from workbench.dossiers import _prepare_queue
    store, task, evidence, batch, baseline, intent, job = setup_scheduler(tmp_path)
    monkeypatch.setattr('workbench.question_engine.refresh', lambda *a: {'questions': []})
    monkeypatch.setattr('workbench.dossiers.seed', lambda *a, **k: None)
    monkeypatch.setattr('workbench.question_engine.reopen_deferred', lambda *a: None)
    # Baseline question order intentionally prefers the fresh batch. Returned
    # policy must independently prove the other batch's exact logical owner.
    monkeypatch.setattr('workbench.question_engine.batch_priority', lambda b, *a: (b['id'] != baseline['id'],))
    owner = store.add('dossier', 'case', task_id=task['id'], evidence_id='evidence', generation=2)
    store.update(batch['id'], dossier_ids=[owner['id']])
    job['contracts'][0]['dossier_id'] = owner['id']
    intent['scope']['logical_contract']['dossier_id'] = owner['id']
    stored_job = store.add('investigation_job', 'case',
        **{k: v for k, v in job.items() if k not in ('id', 'case_id')})
    intent['result_scope']['job_id'] = stored_job['id']
    stored_intent = store.add('test_intent', 'case',
        **{k: v for k, v in intent.items() if k not in ('id', 'case_id')})
    store.update(stored_job['id'], test_intent_ids=[stored_intent['id']])
    store.update(batch['id'], job_ids=[stored_job['id']])
    controller = SimpleNamespace(store=store, active_observations=lambda cid: [
        {'id': 'returned-record', 'evidence_id': 'evidence'}])
    task = store.update(task['id'], review_queue_policy='question-priority-v1')
    queue = _prepare_queue(controller, 'case', evidence, task)[-1]
    assert [b['id'] for b in queue] == [baseline['id'], batch['id']]
    assert all('_review_selection' not in b for b in queue)
    task = store.update(task['id'], review_queue_policy='returned-first-v1')
    queue = _prepare_queue(controller, 'case', evidence, task)[-1]
    assert queue[0]['id'] == batch['id']
    assert queue[0]['_review_selection']['reason'] == 'returned_contract_ready'
    assert not store.list('receipt', 'case')


def test_new_intents_keep_distinct_owners_and_assessment_cannot_cross_them(tmp_path):
    store = Store(tmp_path/'logical.db')
    case = store.add('case', '', target_os='linux'); cid = case['id']
    evidence = store.add('evidence', cid, connected=True, signature='fixture')
    task = store.add('task', cid, evidence_id=evidence['id'], retry_generation=0)
    owners = [store.add('dossier', cid, task_id=task['id'], evidence_id=evidence['id'], generation=0,
        status='pending', observation_ids=[], revision=4) for _ in range(2)]
    question = {'id': 'same-question', 'question_key': 'question-key', 'version': 1}
    requests = [{'hypothesis_id': d['id'], 'tool': 'search', 'query': 'fixture',
                 'success_condition': 'same condition', 'refutation_condition': 'same contrary',
                 'inconclusive_condition': 'same limitation'} for d in owners]
    intents = [question_engine.reserve(store, cid, task, question, r, evidence, 'fixture-run') for r in requests]
    assert intents[0]['id'] != intents[1]['id']
    assert intents[0]['scope']['request'] == intents[1]['scope']['request']
    assert intents[0]['scope']['logical_contract']['target_ref'] == {
        'kind': 'dossier', 'id': owners[0]['id'], 'version': 4}
    job = store.add('investigation_job', cid, task_id=task['id'], evidence_id=evidence['id'], generation=0,
        status='ingested', source_run='fixture-run', contracts=[contract(r) for r in requests],
        test_intent_ids=[i['id'] for i in intents], observation_ids=[], result_status='covered',
        result_scope={'complete': True})
    question_engine.finish_intents(store, cid, job)
    assessment = {'check_id': job['id'], **contract(requests[0]), 'outcome': 'inconclusive',
                  'evaluation_status': 'assessed', 'reason': 'bounded fixture'}
    for _ in range(2):
        question_engine.record_check_assessments(store, cid, task, {'check_assessments': [assessment]}, 'receipt')
    assessed, unassessed = [store.get(i['id']) for i in intents]
    assert assessed['assessment_status'] == 'assessed' and len(assessed['assessment_history']) == 1
    assert unassessed['assessment_status'] == 'unassessed'
    assert assessed['assessment_result_revision'] == result_revision(assessed['result_scope'])
    # Changed retained result is not satisfied by the old assessment, including
    # after ingestion resets status while preserving immutable history.
    job = store.update(job['id'], result_scope={'complete': False, 'truncated': True}, result_status='partial')
    question_engine.finish_intents(store, cid, job)
    assert store.get(assessed['id'])['assessment_status'] == 'unassessed'
    assert len(store.get(assessed['id'])['assessment_history']) == 1


@pytest.mark.parametrize('intent_run,job_run', [
    ('old-source-run', 'current-source-run'), ('old-source-run', None),
    (None, 'current-source-run')])
def test_direct_assessment_writer_rejects_source_run_mismatch(tmp_path, intent_run, job_run):
    from workbench import case_memory
    store = Store(tmp_path/'source-scope.db')
    task = {'id': 'task', 'retry_generation': 0}
    condition = contract({'hypothesis_id': 'dossier', 'success_condition': 'fixture condition'})
    scope = {'task_id': 'task', 'evidence_id': 'evidence', 'generation': 0,
             'logical_contract': condition}
    if intent_run is not None:
        scope['source_run'] = intent_run
    intent = case_memory.reserve(store, 'case', {'question_key': 'fixture-question'}, 'search', scope,
        {k: v for k, v in condition.items() if k.endswith('_condition')})
    values = {'task_id': 'task', 'evidence_id': 'evidence', 'generation': 0,
              'status': 'ingested', 'contracts': [condition], 'test_intent_ids': [intent['id']],
              'observation_ids': [], 'result_scope': {'complete': True}}
    if job_run is not None:
        values['source_run'] = job_run
    job = store.add('investigation_job', 'case', **values)
    # Simulate an accidental physical link, bypassing the correct reuse gate.
    # Ingestion cannot authorize propagation into an unrelated source run.
    question_engine.finish_intents(store, 'case', job)
    assessment = {'check_id': job['id'], **condition, 'outcome': 'inconclusive',
                  'evaluation_status': 'assessed', 'reason': 'fixture limitation'}
    question_engine.record_check_assessments(store, 'case', task,
        {'check_assessments': [assessment]}, 'fixture-receipt')
    unchanged = store.get(intent['id'])
    assert unchanged['assessment_status'] == 'unassessed'
    assert not unchanged.get('assessment_history') and not unchanged.get('latest_assessment')
