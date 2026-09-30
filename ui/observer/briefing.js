(function(root) {
  'use strict';
  // Display-only: one version-bound briefing, never a new hypothesis or plan.
  const objects=(view,type)=>Object.values(view?.objects||{}).filter(o=>o.type===type);
  const explanationAt=h=>h.explanation_history?.entries.find(r=>r.revision===h.ledger_revision)?.at||h.changed_at||'';
  function linkedTests(h,view) {
    if(!h)return [];
    const targets=new Set([h.key,...explicitRelations(view,'explains').filter(r=>r.to===h.key).map(r=>r.from)]);
    return explicitRelations(view,'discriminates').filter(r=>targets.has(r.to)).map(r=>view.objects[r.from])
      .filter((t,i,all)=>t?.type==='test'&&all.findIndex(x=>x.key===t.key)===i);
  }
  function explicitRelations(view,kind){
    return (view?.relations||[]).filter(r=>r.kind===kind&&r.target_scope&&
      r.from_ref?.key===r.from&&r.to_ref?.key===r.to&&
      view.objects[r.from]?.version===r.from_ref.version&&view.objects[r.to]?.version===r.to_ref.version);
  }
  function context({view,current,activity,available,selectedKey,pinned=false,restricted=()=>false}) {
    const ended=!!current.case.execution_end||['complete','completed','quiescent','resource_limit','failed','paused'].includes(current.case.status);
    const real=available&&!ended?activity.items.find(a=>a.state==='running'):null;
    const activeTest=real&&objects(current,'test').find(t=>t.execution==='running'&&t.job_ids.includes(real.id));
    const q=activeTest&&objects(current,'question').find(q=>q.id===activeTest.question_id||q.contexts?.some(c=>c.id===activeTest.question_id));
    const linked=new Set(q?.hypothesis_keys||[]);
    const hypotheses=available?objects(view,'hypothesis').filter(h=>!restricted(h)).sort((a,b)=>
      Number(linked.has(b.key))-Number(linked.has(a.key))||Number(b.assessment_current===true)-Number(a.assessment_current===true)||
      String(explanationAt(b)).localeCompare(String(explanationAt(a)))):[];
    // A past/pinned version cannot become the live explanation of a current job.
    const active=!pinned&&hypotheses.find(h=>linked.has(h.key)&&h.assessment_current&&current.objects[h.key]?.version===h.version&&
      linkedTests(h,current).some(t=>t.key===activeTest.key));
    const viewing=hypotheses.find(h=>h.key===selectedKey);
    const primary=viewing||active||hypotheses[0];
    const tests=linkedTests(primary,view);
    const order={running:0,queued:1,blocked:2,candidate:3,partial:4,succeeded:5,failed:6};
    const related=tests.slice().sort((a,b)=>(order[a.execution]??9)-(order[b.execution]??9)||
      String(b.changed_at||'').localeCompare(String(a.changed_at||'')))[0];
    // Manual selection must not present another hypothesis's job as its test.
    const test=activeTest&&tests.some(t=>t.key===activeTest.key)&&!pinned?activeTest:related;
    return {ended,real,activeTest,active,viewing,primary,hypotheses,test};
  }
  function testExplanation(t,{ended=false}={}) {
    if(!t)return {status:'아직 검사를 정하지 않았어요',purpose:'다음에 무엇을 확인할지 아직 정해지지 않았어요.',outcomes:[]};
    const labels={candidate:'확인 후보 · 아직 시작하지 않았어요',queued:'실행을 기다리고 있어요',running:'지금 확인하고 있어요',
      blocked:'확인에 필요한 자료·도구가 부족해요',partial:'일부 결과만 확보했어요',succeeded:'검사는 끝났어요',
      failed:'검사에 실패했어요'};
    const c=t.conditions||{},outcomes=[];
    // Test success can establish an approved-operation alternative. It must
    // not be relabelled as support for the suspicious explanation by this UI.
    if(c.success_condition)outcomes.push({kind:'support',label:'판단을 좁힐 수 있는 결과',text:c.success_condition});
    if(c.refutation_condition)outcomes.push({kind:'counter',label:'이런 결과면 다른 설명을 살펴봐야 해요',text:c.refutation_condition});
    if(c.inconclusive_condition)outcomes.push({kind:'unknown',label:'이런 경우에는 아직 판단할 수 없어요',text:c.inconclusive_condition});
    return {status:ended?'지난 검사 · 지금은 진행하지 않아요':labels[t.execution]||'검사 상태를 확인하지 못했어요',
      purpose:t.request?.reason||t.design?.expected_update||'검사 목적이 아직 기록되지 않았어요.',outcomes,
      incomplete:t.execution==='partial'||t.result_scope?.truncated===true||t.result_scope?.complete===false,
      assessed:t.assessment_status==='assessed'};
  }
  function claimFor(h,view) {
    if(!h)return null;
    return explicitRelations(view,'explains').filter(r=>r.to===h.key).map(r=>view.objects[r.from])
      .find(c=>c.type==='claim'&&c.validity==='adopted'&&c.display_binding?.status==='bound')||null;
  }
  function composeBundle(h,view,{ended=false,historical=false,restricted=()=>false}={}){
    const c=historical?null:claimFor(h,view);
    const claim=c&&!restricted(c)?c:null;
    const test=historical?null:linkedTests(h,view).filter(t=>!restricted(t)).sort((a,b)=>
      ({running:0,queued:1,blocked:2,candidate:3}[a.execution]??4)-({running:0,queued:1,blocked:2,candidate:3}[b.execution]??4))[0];
    const plan=testExplanation(test,{ended});
    const relation=claim&&explicitRelations(view,'explains').find(r=>r.from===claim.key&&r.to===h.key);
    return {hypothesis:h,claim,test,plan,historical,
      snapshot:view.envelope.projection_revision,
      refs:[h,claim,test].filter(Boolean).map(o=>({key:o.key,version:o.version})),
      fact:claim?factText(claim):null,
      reason:relation?.rationale||h.ranking_reason||h.historical_reason||h.reason||null,
      limitation:!h.assessment_current?'이 설명은 이전 판단이에요. 새 근거를 반영한 판단은 아직 확인되지 않았어요.':
        h.gaps?.[0]||h.counterarguments?.find(Boolean)||null,
      linkageGap:!claim?'이 가설을 뒷받침하는 단서와의 연결은 아직 기록되지 않았어요.':null,
      next:test?plan.purpose:h.next_discriminator||h.historical_next_discriminator||null,
      nextStatus:test?plan.status:'다음 검사 후보 · 실행 계획과는 별개',
      nextGap:!test?'이 가설을 판별할 실제 검사와의 연결은 아직 없어요.':null};
  }
  function timelineBundle(t,view){
    const r=t.representative_claim_ref,c=r&&view.objects[r.key];
    const valid=c&&c.version===r.version&&c.type==='claim'&&c.validity==='adopted'&&
      c.display_binding?.status==='bound'&&t.claim_refs.some(x=>x.key===r.key&&x.version===r.version);
    return {claim:valid?c:null,target:valid?c.key:t.source_ref.key,
      text:valid?factText(c):t.title||'원문 시각 · 해석은 상세에서 확인',
      reason:valid?t.relevance_reason:null};
  }
  function factText(c) {
    const fact=c?.display_binding?.status==='bound'&&c.display_binding.fact;
    if(!fact)return c?.statement||'이 단서가 무엇을 뜻하는지 아직 확인하지 못했어요.';
    const raw=String(fact.value),value=raw.length>140?raw.slice(0,137)+'… (일부 표시)':raw;
    // Only the already bound field's meaning, never product/actor/intent inference.
    const labels={'/fields/command':'명령 문자열','/fields/path':'파일 경로','/fields/user':'계정',
      '/fields/target_user':'대상 계정','/fields/event_id':'이벤트 번호','/fields/network_state':'통신 상태 값'};
    return labels[fact.pointer]?'기록에 '+labels[fact.pointer]+' ‘'+value+'’가 남아 있어요.':
      '기록에 ‘'+value+'’라는 내용이 남아 있어요.';
  }
  // Navigation is view state only. Identity/version checks keep historical
  // explanations separate from live work and from today's source contents.
  class ExplanationHistory {
    constructor(){this.items=[];this.cursor=null;this.cache=new Map();this.identity=null;}
    update(view,hypotheses,primary){
      const e=view.envelope, identity=[e.case_id,e.run_id,e.data_mode,e.source_binding||''].join('|');
      if(this.identity&&this.identity!==identity)throw Error('설명 이력의 사건·차수가 달라요');
      this.identity=identity;
      const old=this.selected;
      const items=[];
      for(const h of hypotheses){
        const manifest=h.explanation_history;
        const latest=manifest?.entries.find(r=>r.revision===h.ledger_revision);
        items.push({token:h.key+'@'+(latest?.version||h.version),at:latest?.at||h.changed_at,key:h.key,
          owner:h,view,archived:false,revision:h.ledger_revision});
        for(const r of manifest?.entries||[])if(r.revision!==h.ledger_revision)
          items.push({token:h.key+'@'+r.version,at:r.at,key:h.key,owner:h,view,
            archived:true,revision:r.revision,entryVersion:r.version});
      }
      const first=items.find(r=>!r.archived&&r.key===primary?.key);
      items.sort((a,b)=>String(b.at||'').localeCompare(String(a.at||''))||b.revision-a.revision||a.token.localeCompare(b.token));
      this.items=first?[first,...items.filter(r=>r!==first)]:items;
      // New snapshots never jump the analyst out of the older explanation.
      // A removed/corrected entry remains explicitly historical, not current.
      if(this.cursor&&!this.items.some(r=>r.token===this.cursor)&&old)this.items.push({...old,removed:true});
      return this.selected;
    }
    get index(){return this.cursor?this.items.findIndex(r=>r.token===this.cursor):0;}
    get selected(){return this.items[this.index]||null;}
    get past(){return !!this.cursor;}
    move(delta){
      if(!this.items.length)return null;
      const index=Math.max(0,Math.min(this.items.length-1,this.index+delta));
      this.cursor=index===0?null:this.items[index].token;
      return this.selected;
    }
    latest(){this.cursor=null;return this.selected;}
    accept(record,response){
      const e=record.view.envelope,r=response?.envelope,ref=response?.owner_ref,entry=response?.entry;
      if(!r||['case_id','run_id','data_mode','source_binding','projection_revision'].some(k=>r[k]!==e[k])||
          ref?.key!==record.key||ref?.version!==record.owner.version||
          response.history_version!==record.owner.explanation_history.version||
          entry?.hypothesis_id!==record.owner.id||entry?.revision!==record.revision||entry?.version!==record.entryVersion||
          entry?.historical!==true||!(response.source_refs||[]).every(r=>record.view.objects[r.key]?.version===r.version))
        throw Error('설명 이력의 버전이 바뀌었어요. 최신 내용을 다시 확인해 주세요.');
      this.cache.set(record.token,response);return response;
    }
    loaded(record=this.selected){return record&&this.cache.get(record.token);}
  }
  const api={linkedTests,explicitRelations,context,testExplanation,claimFor,composeBundle,timelineBundle,factText,ExplanationHistory};
  if(typeof module!=='undefined')module.exports=api;
  else root.ObserverBriefing=api;
})(typeof window!=='undefined'?window:globalThis);
