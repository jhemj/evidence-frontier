import json
import sqlite3

from scripts.audit_reader_cards import audit_snapshot, load_db


def snap(cards, observations, evidence=None):
    return {'case': {'id': 'case-1'}, 'triage': {'version': 'triage-test', 'cards': cards},
            'observations': observations, 'evidence': evidence or [], 'task': [], 'dossiers': []}


def card(cid, ref, **kw):
    return {'id': cid, 'title': kw.pop('title', cid), 'card_summary': 'fact', 'observation_ids': [ref],
            'event_time': {'raw': '2026-01-01T00:00:00.000000001+00:00', 'time_kind': 'occurred'},
            'display': {'collapsed_suggestion': False, 'must_surface': True}, **kw}


def test_refs_disconnected_and_render_anchors_are_audited():
    observations = [{'id': 'o1', 'evidence_id': 'e-gone', 'fields': {}}]
    result = audit_snapshot(snap([card('c1', 'o1', source_anchor_ids=[])], observations, [{'id': 'e-gone', 'connected': False}]))
    assert result['diagnostic_counts']['disconnected_observation_refs'] == 1
    assert result['anchors_verified'] is False


def test_missing_refs_summary_repeat_and_contradiction_are_diagnostics():
    c1 = card('c1', 'missing', title='same', card_summary='', display={'collapsed_suggestion': True, 'must_surface': True})
    c2 = card('c2', 'o2', title='same')
    result = audit_snapshot(snap([c1, c2], [{'id': 'o2', 'fields': {}}]))
    assert 'missing_observation_refs' in result['diagnostic_counts']
    assert 'missing_card_summary' in result['diagnostic_counts']
    assert 'repeated_card_title' in result['diagnostic_counts']
    assert 'collapsed_must_surface_contradiction' in result['diagnostic_counts']


def test_render_anchor_requires_html_and_title_summary_repetition_is_reported():
    c = card('c', 'o', title='same', card_summary='same')
    result = audit_snapshot({**snap([c], [{'id': 'o', 'fields': {}}]), 'rendered_html': '<a id="other"></a>'})
    assert result['anchors_verified'] is True
    assert result['diagnostic_counts']['missing_render_citation_anchor'] == 1
    assert result['diagnostic_counts']['title_summary_repetition'] == 1


def test_db_loader_normalizes_records_and_builds_nonzero_triage(tmp_path):
    dbpath = tmp_path / 'case.sqlite3'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE records (body TEXT, case_id TEXT)')
        rows = [
            {'kind': 'case', 'id': 'case-1', 'target_os': 'linux'},
            {'kind': 'task', 'id': 'task-1', 'retry_generation': 0},
            {'kind': 'observation', 'id': 'obs-1', 'evidence_id': 'ev-1', 'type': 'linux_command', 'fields': {}},
            {'kind': 'dossier', 'id': 'd-1', 'task_id': 'task-1', 'generation': 0, 'status': 'pending', 'title': '단서'},
        ]
        db.executemany('INSERT INTO records VALUES (?,?)', [(json.dumps(r), 'case-1') for r in rows])
    loaded = load_db(dbpath, 'case-1')
    assert loaded['case']['id'] == 'case-1'
    assert loaded['triage']['cards']


def test_nanosecond_order_and_db_free_output(tmp_path):
    cards = [card('late', 'late', title='late'), card('early', 'early', title='early')]
    cards[0]['event_time']['raw'] = '2026-01-01T00:00:00.000000009+00:00'
    cards[1]['event_time']['raw'] = '2026-01-01T00:00:00.000000001+00:00'
    result = audit_snapshot(snap(cards, [{'id': 'late', 'fields': {}}, {'id': 'early', 'fields': {}}]))
    assert result['diagnostic_counts']['non_chronological_nanosecond_order'] == 1
    assert 'raw' not in json.dumps(result)
