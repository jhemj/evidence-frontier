from workbench.case_memory import complete, project, reserve, sync
from workbench.store import Store


def test_sync_reopens_only_changed_question_and_keeps_old_revision(tmp_path):
    s = Store(tmp_path / 'case.db')
    first = sync(s, 'c', {'id': 't'}, {'id': 'e'},
                 [{'id': 'o1', 'digest': 'a'}], [{'id': 'h', 'text': 'Was it executed?', 'observation_ids': ['o1']}], [])
    q = first['questions'][0]; q['status'] = 'scoped_answered'
    s.update(next(x['id'] for x in s.list('case_question', 'c')), status='scoped_answered')
    second = sync(s, 'c', {'id': 't'}, {'id': 'e'},
                  [{'id': 'o2', 'digest': 'b'}], [{'id': 'h', 'text': 'Was it executed?', 'observation_ids': ['o2']}], [])
    assert second['questions'][0]['status'] == 'reopened'
    assert len(s.list('case_question', 'c')) == 1
    assert len(s.list('decision_revision', 'c')) == 1


def test_test_intent_digest_includes_scope_and_completion_truth(tmp_path):
    s = Store(tmp_path / 'case.db')
    intent = reserve(s, 'c', {'question_key': 'q'}, 'search', {'query': 'x', 'limit': 8})
    done = complete(s, 'c', intent['id'], {'next_cursor': 'c'}, complete=False)
    assert done['status'] == 'partial' and done['result_complete'] is False
    other = reserve(s, 'c', {'question_key': 'q'}, 'search', {'query': 'x', 'limit': 9})
    assert intent['intent_digest'] != other['intent_digest']


def test_sync_is_idempotent_and_ignores_unrelated_noise(tmp_path):
    s = Store(tmp_path / 'case.db')
    args = ([{'id': 'o', 'digest': 'a'}], [{'id': 'h', 'text': 'question', 'observation_ids': ['o']}], [])
    first = sync(s, 'c', {'id': 't', 'generation': 2}, {'id': 'e'}, *args)
    second = sync(s, 'c', {'id': 't', 'generation': 2}, {'id': 'e'}, *args)
    assert second['questions'][0]['status'] == 'open'
    noisy = sync(s, 'c', {'id': 't', 'generation': 2}, {'id': 'e'},
                 [{'id': 'o', 'digest': 'a'}, {'id': 'noise', 'digest': 'n'}], *args[1:])
    assert noisy['questions'][0]['status'] == 'open'
    assert first['questions'][0]['question_key'] == second['questions'][0]['question_key']


def test_task_generation_and_object_scope_isolation(tmp_path):
    s = Store(tmp_path / 'case.db')
    a = sync(s, 'c', {'id': 't', 'generation': 1}, {'id': 'e'},
             [{'id': 'o1'}], [{'id': 'h', 'text': 'same', 'observation_ids': ['o1']}], [])
    b = sync(s, 'c', {'id': 't', 'generation': 2}, {'id': 'e'},
             [{'id': 'o2'}], [{'id': 'h', 'text': 'same', 'observation_ids': ['o2']}], [])
    assert a['questions'][0]['question_key'] != b['questions'][0]['question_key']
    assert len(project(s, 'c', {'id': 't', 'generation': 1})) == 1
    assert len(project(s, 'c', {'id': 't', 'generation': 2})) == 1


def test_assess_is_immutable_and_rejects_stale_dependency(tmp_path):
    s = Store(tmp_path / 'case.db')
    q = sync(s, 'c', {'id': 't'}, {'id': 'e'},
             [{'id': 'o'}], [{'id': 'h', 'text': 'q', 'observation_ids': ['o']}], [])['questions'][0]
    from workbench.case_memory import assess
    d = assess(s, 'c', q['id'], 'scoped_answered', 'bounded answer', ['o'], dependency_revision=q['dependency_revision'])
    assert d['question_id'] == q['id'] and len(s.list('decision_revision', 'c')) == 1
    try:
        assess(s, 'c', q['id'], 'open', 'stale', dependency_revision='wrong')
    except ValueError as exc:
        assert 'stale' in str(exc)
    else:
        raise AssertionError('stale decision was accepted')


def test_intent_dedup_and_cursor_or_condition_changes(tmp_path):
    s = Store(tmp_path / 'case.db')
    a = reserve(s, 'c', {'question_key': 'q', 'version': 1}, 'search', {'query': 'x', 'cursor': 'a'}, {'complete': False})
    again = reserve(s, 'c', {'question_key': 'q', 'version': 1}, 'search', {'query': 'x', 'cursor': 'a'}, {'complete': False})
    assert a['id'] == again['id']
    changed = reserve(s, 'c', {'question_key': 'q', 'version': 1}, 'search', {'query': 'x', 'cursor': 'b'}, {'complete': False})
    assert changed['id'] != a['id']
    condition = reserve(s, 'c', {'question_key': 'q', 'version': 1}, 'search', {'query': 'x', 'cursor': 'a'}, {'complete': True})
    assert condition['id'] != a['id']


def test_business_id_survives_context_generation_and_own_assessment(tmp_path):
    s=Store(tmp_path/'case.db');obs=[{'id':'o','fields':{'path':'/fixture'}}]
    h={'id':'h','text':'Was it run?','observation_ids':['o']}
    a=sync(s,'c',{'id':'t','generation':1},{'id':'e'},obs,[h])['questions'][0]
    s.update(a['id'],status='scoped_answered')
    changed={**h,'text':'New display title','judgment':'확인','revision':8,
             'assessment_history':[{'reason':'own assessment'}]}
    again=sync(s,'c',{'id':'t','generation':1},{'id':'e'},obs,[changed])['questions'][0]
    assert again['status']=='scoped_answered' and again['dependency_revision']==a['dependency_revision']
    b=sync(s,'c',{'id':'other','generation':2},{'id':'e'},obs,[changed])['questions'][0]
    assert b['id']!=a['id'] and b['business_question_id']==a['business_question_id']
    other=sync(s,'c',{'id':'t','generation':1},{'id':'e'},obs,[{**h,'id':'different'}])['questions'][0]
    assert other['business_question_id']!=a['business_question_id']


def test_explicit_definition_and_evidence_role_changes_reopen(tmp_path):
    s=Store(tmp_path/'case.db');obs=[{'id':'o'}]
    h={'id':'h','question':'Did it run?','observation_ids':['o'],'supporting_evidence_ids':['o']}
    a=sync(s,'c',{'id':'t'},{'id':'e'},obs,[h])['questions'][0]
    b=sync(s,'c',{'id':'t'},{'id':'e'},obs,[{**h,'supporting_evidence_ids':[],'refuting_evidence_ids':['o']}])['questions'][0]
    assert b['status']=='reopened' and b['version']==a['version']+1
    c=sync(s,'c',{'id':'t'},{'id':'e'},obs,[{**h,'question':'Was it approved?'}])['questions'][0]
    assert c['definition_revision']!=a['definition_revision'] and c['business_question_id']==a['business_question_id']


def test_root_parent_and_claim_text_revision_are_explicit(tmp_path):
    s=Store(tmp_path/'case.db');obs=[{'id':'o'}]
    root={'id':'c:e','text':'What occurred?','source_kind':'case_question'}
    claim={'id':'cl','text':'Recorded command','source_kind':'claim','observation_ids':['o']}
    a=sync(s,'c',{'id':'t'},{'id':'e'},obs,[root],claims=[claim])['questions']
    assert a[1]['parent_question_id']==a[0]['business_question_id']
    b=sync(s,'c',{'id':'t'},{'id':'e'},obs,[root],claims=[{**claim,'text':'Corrected narrow claim'}])['questions'][1]
    assert b['status']=='reopened' and b['question']=='Corrected narrow claim'
