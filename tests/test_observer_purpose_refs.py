"""Purpose navigation refs are exact, scoped, bounded, and read-only."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from scripts.observer_snapshot import capture, purpose_sources, PURPOSE_SOURCE_MAX_BYTES
from workbench.observer_view import digest, purpose_observation_ids, project, validate


def oid(number):
    return f'OBSERVATION-{number:012x}'


def row(kind, ident, **kwargs):
    return {'id': ident, 'kind': kind, 'case_id': 'C',
            'created_at': '2026-01-01T00:00:00Z', **kwargs}


def rows(reason=None):
    return [row('case', 'C', status='running'), row('evidence', 'E'),
            row('task', 'T', evidence_id='E', retry_generation=0, status='running'),
            row('investigation_job', 'J', task_id='T', evidence_id='E', generation=0,
                status='submitted', worker_status='running',
                request={'tool': 'read_file', 'path': '/fixture/log',
                    'reason': reason or f'{oid(1)}의 인접 기록을 대조합니다.'})]


def observation(number, **kwargs):
    return row('observation', oid(number), evidence_id='E', type='linux_command',
        fields={'path': '/fixture/log', 'excerpt': 'Recorded string only; not execution success.'}, **kwargs)


def database(tmp_path, data):
    path = tmp_path / 'fixture.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE records(id TEXT PRIMARY KEY,kind TEXT,case_id TEXT,created_at TEXT,body TEXT)')
        for item in data:
            db.execute('INSERT INTO records VALUES(?,?,?,?,?)',
                (item['id'], item['kind'], item['case_id'], item['created_at'], json.dumps(item)))
    return path


def activity(view):
    return next(a for a in view['activity']['items'] if a['id'] == 'J')


def test_capture_binds_canonical_source_and_final_object_version_without_db_changes(tmp_path):
    original = observation(1)
    path = database(tmp_path, rows() + [original])
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    view = capture(path, 'C', 'fixture-run')
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    a = activity(view)
    obj = view['objects']['observation:' + oid(1)]
    assert a['purpose_refs'] == [{'id': oid(1), 'key': obj['key'],
        'version': obj['version'], 'source_version': digest(original)}]
    assert a['purpose_missing_refs'] == [] and a['purpose_ref_limit'] == 32
    assert not a['purpose_refs_truncated']
    assert obj['reference_only'] and obj['excerpt'] == original['fields']['excerpt']
    assert view['timeline'] == [] and view['relations'] == []
    assert view['summary']['adopted_claims'] == 0
    assert validate(view) is view


def test_structured_reference_is_reused_without_duplicate_capture(tmp_path):
    data = rows()
    data[2]['observation_ids'] = [oid(1)]
    original = observation(1)
    path = database(tmp_path, data + [original])
    view = capture(path, 'C', 'fixture-run')
    assert len([o for o in view['objects'].values() if o['type'] == 'observation']) == 1
    assert len(view['timeline']) == 1
    assert not view['objects']['observation:' + oid(1)]['reference_only']
    # The extra purpose reader does no source SQL when originals already exist.
    class NoQueries:
        def execute(self, *args):
            raise AssertionError('Already captured source must not be queried again')
    originals = {original['id']: original}
    saved = deepcopy(data)
    purpose_sources(NoQueries(), data, 'C', originals)
    assert data == saved and originals == {original['id']: original}


@pytest.mark.parametrize('change', ['other_case', 'other_evidence', 'old_source_generation',
                                  'superseded_source_task', 'fake_id', 'unknown_source_task'])
def test_invalid_source_names_remain_unresolved_without_fetching_body(tmp_path, change):
    data = rows()
    source = observation(1)
    if change == 'other_case':
        source['case_id'] = 'OTHER'
    if change == 'other_evidence':
        data.append(row('evidence', 'E-other'))
        source['evidence_id'] = 'E-other'
    if change == 'old_source_generation':
        data.append(row('task', 'INGEST', evidence_id='E', retry_generation=1))
        source.update(task_id='INGEST', generation=0)
    if change == 'superseded_source_task':
        data.append(row('task', 'INGEST', evidence_id='E', superseded=True))
        source.update(task_id='INGEST', generation=0)
    if change == 'unknown_source_task':
        source.update(task_id='missing-task', generation=0)
    if change != 'fake_id':
        data.append(source)
    path = database(tmp_path, data)
    view = capture(path, 'C', 'fixture-run')
    assert 'observation:' + oid(1) not in view['objects']
    assert activity(view)['purpose_refs'] == []
    assert activity(view)['purpose_missing_refs'] == [oid(1)]
    # Query metadata only; the canonical source body must not be fetched.
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA query_only=ON')
        traces = []
        db.set_trace_callback(traces.append)
        current_rows = [r for r in data if r['kind'] != 'observation']
        purpose_sources(db, current_rows, 'C', {})
        assert not any(query.startswith('SELECT body FROM records') for query in traces)


@pytest.mark.parametrize('change', ['old_job_generation', 'superseded_job_task',
                                  'wrong_task_evidence', 'disconnected_evidence', 'missing_job_task'])
def test_invalid_job_scope_does_not_fetch_purpose_sources(tmp_path, change):
    data = rows()
    if change == 'old_job_generation':
        data[2]['retry_generation'] = 1
    if change == 'superseded_job_task':
        data[2]['superseded'] = True
    if change == 'wrong_task_evidence':
        data.append(row('evidence', 'E-other'))
        data[2]['evidence_id'] = 'E-other'
    if change == 'disconnected_evidence':
        data[1]['connected'] = False
    if change == 'missing_job_task':
        data[3]['task_id'] = 'missing-task'
    path = database(tmp_path, data + [observation(1)])
    view = capture(path, 'C', 'fixture-run')
    assert 'observation:' + oid(1) not in view['objects']
    with sqlite3.connect(path) as db:
        traces = []
        db.set_trace_callback(traces.append)
        purpose_sources(db, data, 'C', {})
        assert traces == []


def test_raw_source_can_be_referenced_by_a_different_current_investigation_generation(tmp_path):
    data = rows()
    data[2]['retry_generation'] = data[3]['generation'] = 2
    path = database(tmp_path, data + [observation(1)])
    assert activity(capture(path, 'C', 'fixture-run'))['purpose_refs'][0]['id'] == oid(1)


def test_id_parser_deduplicates_and_rejects_urls_paths_or_prefix_lookalikes():
    identity = oid(1)
    text = (f'({identity}), `{identity}` /{oid(2)} '
            f'https://invalid.example/{oid(3)} NAME-{oid(4)} '
            f'{oid(5)}suffix OBSERVATION-not-an-id {oid(6)}-extra '
            f'https://invalid.example/?id={oid(7)} file:record?id={oid(8)} '
            '<img src="https://invalid.example"> $(do-not-execute)')
    assert purpose_observation_ids(text) == ([identity], False)
    assert purpose_observation_ids(None) == ([], False)


def test_only_first_32_unique_explicit_names_are_captured(tmp_path):
    identities = [oid(i) for i in range(1, 36)]
    data = rows(' '.join(identities)) + [observation(i) for i in range(1, 36)]
    path = database(tmp_path, data)
    view = capture(path, 'C', 'fixture-run')
    a = activity(view)
    assert [ref['id'] for ref in a['purpose_refs']] == identities[:32]
    assert a['purpose_refs_truncated'] and a['purpose_missing_refs'] == []
    assert len(view['objects']) == 32
    assert all('observation:' + identity not in view['objects'] for identity in identities[32:])


def test_large_source_stays_named_unknown_and_smaller_popup_excerpt_is_bounded(tmp_path):
    too_large = observation(1)
    too_large['fields']['excerpt'] = 'x' * PURPOSE_SOURCE_MAX_BYTES
    medium = observation(2)
    medium['fields']['excerpt'] = 'y' * 20000
    data = rows(f'{oid(1)}과 {oid(2)}의 기록을 대조합니다.') + [too_large, medium]
    view = capture(database(tmp_path, data), 'C', 'fixture-run')
    a = activity(view)
    assert a['purpose_missing_refs'] == [oid(1)]
    assert len(a['purpose_refs']) == 1 and a['purpose_refs'][0]['id'] == oid(2)
    obj = view['objects']['observation:' + oid(2)]
    assert len(obj['excerpt']) == 12000 and obj['excerpt_partial']
    assert obj['source_version'] == digest(medium)


@pytest.mark.parametrize('field', ['key', 'version', 'source_version'])
def test_tampered_purpose_ref_cannot_validate_in_same_snapshot(tmp_path, field):
    view = capture(database(tmp_path, rows() + [observation(1)]), 'C', 'fixture-run')
    activity(view)['purpose_refs'][0][field] = 'wrong'
    view['envelope']['projection_revision'] = digest({k: v for k, v in view.items() if k != 'envelope'})
    with pytest.raises(ValueError, match='purpose source reference'):
        validate(view)


def test_direct_projection_reports_missing_names_without_synthesizing_relationships():
    data = rows(f'{oid(1)}과 {oid(2)}를 대조합니다.') + [observation(1)]
    view = project(data, case_id='C', run_id='fixture-run', data_mode='example',
        captured_at='2026-01-01T00:00:00Z', ledger_position={})
    assert validate(view) is view
    assert [r['id'] for r in activity(view)['purpose_refs']] == [oid(1)]
    assert activity(view)['purpose_missing_refs'] == [oid(2)]
    assert view['relations'] == []
