"""Current activity and recent result are passive, independent display inputs."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def node(script):
    binary = os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')
    assert binary
    subprocess.run([binary, '-e', script], cwd=ROOT, check=True)


def test_previous_result_excludes_active_requests_and_unvalidated_lifecycle_responses():
    node("""
const a=require('node:assert/strict'),m=require('./ui/observer/moa.js');
const done={id:'D',kind:'tool',state:'succeeded',at:'2026-01-01T00:00:00Z'},
 active={id:'A',kind:'model',state:'waiting',at:'2026-01-01T00:01:00Z'},
 response={id:'R',kind:'model',state:'received',phase:'response_received',at:'2026-01-01T00:02:00Z'};
a.equal(m.completedItem([active,done,response]).id,'D');
a.equal(m.completedItem([active,done,response],{excludeId:'D'}),null);
a.equal(m.completedItem([{...done,at:'unknown'}]),null);
a.equal(m.completedItem([done,{...response,phase:undefined}]).id,'R');
a.equal(m.completedItem([{...done,state:'covered_zero'}]).state,'covered_zero');
a.ok(m.workResult({...response,phase:undefined}).detail.includes('채택 여부는 아직'));
a.ok(m.workResult(active).label.includes('아직 완료 결과'));
""")


def test_old_failure_cannot_override_current_work_and_later_result_clears_reaction():
    node("""
const a=require('node:assert/strict'),m=require('./ui/observer/moa.js'),r=new m.ResultReaction(),
 fail={id:'F',kind:'tool',state:'failed',at:'2026-01-01T00:00:00Z',failure:{title:'경로 확인 실패'}},
 ok={id:'S',kind:'tool',state:'succeeded',at:'2026-01-01T00:01:00Z'},
 work={id:'W',kind:'model',state:'waiting',timer_origin:'dispatch',at:'2026-01-01T00:02:00Z'},
 v={case:{status:'running'}};
a.equal(r.update(fail,{scope:'C/R',now:1}).transient,'none'); // reconnect: do not replay
a.equal(m.activity(v,[work,fail]).base,'thinking');
a.equal(m.activity(v,[fail]).base,'idle'); // warning stays separate
a.equal(r.update(ok,{scope:'C/R',now:2}).transient,'none');
const newer={...fail,id:'F2',at:'2026-01-01T00:03:00Z'};
a.equal(r.update(newer,{scope:'C/R',now:3}).transient,'disappointed');
a.equal(r.update(newer,{scope:'C/R',now:8003}).transient,'none');
a.equal(r.update({...newer,id:'F3'},{scope:'C/R',now:9000}).transient,'disappointed');
a.equal(r.update({...ok,id:'S2'},{scope:'C/R',now:9001}).transient,'none');
a.equal(r.update({...newer,failure_impact:'resolved'},{scope:'C/R',now:9002}).transient,'none');
r.update(null,{scope:'C/R',available:false,now:10000});
a.equal(r.update(newer,{scope:'C/R',now:10001}).transient,'none');
a.equal(r.update({...fail,id:'OTHER'},{scope:'OTHER/R',now:10002}).transient,'none');
""")


def test_result_templates_do_not_promote_partial_search_to_absence_or_proof():
    node("""
const a=require('node:assert/strict'),m=require('./ui/observer/moa.js'),
 search={kind:'tool',title:'search',state:'partial',result_summary:{basis:'retained_tool_result_scope',
 matches_scope:'this_search_page',matches:0,has_more:true}};
const zero=m.workResult(search);a.ok(zero.detail.includes('이번 검색 페이지에서 0건'));
a.ok(zero.limitation.includes('부재를 확정하지'));a.ok(zero.limitation.includes('아직 확인하지 못한'));
a.ok(m.workResult({...search,result_summary:{...search.result_summary,matches:3}}).detail.includes('3건'));
a.ok(m.workResult({...search,result_summary:{...search.result_summary,matches:-1}}).detail.includes('미제공')||
 m.workResult({...search,result_summary:{...search.result_summary,matches:-1}}).detail.includes('아직 제공되지'));
a.ok(m.workResult({kind:'model',state:'succeeded',result_adopted:true}).detail.includes('사건 전체가 입증됐다는 뜻은 아니'));
a.ok(m.workResult({kind:'tool',state:'failed',failure_impact:'resolved'}).label.includes('재시도로 회복'));
a.ok(!m.workResult({kind:'tool',state:'covered',result_summary:{basis:'retained_tool_result_scope',metadata_available:true}}).detail.includes('파일의 보존 메타데이터'));
""")


def test_result_navigation_skips_concurrently_active_requests():
    node("""
const a=require('node:assert/strict'),m=require('./ui/observer/moa.js'),{WorkHistory}=require('./ui/observer/view-utils.js'),h=new WorkHistory(),
 current={id:'C',kind:'tool',state:'waiting',at:'2026-01-01T00:03:00Z'},
 parallel={id:'P',kind:'model',state:'validating',at:'2026-01-01T00:05:00Z'},
 latest={id:'L',kind:'tool',state:'covered',at:'2026-01-01T00:02:00Z'},
 older={id:'O',kind:'model',state:'received',at:'2026-01-01T00:01:00Z'};
h.update({case_id:'C',run_id:'R',data_mode:'live'},[current,parallel,latest,older],current);
const options={eligible:x=>x.id!==h.currentId&&!!m.completedItem([x]),defaultId:'L'};
h.move(1,options);a.equal(h.selected.id,'O');a.equal(h.currentId,'C');
h.move(-1,options);a.equal(h.selected.id,'L');h.latest();a.equal(h.selected.id,'C');
a.equal(h.items.length,4); // all requests remain available in full work history
""")


def test_two_result_bubbles_and_explanation_are_above_the_character():
    html = (ROOT / 'ui/observer/index.html').read_text()
    js = (ROOT / 'ui/observer/observer.js').read_text()
    css = (ROOT / 'ui/observer/gray-theme.css').read_text()
    assert html.count('id="hypothesis-bubbles"') == 1
    assert html.index('id="hypothesis-bubbles"') < html.index('class="work-bubbles"')
    assert html.index('id="previous-work"') < html.index('class="thought-scene"')
    assert html.index('id="guide-work"') < html.index('class="thought-scene"')
    assert 'grid-template-columns:minmax(0,1fr) minmax(0,1fr)' in css
    previous = js.split('function renderPreviousWork(')[1].split('async function loadExplanation(')[0]
    assert 'ObserverMoa.workResult(item)' in previous
    assert 'fetch(' not in previous and 'synchronize(' not in previous
    feedback = js.split('function updateFeedback(')[1].split('function renderSummary(')[0]
    assert 'workHistory.selected' not in feedback
    assert 'base:work.base,...reaction' in feedback
