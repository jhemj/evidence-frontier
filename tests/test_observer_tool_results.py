"""Tool summaries are returned-scope metadata, not AI conclusions."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from scripts.observer_snapshot import capture
from workbench.observer_activity import tool_result_summary, lifecycle_activities
from workbench.observer_view import digest, project, validate


CASE = 'CASE-tool-summary'


def fixture():
    def row(kind, identity, **kw):
        return {'kind': kind, 'id': identity, 'case_id': CASE,
            'created_at': '2026-01-01T00:00:00Z', **kw}
    return [row('case', CASE, status='running'), row('evidence', 'E'),
        row('task', 'T', evidence_id='E', status='running', retry_generation=0),
        row('observation', 'O1', evidence_id='E', type='linux_literal_match', fields={'path': '/fixture/source'}),
        row('investigation_job', 'J', task_id='T', evidence_id='E', generation=0,
            status='ingested', result_status='partial', ended_at='2026-01-01T00:02:00Z',
            dispatched_at='2026-01-01T00:01:00Z', observation_ids=['O1'],
            request={'tool': 'search', 'query': 'fixture', 'reason': 'fixture scope'},
            result_scope={'tool': 'search', 'status': 'partial', 'complete': False, 'truncated': True,
                'matches': 2, 'returned': 1, 'omitted_matches': 1,
                'matches_scope': 'this page scan only; not total matches',
                'has_more': True, 'remaining_matches_unknown': True, 'unknown_time_excluded': 4,
                'next_cursor': 'PRIVATE CURSOR', 'continuation_request': {'private': 'PRIVATE REQUEST'},
                'error': 'PRIVATE ERROR DETAIL'})]


def projected(rows):
    return validate(project(rows, case_id=CASE, run_id='tool-summary-fixture', data_mode='example',
        captured_at='2026-01-01T00:03:00Z', ledger_position={}))


def item(view):
    return next(a for a in view['activity']['items'] if a['id'] == 'J')


def test_search_result_summary_is_a_partial_page_not_global_absence_or_independence():
    a = item(projected(fixture()))
    s = a['result_summary']
    assert s['metadata_available'] is True
    assert s['status'] == 'partial' and s['complete'] is False and s['truncated'] is True
    assert (s['matches'], s['returned'], s['omitted_matches']) == (2, 1, 1)
    assert s['matches_scope'] == 'this_search_page'
    assert s['has_more'] is True and s['remaining_matches_unknown'] is True
    assert s['unknown_time_excluded'] == 4
    assert s['recorded_reference_count'] == 1
    assert s['interpretation'] == 'tool_result_only_not_hypothesis_judgment'
    assert a['completed_at'] == '2026-01-01T00:02:00Z'
    assert 'PRIVATE' not in json.dumps(a)
    ref = a['result_refs'][0]
    assert ref == {'id': 'O1', 'key': 'observation:O1',
        'version': projected(fixture())['objects']['observation:O1']['version'],
        'source_version': projected(fixture())['objects']['observation:O1']['source_version']}


def test_zero_page_retains_count_and_never_claims_absence_or_refutation():
    rows = fixture()
    job = rows[-1]
    job.update(result_status='covered_zero', observation_ids=[])
    job['result_scope'].update(status='covered_zero', complete=True, truncated=False,
        matches=0, returned=0, omitted_matches=0, has_more=False, remaining_matches_unknown=False)
    a = item(projected(rows))
    assert a['result_summary']['returned'] == 0
    assert a['result_summary']['matches_scope'] == 'this_search_page'
    assert a['result_summary']['interpretation'] == 'tool_result_only_not_hypothesis_judgment'
    assert a['result_refs'] == []


@pytest.mark.parametrize('key,value', [('matches', True), ('matches', -1), ('returned', '1'),
    ('omitted_matches', -1), ('has_more', 1), ('truncated', 1), ('unknown_time_excluded', -1)])
def test_invalid_count_or_boolean_is_unprovided_not_coerced(key, value):
    job = fixture()[-1]
    job['result_scope'][key] = value
    assert tool_result_summary(job)[key] is None


@pytest.mark.parametrize('change', ['missing_scope', 'wrong_tool', 'wrong_status', 'wrong_complete',
    'nonterminal', 'unknown_match_scope', 'inconsistent_counts'])
def test_missing_or_conflicting_scope_never_synthesizes_summary(change):
    job = fixture()[-1]
    if change == 'missing_scope': job.pop('result_scope')
    elif change == 'wrong_tool': job['result_scope']['tool'] = 'read_file'
    elif change == 'wrong_status': job['result_scope']['status'] = 'covered'
    elif change == 'wrong_complete': job['result_scope']['complete'] = True
    elif change == 'nonterminal': job['status'] = 'submitted'
    elif change == 'unknown_match_scope': job['result_scope']['matches_scope'] = 'all evidence'
    elif change == 'inconsistent_counts': job['result_scope']['returned'] = 8
    s = tool_result_summary(job)
    assert s['matches'] is None and s['returned'] is None
    if change not in ('unknown_match_scope', 'inconsistent_counts'):
        assert s['metadata_available'] is False


def test_path_failure_reason_uses_only_known_typed_code_not_raw_error():
    job = fixture()[-1]
    job.update(result_status='failed')
    job['result_scope'].update(status='failed', failure={'code': 'path_not_resolved', 'retryable': False})
    s = tool_result_summary(job)
    assert s['failure_code'] == 'path_not_resolved' and s['retryable'] is False
    assert s['complete'] is False
    job['result_scope'].pop('failure')
    job['result_scope']['error'] = 'path_not_resolved in untrusted message'
    assert tool_result_summary(job)['failure_code'] is None


@pytest.mark.parametrize('change', ['wrong_evidence', 'wrong_generation', 'missing', 'not_ingested'])
def test_foreign_or_missing_result_reference_stays_explicitly_unresolved(change):
    rows = fixture()
    if change == 'wrong_evidence': rows[3]['evidence_id'] = 'OTHER'
    elif change == 'wrong_generation': rows[3].update(task_id='T', generation=1)
    elif change == 'missing': rows.remove(rows[3])
    elif change == 'not_ingested': rows[-1]['status'] = 'received'
    a = item(projected(rows))
    assert a['result_refs'] == [] and a['result_missing_refs'] == ['O1']
    if change == 'not_ingested':
        assert a['result_adopted'] is False
        assert a['result_summary']['recorded_reference_count'] is None


def test_result_refs_use_finalized_versions_and_are_bounded_without_independence_promotion():
    rows = fixture()
    original = rows[-1]
    ids = ['O'+str(i) for i in range(40)]
    original['observation_ids'] = ids + [ids[0]]
    template = rows[3]
    rows[3:4] = [{**deepcopy(template), 'id': ident} for ident in ids]
    v = projected(rows)
    a = item(v)
    assert a['result_summary']['recorded_reference_count'] == 40
    assert a['result_ref_limit'] == 32 and a['result_refs_truncated'] is True
    assert len(a['result_refs']) == 32
    for ref in a['result_refs']:
        assert ref['version'] == v['objects'][ref['key']]['version']
    assert not any(r.get('kind') in ('explains', 'discriminates') for r in v['relations'])


@pytest.mark.parametrize('key', ['key', 'version', 'source_version'])
def test_stale_or_mixed_result_ref_fails_snapshot_validation(key):
    v = projected(fixture())
    item(v)['result_refs'][0][key] = 'OTHER'
    v['envelope']['projection_revision'] = digest({k: val for k, val in v.items() if k != 'envelope'})
    with pytest.raises(ValueError, match='Incompatible tool result reference'):
        validate(v)


def test_completed_at_never_falls_back_to_dispatch_or_registration():
    rows = fixture()
    rows[-1].pop('ended_at')
    a = item(projected(rows))
    assert a['at'] == rows[-1]['dispatched_at']
    assert a['completed_at'] is None
    rows[-1].update(status='submitted', worker_status='running')
    assert item(projected(rows))['completed_at'] is None


def test_legacy_model_response_is_not_verified_adoption_and_uses_receipt_time():
    rows = fixture()
    inp = {'kind': 'review_input', 'id': 'I', 'case_id': CASE, 'task_id': 'T', 'generation': 0,
        'created_at': '2026-01-01T00:01:00Z', 'pack': {}}
    rows.extend([inp, {'kind': 'receipt', 'id': 'R', 'case_id': CASE, 'task_id': 'T', 'generation': 0,
        'input_record_id': 'I', 'created_at': '2026-01-01T00:02:00Z'}])
    a = next(a for a in projected(rows)['activity']['items'] if a['id'] == 'I')
    assert a['state'] == 'received' and a['result_adopted'] is None
    assert a['completed_at'] == '2026-01-01T00:02:00Z'


@pytest.mark.parametrize('phase,completed', [('response_received', False), ('validating', False),
    ('validated_output', False), ('accepted', True), ('partial_accepted', True), ('rejected', True)])
def test_lifecycle_response_and_validation_are_not_completed_work(phase, completed):
    event = {'kind': 'request_lifecycle', 'id': 'L', 'case_id': CASE,
        'lifecycle_version': 'model-request-lifecycle-1', 'attempt_id': 'A', 'seq': 1,
        'event_id': 'EVENT', 'task_id': 'T', 'generation': 0, 'phase': phase,
        'created_at': '2026-01-01T00:02:00Z', 'observed_at': '2026-01-01T00:02:00Z'}
    a = lifecycle_activities([event], [], {}, lambda r: True, digest)[0]
    assert (a['completed_at'] is not None) is completed


def test_capture_uses_saved_scope_and_retained_refs_without_raw_output_or_db_write(tmp_path):
    database = tmp_path/'tool-result.sqlite3'
    rows = fixture()
    rows[-1]['result'] = {'private': 'PRIVATE RAW OUTPUT'}
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE records(id TEXT,kind TEXT,case_id TEXT,created_at TEXT,body TEXT)')
        for r in rows:
            c.execute('INSERT INTO records VALUES(?,?,?,?,?)',
                (r['id'], r['kind'], r['case_id'], r['created_at'], json.dumps(r)))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    v = capture(database, CASE, 'tool-summary-fixture')
    assert item(v)['result_summary']['returned'] == 1
    assert item(v)['result_refs'][0]['key'] == 'observation:O1'
    assert 'PRIVATE' not in json.dumps(item(v))
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
