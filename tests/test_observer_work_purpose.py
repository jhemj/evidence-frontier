"""Current tool explanation uses its actual saved request, never another job."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from workbench.observer_view import project, validate

ROOT = Path(__file__).resolve().parents[1]


def test_exact_current_request_reason_reaches_display_and_preserves_limits():
    reason = '인접 원문을 읽어 서로 다른 호출의 기록상 관계를 확인합니다. ' + '제시 범위에 대한 설명. '*12 + '실행 성공이나 승인은 이 검사로 판정할 수 없습니다.'
    rows = [{'kind':'case', 'id':'C', 'case_id':'C', 'status':'running'},
            {'kind':'evidence','id':'E','case_id':'C'},
            {'kind':'task','id':'T','case_id':'C','evidence_id':'E','status':'running'},
            {'kind':'investigation_job','id':'J','case_id':'C','task_id':'T',
             'evidence_id':'E','status':'submitted','worker_status':'running',
             'dispatched_at':'2026-01-01T00:00:00Z',
             'request':{'tool':'read_file','path':'/fixture/log','reason':reason}}]
    v = validate(project(rows, case_id='C', run_id='fixture', data_mode='example',
                         captured_at='2026-01-01T00:00:15Z', ledger_position={}))
    a = next(a for a in v['activity']['items'] if a['id']=='J')
    assert a['purpose'] == reason
    binary = os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')
    assert binary, 'Node is required for the display contract'
    script = """
const a=require('node:assert/strict'),m=require('./ui/observer/moa.js'),v=JSON.parse(process.argv[1]);
const item=v.activity.items.find(x=>x.id==='J'),shown=m.activity(v,v.activity.items);
a.equal(shown.purpose,item.purpose);a.equal(shown.purpose_ref,'J');
a.ok(shown.purpose.endsWith('판정할 수 없습니다.'));a.ok(shown.label.includes('/fixture/log'));
a.equal(m.activity(v,[{...item,purpose:null}]).purpose,null);
a.ok(m.activity(v,[{...item,purpose:null}]).why.includes('기록되지'));
a.equal(m.activity(v,[{...item,state:'succeeded'}]).purpose,undefined);
a.equal(m.activity(v,[item],{available:false}).purpose,undefined);
a.equal(m.activity({case:{status:'paused'}},[item]).purpose,undefined);
const model={id:'M',kind:'model',state:'running',target:'different clue'};
a.equal(m.activity(v,[item,model]).purpose_ref,'J');
a.equal(m.activity(v,[{...item,state:'succeeded'},model]).purpose_ref,undefined);
"""
    subprocess.run([binary, '-e', script, json.dumps(v)], cwd=ROOT, check=True)
    source=(ROOT/'ui/observer/observer.js').read_text()
    assert "why.dataset.workId=workExplanation.purpose_ref" in source
    assert "appendPurpose(text,ObserverMoa.currentItem(currentActivity().items),state.current)" in source
