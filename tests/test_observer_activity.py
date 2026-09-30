import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

from scripts.observer_snapshot import capture
from workbench.observer_view import project, validate

ROOT = Path(__file__).resolve().parents[1]


def fixture():
    def row(kind, identity, **kw):
        return dict(kind=kind, id=identity, case_id='CASE-fixture', created_at='2026-01-01T00:00:00Z', **kw)
    records = [row('case', 'CASE-fixture', status='running'), row('evidence', 'E'),
               row('task', 'T', status='running', retry_generation=0)]
    for n in range(3):
        records.append(row('dossier', 'D'+str(n), task_id='T', evidence_id='E', generation=0,
                           title='CHANGED TITLE NOT IN THE REQUEST', status='pending', observation_ids=[]))
    pack = {'secret_prompt': 'MUST NOT EXPORT', 'required_dossiers': [
        {'id': 'D'+str(n), 'title': 'Saved clue '+str(n), 'observation_ids': ['O'+str(n)]} for n in range(3)],
        'observations': [{'id': 'O'+str(n), 'fields': {'path': '/fixture/history',
                          'command': 'inspect --item '+str(n)}} for n in range(3)]}
    records.append(row('review_input', 'I', task_id='T', evidence_id='E', generation=0, pack=pack,
                       activity_target='단서 3개 · 1차 검토', input_sha256='fixture-input'))
    return records


def view(records):
    return validate(project(records, case_id='CASE-fixture', run_id='fixture', data_mode='example',
                            captured_at='2026-01-01T01:00:00Z', ledger_position={}))


def item(v):
    return next(a for a in v['activity']['items'] if a['id'] == 'I')


def test_actual_input_subjects_not_current_claim_titles_or_invented_execution():
    a = item(view(fixture()))
    assert a['state'] == 'input_registered'
    assert a['context']['adopted_fact'] is False
    assert len(a['context']['subjects']) == 3
    assert a['context']['subjects'][2]['examples'][0]['label'] == '명령 기록: inspect --item 2'
    assert 'CHANGED TITLE' not in json.dumps(a)
    assert a['context']['input_id'] == 'I'


def test_explicit_request_start_is_required_for_running_model():
    rows = fixture()
    rows[-1]['request_started_at'] = '2026-01-01T00:01:00Z'
    a = item(view(rows))
    assert a['state'] == 'running' and a['timer_at'] == rows[-1]['request_started_at']
    assert a['timer_origin'] == 'execution'


def test_cross_generation_subjects_and_receipts_do_not_attach():
    rows = fixture()
    rows[3]['generation'] = 1
    rows.append(dict(kind='receipt', id='R', case_id='CASE-fixture', task_id='T', generation=1,
                     input_record_id='I', error='old error'))
    a = item(view(rows))
    assert a['state'] == 'input_registered'
    assert [s['id'] for s in a['context']['subjects']] == ['D1', 'D2']
    assert a['context']['omitted_subject_count'] == 1


def test_validation_failure_names_affected_clue_and_preserves_partial_adoption():
    rows = fixture()
    rows.append(dict(kind='receipt', id='R', case_id='CASE-fixture', task_id='T', generation=0,
                     input_record_id='I', error='invalid literal', failure_category='output_contract',
                     validation_errors=[{'code': 'source_literal_mismatch', 'dossier_id': 'D1'}]))
    a = item(view(rows))
    assert a['state'] == 'failed' and a['failure']['phase'] == 'validation'
    assert a['failure']['subject_ids'] == ['D1']
    assert '별도로 채택' in a['failure']['impact']
    assert '요청 실패' not in a['title'] and a['timer_at'] is None


def test_read_only_capture_exports_bounded_request_examples_not_prompt(tmp_path):
    path = tmp_path/'fixture.sqlite3'
    rows = fixture()
    rows[-1]['pack']['observations'][0]['fields']['excerpt'] = 'x'*50000
    with sqlite3.connect(path) as c:
        c.execute('CREATE TABLE records(id TEXT,kind TEXT,case_id TEXT,created_at TEXT,body TEXT)')
        for r in rows:
            c.execute('INSERT INTO records VALUES(?,?,?,?,?)', (r['id'], r['kind'], r['case_id'], r.get('created_at'), json.dumps(r)))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    a = item(capture(path, 'CASE-fixture', 'fixture'))
    assert len(a['context']['subjects']) == 3
    assert 'MUST NOT EXPORT' not in json.dumps(a)
    assert 'x'*500 not in json.dumps(a)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_browser_work_description_uses_concrete_subjects_and_honest_lifecycle():
    binary = os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')
    assert binary, 'Node is required for display contracts'
    a = item(view(fixture()))
    failed = copy.deepcopy(a)
    failed.update(state='failed', failure={'title': '원문 확인 실패', 'subject_ids': ['D1'],
                                         'reason': '인용 불일치', 'impact': '검증되지 않은 부분은 미채택'})
    script = """
const a=require('node:assert/strict'),m=require(process.argv[1]),[prepared,failed]=JSON.parse(process.argv[2]);
const v={case:{status:'running'}};
let x=m.activity(v,[prepared]);
a.ok(x.label.includes('inspect --item 0'));a.equal(x.subjects.length,3);
a.ok(x.statusNote.includes('실행 상태'));a.ok(!x.label.includes('기다리고'));
x=m.activity(v,[{...prepared,state:'running'}]);a.ok(x.label.includes('검토하고 있어요'));
x=m.failure(failed);a.ok(x.label.includes('inspect --item 1'));a.ok(!x.label.includes('inspect --item 0'));
a.ok(m.activity(v,[failed]).label.includes('inspect --item 1'));
a.equal(m.activity(v,[prepared,failed]).base,'waiting');
"""
    r = subprocess.run([binary, '-e', script, str(ROOT/'ui/observer/moa.js'), json.dumps([a, failed])],
                       capture_output=True, text=True, timeout=15)
    assert r.returncode == 0, r.stdout+r.stderr
