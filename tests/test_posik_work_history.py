"""Work-based browsing is a passive view, not a new model/worker request."""
import os
from pathlib import Path
import shutil
import subprocess

from workbench.observer_activity import failure_details

ROOT=Path(__file__).resolve().parents[1]


def node(code):
    binary=os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')
    assert binary
    subprocess.run([binary,'-e',code],cwd=ROOT,check=True)


def test_work_history_retains_selection_on_updates_and_cannot_mix_cases():
    node("""
const a=require('node:assert/strict'),{WorkHistory}=require('./ui/observer/view-utils.js'),h=new WorkHistory(),
 e={case_id:'C',run_id:'R',data_mode:'live'},older={id:'O',kind:'tool',state:'failed',at:'2026-01-01T00:00:00Z'},
 active={id:'A',kind:'model',state:'waiting',at:'2026-01-01T00:01:00Z'};
h.update(e,[older,active],active);a.equal(h.selected.id,'A');a.equal(h.past,false);
h.move(1);a.equal(h.selected.id,'O');a.equal(h.past,true);
h.update(e,[{...older,state:'failed',at:'2026-01-01T00:03:00Z'},active],active);
a.equal(h.selected.id,'O');a.equal(h.items.length,2);a.equal(h.selected.state,'failed');
h.latest();a.equal(h.selected.id,'A');a.equal(h.past,false);
h.update({...e,case_id:'OTHER'},[],null);a.equal(h.items.length,0);a.equal(h.selected,null);
h.update(e,[older,active],active);h.select('missing');a.equal(h.selected.id,'A');
""")


def test_typed_path_failure_is_specific_without_inventing_historical_absence():
    failure=failure_details({'kind':'investigation_job','result_scope':{'failure':{'code':'path_not_resolved'}}})
    assert failure['title']=='파일 경로를 찾지 못했어요'
    assert '삭제됐다는 뜻은 아니' in failure['impact']
    generic=failure_details({'kind':'investigation_job','error':'untrusted path_not_resolved text'})
    assert generic['title']=='자료 확인 작업 실패'


def test_recent_failure_reaction_does_not_turn_a_dispatch_timer_into_reading():
    node("""
const a=require('node:assert/strict'),m=require('./ui/observer/moa.js'),v={case:{status:'running'}},
 failed={id:'F',kind:'tool',state:'failed',at:'2026-01-01T00:00:00Z',failure:{title:'파일 경로를 찾지 못했어요'}},
 job={id:'J',kind:'tool',state:'running',title:'read_file',target:'/fixture/file',timer_origin:'dispatch'};
a.equal(m.recentFailure([failed]).id,'F');a.equal(m.recentFailure([{...failed,failure_impact:'resolved'}]),null);
const work=m.activity(v,[job,failed]);a.equal(work.base,'waiting');a.ok(!work.label.includes('읽고 있어요'));
a.ok(work.label.includes('/fixture/file'));a.ok(work.statusNote.includes('완료 결과'));
a.ok(m.activity(v,[{...job,timer_origin:'execution'}]).label.includes('읽고 있어요'));
""")


def test_work_navigation_and_clock_grid_are_local_and_brand_changes_are_scoped():
    js=(ROOT/'ui/observer/observer.js').read_text()
    nav=js.split('function navigateExplanation(')[1].split('function activityRow(')[0]
    assert 'workHistory.move' in nav and 'loadExplanation(' not in nav and 'fetch(' not in nav
    feedback=js.split('function updateFeedback(')[1].split('function renderSummary(')[0]
    assert 'fetch(' not in feedback and 'synchronize(' not in feedback
    assert 'feedbackReaction.update(ObserverMoa.completedItem(items)' in feedback
    assert "base:failed&&waiting?'concerned'" not in feedback
    html=(ROOT/'ui/observer/index.html').read_text()
    assert '<title>forsic · 포식이 조사실</title>' in html
    assert '이전 작업' in html and '포식이가 한 일' in html
    assert 'file-clock-grid' in js
    assert 'X-Requested-With' in (ROOT/'dist/app.js').read_text()
    assert 'name = "evidence-frontier"' in (ROOT/'pyproject.toml').read_text()
