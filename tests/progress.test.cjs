const {readFileSync}=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const context=vm.createContext({});
vm.runInContext(readFileSync('dist/progress.js','utf8'),context);
const now=Date.parse('2026-09-15T06:00:00Z');
const s={case:{status:'running'},evidence:[{id:'e'}],task:[{id:'t',evidence_id:'e',action:'ai_judgment',status:'running',started_at:'2026-09-15T05:00:00Z'}],
  dossier:Array.from({length:12},(_,i)=>({task_id:'t',generation:0,status:i<3?'reviewed':i<6?'model_failed':'pending'})),
  dossier_batch:Array.from({length:3},()=>({task_id:'t',generation:0,status:'done'}))};
let p=context.investigationProgress(s,now);
assert.equal(p.eta,3600);assert.equal(p.reviewed,3);assert.equal(p.failed,3);assert.equal(p.percent,50);
s.case.status='paused';assert.equal(context.investigationProgress(s,now).eta,null);
s.case.status='quiescent';s.task[0].status='failed';s.task[0].ended_at='2026-09-15T05:30:00Z';
assert.equal(context.investigationProgress(s,now).elapsed,1800);
s.evidence[0].connected=false;assert.equal(context.investigationProgress(s,now).total,0);
console.log('ETA, failure separation, pause, elapsed freeze and disconnected evidence checks passed');
