"""Exercise the real app's async selection/polling code with controlled responses."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')


def run_js(script):
    if not NODE:
        pytest.skip('Node runtime unavailable')
    # Only suppress automatic startup. Register and exercise the actual poll timer.
    app = (ROOT / 'dist/app.js').read_text().replace('perform(init);', '')
    harness = r'''
const assert=require('node:assert/strict'), vm=require('node:vm');
const elements=new Map(), intervals=new Map();
const context={assert, Date, Map, Promise, console,
  setInterval:(fn,ms)=>intervals.set(ms,fn),setTimeout:()=>1,clearTimeout:()=>{},
  document:{hidden:false,addEventListener(){},getElementById(id){
    if(!elements.has(id))elements.set(id,{hidden:true,textContent:'',dataset:{},
      classList:{add(){},remove(){}},setAttribute(){},removeAttribute(){}});
    return elements.get(id);
  }}, elements, intervals};
vm.createContext(context);
'''
    fixtures = r'''
const requests=[], rendered=[];let caseListReads=0;
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();};
const data=(id,revision='v1')=>({case:{id,status:'running'},view_revision:revision,
  evidence:[],task:[],report:[],coverage:[],hypothesis:[],message:[],claim:[],summary:{}});
api=async(path)=>{
  if(path==='/cases'){++caseListReads;return [{id:'a',name:'A',status:'running'},{id:'b',name:'B',status:'running'}];}
  return new Promise((resolve,reject)=>requests.push({path,resolve,reject}));
};
renderSnapshot=()=>{assert.equal(snapshot.case.id,current);rendered.push(snapshot);};
renderInvestigationProgress=()=>{};
'''
    source = harness + '\nvm.runInContext(' + json.dumps(app) + ',context);\n'
    source += 'const watchdog=setTimeout(()=>{console.error("Async test did not settle");process.exit(1);},3000);\n'
    source += 'vm.runInContext(' + json.dumps(fixtures + '\n(async()=>{' + script + '\n})()') + ',context).then(()=>clearTimeout(watchdog),e=>{clearTimeout(watchdog);console.error(e);process.exitCode=1;});'
    result = subprocess.run([NODE, '-e', source], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_initial_slow_snapshot_and_poll_share_the_pending_request():
    run_js(r'''
      const selection=selectCase('a');await flush();
      for(let i=0;i<5;i++){intervals.get(3500)();await flush();}
      assert.equal(requests.length,1);
      assert.equal(elements.get('toast')?.textContent||'','');
      assert.equal(snapshot,null);
      requests[0].resolve(data('a'));await selection;await flush();
      assert.equal(snapshot.case.id,'a');assert.equal(rendered.length,1);
      assert.equal(caseListReads,2);
      assert.equal(elements.get('toast')?.textContent||'','');
    ''')


def test_case_switch_fetches_new_case_and_discards_late_old_response():
    run_js(r'''
      const a=selectCase('a');await flush();
      const b=selectCase('b');await flush();
      assert.equal(requests.length,2);
      requests[1].resolve(data('b'));await b;
      requests[0].resolve(data('a'));await a;
      assert.equal(snapshot.case.id,'b');assert.equal(rendered.length,1);
      assert.equal(elements.get('workspace').hidden,false);
    ''')


def test_case_switch_back_does_not_accept_older_same_case_response():
    run_js(r'''
      const first=selectCase('a');await flush();
      const middle=selectCase('b');await flush();
      const last=selectCase('a');await flush();
      assert.equal(requests.length,3);
      requests[2].resolve(data('a','new'));await last;
      requests[0].resolve(data('a','old'));requests[1].resolve(data('b'));
      await Promise.all([first,middle]);
      assert.equal(snapshot.view_revision,'new');assert.equal(rendered.length,1);
    ''')


@pytest.mark.parametrize('invalid', ['null', '{}', '{case:null}', "data('other')", "{...data('a'),evidence:null}", "{...data('a'),unchanged:true}"])
def test_invalid_first_snapshot_is_not_adopted_and_can_retry(invalid):
    run_js(r'''
      const selection=selectCase('a');await flush();
      const rejected=assert.rejects(selection,/사건 정보를/);
      requests[0].resolve(''' + invalid + r''');await rejected;
      assert.equal(snapshot,null);assert.equal(rendered.length,0);
      const retry=refresh();await flush();requests[1].resolve(data('a'));await retry;
      assert.equal(snapshot.case.id,'a');assert.equal(elements.get('workspace').hidden,false);
    ''')


def test_delta_preserves_full_snapshot_and_failed_poll_retains_last_good_data():
    run_js(r'''
      const selection=selectCase('a');await flush();requests[0].resolve(data('a'));await selection;
      const full=snapshot;
      const delta=refresh();await flush();assert.ok(requests[1].path.includes('?since=v1'));
      requests[1].resolve({case:{id:'a',status:'paused'},unchanged:true,view_revision:'v1',
        evidence:[],task:[],activity:{items:[]},eta_estimate:{}});await delta;
      assert.equal(snapshot.report,full.report);assert.equal(snapshot.case.status,'paused');
      const last=snapshot, failure=refresh();await flush();
      const rejected=assert.rejects(failure,/offline/);requests[2].reject(Error('offline'));await rejected;
      assert.equal(snapshot,last);
      const retry=refresh();await flush();requests[3].resolve(data('a','v2'));await retry;
      assert.equal(snapshot.view_revision,'v2');
    ''')


def test_actions_cannot_use_previous_case_while_new_case_is_loading():
    run_js(r'''
      const a=selectCase('a');await flush();requests[0].resolve(data('a'));await a;
      const b=selectCase('b');await flush();
      assert.equal(snapshot,null);assert.equal(elements.get('workspace').hidden,true);
      await elements.get('run').onclick();await flush();
      assert.equal(requests.length,2);assert.match(elements.get('toast').textContent,/불러오는 중/);
      requests[1].resolve(data('b'));await b;
    ''')
