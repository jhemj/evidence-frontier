"""Optional offline advisory display: untrusted labels and stale distributions."""
import os
from pathlib import Path
import shutil
import subprocess


def test_opinion_dom_is_text_only_and_suppresses_stale_or_live_fixtures():
    root = Path(__file__).resolve().parents[1]
    binary = os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')
    assert binary, 'Node is required for the advisory display contracts'
    script = """
const a=require('node:assert/strict'),{renderOpinion}=require('./ui/observer/decision.js');
class N{
 constructor(tag){this.tag=tag;this.children=[];this.attributes={};this.textContent='';}
 append(...nodes){this.children.push(...nodes);}
 setAttribute(key,value){this.attributes[key]=String(value);}
 set innerHTML(v){throw Error('Unsafe HTML insertion');}
}
const doc={createElement:tag=>new N(tag)},walk=n=>[n,...n.children.flatMap(walk)];
const attack='<img src="https://untrusted.invalid/x" onerror="bad()">';
const view={title:'선택지 비교 · 시험용 예시',basis:'조건부 선택 분포',status:'ok',
 distribution_available:true,provenance:'fixture',data_mode:'example',
 reason:'fixture only',candidates:[{id:'a',label:attack,description:'literal command',probability:.8},
 {id:'b',label:'판단 보류',description:'근거 부족',probability:.2}]};
let tree=walk(renderOpinion(view,doc));
a.equal(tree.filter(n=>n.tag==='meter').length,2);
a.ok(tree.some(n=>n.textContent===attack));a.ok(!tree.some(n=>n.tag==='img'||n.tag==='script'||n.tag==='a'));
a.ok(tree.some(n=>n.textContent.includes('실제 모델 판단 아님')));
for(const patch of [{status:'stale'},{status:'unsupported'},{data_mode:'live'},
 {distribution_available:false}]){
 tree=walk(renderOpinion({...view,...patch},doc));
 a.equal(tree.filter(n=>n.tag==='meter').length,0);
 a.equal(tree.filter(n=>n.textContent==='점수 미제공').length,2);
}
tree=walk(renderOpinion({...view,candidates:view.candidates.map(c=>({...c,probability:null}))},doc));
a.equal(tree.filter(n=>n.tag==='meter').length,0);
"""
    subprocess.run([binary, '-e', script], cwd=root, check=True)
