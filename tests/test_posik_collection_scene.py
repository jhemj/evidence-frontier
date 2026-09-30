"""Display-only lens spirit and collection animation contracts."""
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_collection_animation_obeys_real_stage_and_waiting_uncertainty():
    binary = os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')
    assert binary, 'Node is required for the display contracts'
    script = """
const a=require('node:assert/strict'),m=require('./ui/observer/moa.js'),
  {RendererHost}=require('./ui/observer/renderer.js');
const v={case:{status:'running'}},stage={kind:'task',title:'linux_scan',state:'running'};
a.equal(m.activity(v,[stage]).base,'gathering');
const pending={...stage,state:'waiting',phase:'worker_status_unobserved'};
const shown=m.activity(v,[pending]);
a.equal(shown.base,'gathering_waiting');a.ok(shown.statusNote.includes('실제 실행 상태'));
a.ok(shown.label.includes('결과를 확인'));a.ok(!shown.label.includes('찾고 있어요'));
a.equal(m.activity(v,[]).base,'idle');
a.equal(m.activity(v,[stage],{available:false}).base,'offline');
a.equal(m.activity({case:{status:'paused'}},[stage]).base,'ended');
// A more specific model/tool request takes precedence over the scan parent.
a.equal(m.activity(v,[stage,{kind:'model',state:'running',target:'saved clue'}]).base,'thinking');
a.equal(m.activity(v,[stage,{kind:'tool',state:'running',title:'search',target:'needle',timer_origin:'execution'}]).base,'searching');
const h=new RendererHost(()=>{});h.preferences({motion:true});
for(const base of ['gathering','gathering_waiting']){
 h.update({base});a.equal(h.input.base,base);
 a.deepEqual(Object.keys(h.input).sort(),['base','motionAllowed','severity','transient']);
}
h.preferences({visible:false});a.equal(h.input.motionAllowed,false);
h.preferences({visible:true,reduced:true});a.equal(h.input.motionAllowed,false);
h.preferences({reduced:false,motion:false});a.equal(h.input.motionAllowed,false);
"""
    subprocess.run([binary, '-e', script], cwd=ROOT, check=True)


def test_lens_spirit_is_local_and_animation_never_fakes_progress():
    html = (ROOT / 'ui/observer/index.html').read_text()
    css = (ROOT / 'ui/observer/moa.css').read_text()
    assert 'moa-lens-rim' in html and 'moa-lens-groove' in html
    assert 'moa-antenna' not in html and 'moa-camera-body' not in html
    assert 'moa-archive' in html and 'moa-loose-page one' in html
    assert 'animation:moa-rummage' in css and 'animation:moa-page-sort' in css
    # Sorting belongs only to confirmed running-stage presentation, never unknown worker state.
    assert '[data-base=gathering] .moa-loose-page.one' in css
    assert '[data-base=gathering_waiting] .moa-loose-page.one' not in css
    assert 'prefers-reduced-motion:reduce' in css and '[data-motion=false]' in css
    assert 'http://' not in html and 'https://' not in html
    assert 'fetch(' not in css and 'setInterval' not in css


def test_expression_transforms_use_local_anchors_and_distinct_actions():
    css=(ROOT/'ui/observer/moa.css').read_text()
    assert '.moa-brow{transform-box:fill-box;transform-origin:center}' in css
    for animation in ('moa-look-around','moa-write','moa-compare','moa-baffled',
                      'moa-question-bob','moa-clock-check','moa-notice'):
        assert '@keyframes '+animation in css
    # Nonessential motion still obeys the existing single user/reduced-motion switch.
    assert '.moa[data-motion=false] *' in css
    assert '[data-base=ended] *' in css and '[data-base=offline] *' in css
