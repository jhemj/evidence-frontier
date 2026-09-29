from workbench.question_engine import apply_updates, refresh, reserve
from workbench.store import Store


class Controller:
    def __init__(self, store, observations): self.store, self.observations = store, observations
    def active_observations(self, cid): return list(self.observations)


def setup(tmp_path):
    store = Store(tmp_path / 'case.db')
    cid = 'case'; evidence = {'id': 'e', 'signature': 'sig', 'connected': True}
    task = {'id': 'task', 'retry_generation': 0}
    cid = store.add('case', cid, question='test')['id']
    return store, cid, evidence, task


def test_refresh_reopens_shared_object_clock_and_source_changes_not_noise(tmp_path):
    store, cid, evidence, task = setup(tmp_path)
    obs = [{'id': 'o1', 'evidence_id': 'e', 'digest': 'a',
            'fields': {'path': '/tmp/tool', 'partition_offset': 0}}]
    ctl = Controller(store, obs)
    h = {'id': 'h', 'evidence_id': 'e', 'hypothesis_kind': 'dynamic',
         'task_id':task['id'],'generation':0,'text': 'Was the tool executed?', 'observation_ids': ['o1']}
    store.add('hypothesis', cid, **{k:v for k,v in h.items() if k != 'id'})
    first = next(q for q in refresh(ctl, cid, evidence, task)['questions'] if q['source_kind'] == 'hypothesis')
    store.update(first['id'], status='scoped_answered')
    ctl.observations.append({'id': 'noise', 'evidence_id': 'e', 'digest': 'n',
                             'fields': {'path': '/other', 'partition_offset': 0}})
    assert next(q for q in refresh(ctl, cid, evidence, task)['questions'] if q['source_kind'] == 'hypothesis')['status'] == 'scoped_answered'
    ctl.observations[0]['digest'] = 'clock-corrected'
    assert next(q for q in refresh(ctl, cid, evidence, task)['questions'] if q['source_kind'] == 'hypothesis')['status'] == 'reopened'


def test_stale_question_update_is_rejected(tmp_path):
    store, cid, evidence, task = setup(tmp_path)
    obs = [{'id': 'o1', 'evidence_id': 'e', 'digest': 'a', 'fields': {'path': '/x'}}]
    ctl = Controller(store, obs)
    store.add('hypothesis', cid, evidence_id='e', hypothesis_kind='dynamic',
              task_id=task['id'],generation=0,text='Question', observation_ids=['o1'])
    memory = refresh(ctl, cid, evidence, task); q = next(q for q in memory['questions'] if q['source_kind'] == 'hypothesis')
    ctl.observations[0]['digest'] = 'new'; refreshed = next(q for q in refresh(ctl, cid, evidence, task)['questions'] if q['source_kind'] == 'hypothesis')
    decisions_before = len(store.list('decision_revision', cid))
    plan = {'id': 'plan', 'valid_ids': ['o1'], 'question_context': {'questions': [q]},
            'output': {'question_updates': [{'question_id': q['id'], 'status': 'scoped_answered',
                                             'reason': 'answer', 'observation_ids': ['o1']}]}}
    apply_updates(store, cid, task, plan)
    assert len(store.list('decision_revision', cid)) == decisions_before
    assert store.list('receipt', cid)[-1]['receipt_type'] == 'question_update_rejected'
    assert refreshed['status'] == 'reopened'


def test_repeated_physical_intent_dedup_and_cursor_change(tmp_path):
    store, cid, evidence, task = setup(tmp_path)
    q = {'id': 'q', 'question_key': 'q', 'version': 2}
    request = {'tool': 'search', 'query': 'needle', 'cursor': 'a',
               'success_condition': 'positive', 'refutation_condition': '', 'inconclusive_condition': ''}
    a = reserve(store, cid, task, q, request, evidence, 'run')
    b = reserve(store, cid, task, q, request, evidence, 'run')
    assert a['id'] == b['id']
    changed = {**request, 'cursor': 'b'}
    assert reserve(store, cid, task, q, changed, evidence, 'run')['id'] != a['id']


def test_refresh_shares_current_other_task_but_excludes_stale_generations_and_evidence(tmp_path):
    store, cid, evidence, _ = setup(tmp_path)
    task = store.add('task', cid, evidence_id='e', retry_generation=3,
                     status='active')
    other_task = store.add('task', cid, evidence_id='e', retry_generation=3,
                           status='active')
    other_evidence = store.add('evidence', cid, signature='other', connected=True)
    ctl = Controller(store, [])

    def add(name, **fields):
        return store.add('hypothesis', cid, name=name, hypothesis_kind='dynamic',
                         text=name, **fields)

    current = add('current', evidence_id='e', task_id=task['id'], generation=3)
    add('old-generation', evidence_id='e', task_id=task['id'], generation=2)
    other=add('other-task', evidence_id='e', task_id=other_task['id'], generation=3)
    add('unknown-origin', evidence_id='e')
    add('other-evidence', evidence_id=other_evidence['id'], task_id=task['id'], generation=3)
    add('superseded', evidence_id='e', task_id=task['id'], generation=3,
        superseded=True)

    rows = refresh(ctl, cid, evidence, task)['questions']
    dynamic_ids = {q['source_ids'][0] for q in rows if q['source_kind']=='hypothesis'}
    assert dynamic_ids == {current['id'],other['id']}
