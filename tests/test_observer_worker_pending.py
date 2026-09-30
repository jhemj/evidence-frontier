import json
import os
import shutil
import subprocess
from pathlib import Path

from workbench.observer_view import project, validate


def view(status='running', task_status='queued', worker_key='durable-worker-key'):
    rows = [{'kind': 'case', 'id': 'C', 'case_id': 'C', 'status': status},
            {'kind': 'evidence', 'id': 'E', 'case_id': 'C'},
            {'kind': 'task', 'id': 'T', 'case_id': 'C', 'evidence_id': 'E',
             'status': task_status, 'action': 'linux_scan', 'label': '로그·계정·지속성 내용 조사',
             'worker_job_key': worker_key, 'started_at': '2026-01-01T00:00:00Z'}]
    return validate(project(rows, case_id='C', run_id='fixture', data_mode='example',
        captured_at='2026-01-01T01:00:00Z', ledger_position={}))


def test_worker_reserved_key_is_pending_not_idle_or_proven_execution():
    item = view()['activity']['items'][0]
    assert item['state'] == 'waiting'
    assert item['phase'] == 'worker_status_unobserved'
    assert item['timer_origin'] == 'stage'
    assert item['result_adopted'] is None
    assert not view(worker_key=None)['activity']['items']
    assert not view(task_status='covered')['activity']['items']
    assert view(status='paused')['activity']['items'][0]['state'] == 'interrupted'


def test_pending_stage_user_text_preserves_execution_unknown():
    root = Path(__file__).resolve().parents[1]
    node = os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')
    assert node, 'UI contract tests require Node'
    script = """
const m=require('./ui/observer/moa.js'),a=require('node:assert/strict');
const v=JSON.parse(process.argv[1]), item=v.activity.items[0];
const shown=m.activity(v,v.activity.items);
a.ok(shown.label.includes('로그·계정·지속성 내용 조사'));
a.ok(shown.label.includes('결과를 확인'));
a.ok(shown.statusNote.includes('실제 실행 상태'));
a.ok(!shown.label.includes('실행 중인 작업은 없어요'));
const timed=m.elapsedActivity(v,v.activity.items,{now:Date.parse('2026-01-01T00:00:12Z')});
a.ok(timed.text.startsWith('현재 단계 12초'));
a.equal(timed.timer_basis,'원장에 기록된 현재 검증·채택 단계 시작 기준');
"""
    subprocess.run([node, '-e', script, json.dumps(view())], cwd=root, check=True)


def test_zero_tests_does_not_claim_all_tests_reviewed():
    source = (Path(__file__).resolve().parents[1] / 'ui/observer/observer.js').read_text()
    assert "!values('test',v).length?'판단할 검사는 아직 기록되지 않았어요'" in source
