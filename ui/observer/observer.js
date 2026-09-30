'use strict';
const state=new ObserverState.ViewState();
const $=id=>document.getElementById(id);
let session=null, selectedQuestion=null, requestPending=false, offline=false, focusedMode=0, pollTimer=null;
let timeLimit=12, uncertainLimit=12;
let observedWork={id:null,since:null};
const explanationHistory=new ObserverBriefing.ExplanationHistory();
const historyRequests=new Set(), historyErrors=new Map();
const sourceCache=new Map();
let purposeSource=null;
const feedbackReaction=new ObserverMoa.ResultReaction();
const workHistory=new ObserverViewUtils.WorkHistory();
const renderer=new ObserverRenderer.RendererHost(({available,failed,hidden})=>{
  const moa=document.querySelector('.moa');moa.hidden=!!hidden||available;
  moa.dataset.base=renderer.input.base;moa.dataset.motion=String(renderer.input.motionAllowed&&!failed);
  moa.dataset.transient=renderer.input.transient;
  $('renderer-status').textContent=failed?'렌더러 오류 · 정적 안내자로 대체':available?'로컬 렌더러 연결됨':renderer.input.motionAllowed?'포식이 · 동작 켜짐':'포식이 · 동작 멈춤';
});
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
renderer.preferences({motion:$('motion').checked,reduced:reducedMotion.matches,visible:!document.hidden});
const previewRenderer=new ObserverRenderer.RendererHost(()=>{
  const m=$('moa-preview-avatar'),p=previewRenderer.input;
  m.dataset.base=p.base;m.dataset.transient=p.transient;m.dataset.motion=String(p.motionAllowed);
});
function previewPreferences(){previewRenderer.preferences({motion:$('motion').checked,reduced:reducedMotion.matches,
  visible:!document.hidden,hidden:!$('moa-preview-dialog').open});}
function previewPose(key){
  const pose=ObserverMoa.previews[key];if(!pose)return;
  previewRenderer.update({...pose,severity:'info'});$('moa-preview-label').textContent=pose.label;
  $('moa-preview-avatar').setAttribute('aria-label','포식이 동작 예시 · '+pose.label);
  for(const b of $('moa-preview-choices').children)b.setAttribute('aria-pressed',String(b.dataset.pose===key));
}
const execution={candidate:'확인 후보',queued:'실행 대기',paused_pending:'일시정지 · 대기 작업 보존',running:'확인 중',blocked:'자료·도구 부족',
  succeeded:'검사 완료',covered:'요청 범위 처리 완료',covered_zero:'검사 완료 · 해당 검색 범위 일치 없음',partial:'일부 결과 확보',failed:'검사 실패',unsupported:'지원되지 않는 검사',waiting:'응답 대기',
  received:'응답 도착 · 검토 필요',validating:'답변·근거 검증 중',adopting:'검증된 결과 반영 중',input_registered:'요청 준비 · 전송 여부 미확인',interrupted:'중단',cancelled:'취소',unknown:'상태 미확인'};
const questions={open:'조사 중',reopened:'재검토',held:'판단 보류',input_wait:'입력 대기',scoped_answered:'현재 범위 종결',contextual:'문맥별 답변',unknown:'미제공'};
const validity={adopted:'검토된 단서',candidate:'검토 전',invalidated:'정정으로 제외',unresolved_references:'근거 확인 필요',historical_assessment:'지난 조사에서 나온 해석'};
const caseStates={running:'조사 중',pause_requested:'일시정지 처리 중',paused:'일시정지',
  complete:'실행 종료',completed:'실행 종료',quiescent:'진행 가능한 작업 없음 · 실행 종료',
  resource_limit:'실행 예산 상한 도달 · 조사 미완료',failed:'실행 실패',created:'시작 전',ready:'시작 전'};
const endReasons={runtime_limit:'실행 시간 상한 도달',job_limit:'작업 수 상한 도달',
  queued_work_completed:'대기 작업 처리 종료 · 질문 규명과 별개',no_runnable_work:'진행 가능한 작업 없음'};
function executionEnd(c){
  const end=c.execution_end;
  return end?(endReasons[end.reason]||'세부 종료 사유 미기록')+(end.ended_at?' · '+dt(end.ended_at):' · 종료 시각 미제공'):'';
}
const types={claim:'확인한 단서',observation:'원본 기록',test:'확인할 내용',hypothesis:'가설',question:'조사 질문',incident:'범위별 침해 판단'};
function node(tag, text, cls) {
  const n=document.createElement(tag);if(text!==undefined && text!==null)n.textContent=String(text);if(cls)n.className=cls;return n;
}
function button(text, action, cls) {
  const b=node('button',text,cls);b.type='button';b.addEventListener('click',action);return b;
}
function badge(text, kind='') {return node('span',text,'badge '+kind);}
function empty(text) {return node('p',text,'empty');}
function dt(value) {
  return value?ObserverViewUtils.clockText(String(value)):'미제공';
}
function clear(id) {$(id).replaceChildren();return $(id);}
function line(label,text){const n=node('div',null,'summary-line');n.append(node('span',label),node('div',text));return n;}
function announce(text){$('announcer').textContent=text;}
function values(type,view=state.displayed){return Object.values(view.objects).filter(o=>o.type===type);}
function openInspection(key){
  if(key)state.select(key);renderSelection();
  if(!$('inspection-dialog').open)$('inspection-dialog').showModal();
}
function refButton(key,label) {
  const o=state.displayed.objects[key];
  if(!o)return node('span','연결되지 않은 참조','note');
  const b=button(label||o.title,()=>{openInspection(key);renderTimeline();},'source-link');b.dataset.key=key;return b;
}
function openPurposeSource(ref,view){
  const o=view.objects[ref.key];
  if(!o||o.type!=='observation'||o.version!==ref.version||o.source_version!==ref.source_version)return;
  const envelope=view.envelope;
  const n=clear('purpose-source-body');purposeSource={ref,envelope};
  n.append(node('h3',o.title),node('p',o.limitation,'note'));
  const dl=node('dl');addDefinition(dl,'원문 위치',o.source_location||o.title);
  for(const [key,value] of Object.entries(o.locator||{}))if(value!==null&&value!==undefined)addDefinition(dl,key,value);
  const technical=node('details');technical.append(node('summary','단서 위치·참조'),node('p',o.id,'source-locator'),dl);n.append(technical);
  const excerpt=node('pre',o.excerpt??'텍스트 발췌가 없는 단서입니다. 원문 위치·참조를 확인해 주세요.','excerpt');n.append(excerpt);
  if(o.excerpt_partial&&envelope.data_mode!=='example'){
    n.append(node('p','현재 보존 원문의 일부를 보여드려요.','note'));
    let loaded='',offset=0;
    const more=button('보존 원문 더 읽기',async()=>{
      more.disabled=true;
      try{
        const p=await read('/api/sources/'+encodeURIComponent(envelope.projection_revision)+'/'+encodeURIComponent(o.id)+'?offset='+offset+'&limit=65536');
        if(p.source_version!==ref.source_version||p.observation_id!==o.id)throw Error('원문 버전 변경');
        if(loaded.length+p.text.length>2000000)throw Error('화면 읽기 한도 도달');
        loaded+=p.text;excerpt.textContent=loaded;offset=p.next_offset;more.hidden=offset===null;more.disabled=false;
      }catch(e){n.append(node('p','원문 추가 읽기 제한: '+e.message,'note'));more.disabled=false;}
    });n.append(more);
  }
  refreshPurposeSource();if(!$('purpose-source-dialog').open)$('purpose-source-dialog').showModal();
}
function refreshPurposeSource(){
  if(!purposeSource)return;
  const {ref,envelope}=purposeSource,o=state.current?.objects[ref.key];
  const current=!offline&&state.sync==='ready'&&state.current.envelope.case_id===envelope.case_id&&
    state.current.envelope.run_id===envelope.run_id&&o?.version===ref.version&&o?.source_version===ref.source_version;
  $('purpose-source-freshness').textContent=current?'이 작업의 설명에 연결된 보존 원문이에요. 열람으로 새 검사를 실행하지 않아요.':
    '열어 둔 원문은 당시 버전이에요. 현재 연결이나 단서 버전이 바뀌었는지 확인해 주세요.';
}
function appendPurpose(textContainer,item,view){
  for(const part of ObserverViewUtils.purposeFragments(item,view)){
    if(part.ref){const b=button(part.text,()=>openPurposeSource(part.ref,view),'inline-source');b.dataset.key=part.ref.key;b.title=view.objects[part.ref.key].title;textContainer.append(b);}
    else textContainer.append(node('span',part.text,part.unresolved?'unresolved-reference':undefined));
  }
}
function safeFreshness() {
  if(offline)return '연결이 끊겼어요 · 마지막으로 받은 내용을 보여드려요';
  if(state.sync!=='ready')return '바뀐 내용을 확인 중이에요 · 아직 최신 내용이 아니에요';
  if(session?.mode==='read_only_live')return session.source_status==='observed'?
    '마지막 확인 '+dt(session.observed_at):'현재 상태를 받지 못했어요 · 마지막 내용을 보여드려요';
  return '지난 조사 기록을 보고 있어요 · 실시간 화면이 아니에요';
}
function currentNarratives(){return offline||session?.mode==='read_only_live'&&session.source_status!=='observed'?[]:state.narratives();}
function currentActivity(){return state.liveActivity||state.current.activity;}
function alerts() {
  const warnings=[];
  if(offline)warnings.push('화면 데이터 연결 끊김. 마지막으로 받은 보존 시점의 내용입니다. 현재 작업 상태는 알 수 없습니다.');
  if(state.sync!=='ready')warnings.push('바뀐 판단과 근거를 다시 확인하고 있어요. 확인이 끝날 때까지 최신 결론으로 보지 말아 주세요.');
  if(session?.mode==='read_only_live'&&session.source_status!=='observed')warnings.push('새 조사 상태를 받지 못했어요. 마지막으로 확인한 시각: '+dt(session.observed_at));
  if(state.pinnedChanged())warnings.push('고정한 내용 이후에 판단이나 근거가 바뀌었어요. 지금 읽는 위치는 유지하고, 최신 내용은 따로 확인할 수 있어요.');
  if(state.changes.length)warnings.push('판단·근거 표시 '+state.changes.length+'건이 바뀌었어요. 무엇이 달라졌는지는 작업 이력에서 확인할 수 있어요.');
  if(state.omittedChanges)warnings.push('이 브라우저의 변경 이력은 최근 200건만 표시합니다. 앞선 '+state.omittedChanges+'건은 원장 이력에서 확인해야 합니다.');
  const failures=state.current?currentActivity().items.filter(a=>a.state==='failed'&&['unresolved','partial'].includes(a.failure_impact)):[];
  for(const a of failures.slice(0,2)){
    const f=ObserverMoa.failure(a);
    warnings.push('영향이 남은 실패: '+f.label+' — '+f.reason+' '+f.impact);
  }
  if(failures.length>2)warnings.push('다른 실패 기록 '+(failures.length-2)+'건은 작업 이력에서 확인할 수 있어요.');
  const reportFailures=(state.current?.report_finalizations||[]).filter(r=>r.status==='failed'||r.status==='superseded');
  if(reportFailures.length)warnings.push('부분 보고서 마감 '+reportFailures.length+'건 실패·범위 변경. 보고서 생성과 조사 종료는 별개입니다. '+(reportFailures.at(-1).error||''));
  const c=clear('critical');c.hidden=!warnings.length;for(const w of warnings)c.append(node('p',w));
  $('connection').textContent=safeFreshness();
  const available=!offline&&state.sync==='ready'&&!(session?.mode==='read_only_live'&&session.source_status!=='observed');
  const appearance=ObserverMoa.judgment(state.current,{available,restricted:o=>state.isRestricted(o)});
  document.querySelector('.moa').dataset.level=appearance.level;
  document.querySelector('.moa').setAttribute('aria-label','포식이 · '+appearance.label);
  $('moa-verdict').textContent=appearance.label;$('moa-verdict-scope').textContent=appearance.scope;
  $('moa-verdict-scope').hidden=!appearance.scope;
  $('moa-verdict-detail').hidden=!appearance.key;
  $('moa-verdict-detail').onclick=()=>appearance.key&&openInspection(appearance.key);
  updateFeedback(available);
}
function updateFeedback(available,now=Date.now()){
  const items=currentActivity().items,work=ObserverMoa.activity(state.current,items,{available});
  const ended=['ended','offline'].includes(work.base),failed=available&&!ended?ObserverMoa.recentFailure(items):null;
  const envelope=state.current.envelope;
  const reaction=feedbackReaction.update(ObserverMoa.completedItem(items),{
    scope:JSON.stringify([envelope.case_id,envelope.run_id,envelope.data_mode]),available:available&&!ended,now});
  renderer.update({base:work.base,...reaction});
  const message=$('guide-feedback');if(!message)return;
  message.hidden=!failed;
  if(failed&&message.dataset.failureId!==failed.id){
    const f=ObserverMoa.failure(failed);message.dataset.failureId=failed.id;
    message.replaceChildren(badge('이전 실패 · 아직 확인할 내용이 남아 있어요','warn'),node('strong',f.label),
      node('p',f.reason),node('p',f.impact,'note'),button('이 작업 보기',()=>{workHistory.select(failed.id);render();}));
  }
}
function renderSummary() {
  const v=state.displayed, s=v.summary;
  const narrative=currentNarratives().find(item=>item.refs.length);
  $('mode').textContent=v.envelope.data_mode==='example'?'예시 · 실제 사건 아님':v.envelope.data_mode==='live'?'실제 조사 · 읽기 전용 관찰':'실데이터 보존 재생';
  $('case-name').textContent=v.case.name;$('run-name').textContent=v.envelope.run_id;
  $('basis').textContent=s.last_meaningful_change?'최근 단서 검토 '+dt(s.last_meaningful_change):'단서 검토 시점은 아직 없어요';
  $('revision').textContent=v.envelope.case_id+' / '+v.envelope.run_id+' / snapshot '+v.envelope.projection_revision.slice(0,12);
  $('summary-title').textContent=state.pinned?'과거 판단 열람':'조사 현황';
  const summary=clear('summary');
  summary.append(line('조사 상태',caseStates[v.case.status]||'상태 미확인'),
    line('남은 확인',!values('test',v).length?'판단할 검사는 아직 기록되지 않았어요':s.unassessed_tests?'아직 판단하지 못한 검사가 있어요 · '+s.unassessed_tests+'개':
      '검사 해석이 모두 기록돼 있어요 · 사건 규명 완료와는 달라요'));
  $('pin').setAttribute('aria-pressed',String(!!state.pinned));$('pin').textContent=state.pinned?'최신 내용으로 돌아가기':'지금 내용 고정';
  $('report-count').textContent=v.reports.length?v.reports.length+'개':'미생성';
  if(v.case.execution_end)summary.append(line('실행 종료',executionEnd(v.case)));
}
function renderNarrative() {
  const n=clear('narrative'), rail=clear('hypothesis-bubbles'), work=clear('guide-work');
  const available=!offline&&state.sync==='ready'&&!(session?.mode==='read_only_live'&&session.source_status!=='observed');
  const live=ObserverBriefing.context({view:state.displayed,current:state.current,activity:currentActivity(),available,
    pinned:!!state.pinned,restricted:h=>state.isRestricted(h)});
  if(available)explanationHistory.update(state.displayed,live.hypotheses,live.primary);
  const record=available?explanationHistory.selected:null, past=explanationHistory.past;
  const saved=record?.archived?explanationHistory.loaded(record):null;
  const entry=saved?.entry;
  const ctx=ObserverBriefing.context({view:state.displayed,current:state.current,activity:currentActivity(),available,
    selectedKey:record?.key,pinned:!!state.pinned||past,restricted:h=>state.isRestricted(h)});
  const primary=record?.archived?(entry?{...record.owner,title:entry.title,statement:entry.statement,scope:entry.scope,
    reason:entry.reason,historical_reason:entry.reason,historical_next_discriminator:entry.next_candidate,
    next_discriminator:null,ranking_reason:null,assessment_current:false,counterarguments:[entry.alternative],
    gaps:[],judgment:entry.judgment,lifecycle:entry.lifecycle,changed_at:entry.at}:null):record?.owner;
  const {real,active:activeExplanation}=live;
  const workExplanation=ObserverMoa.activity(state.current,currentActivity().items,{available});
  workHistory.update(state.current.envelope,currentActivity().items,ObserverMoa.currentItem(currentActivity().items));
  const priorWork=workHistory.past?workHistory.selected:null;
  const resultItems=workHistory.items.filter(a=>a.id!==workHistory.currentId&&ObserverMoa.completedItem([a]));
  const resultIndex=Math.max(0,resultItems.findIndex(a=>a.id===priorWork?.id));
  $('workspace').classList.toggle('reading-past',!!priorWork);
  $('explanation-position').textContent=resultItems.length?(priorWork?'지난 조사 결과':'최근 조사 결과')+' · '+(resultIndex+1)+' / '+resultItems.length:'아직 이전 결과가 없어요';
  $('explanation-at').textContent=(priorWork||resultItems[0])?.at?dt((priorWork||resultItems[0]).at):'';
  $('explanation-older').disabled=!available||resultIndex>=resultItems.length-1;
  $('explanation-newer').disabled=!available||!workHistory.past||resultIndex===0;
  $('explanation-current').hidden=!workHistory.past;
  const historyNote=$('explanation-history-note');historyNote.hidden=!workHistory.past;
  historyNote.textContent='지난 작업을 보고 있어요. ‘현재 조사 중’에는 지금 진행 중인 작업을 계속 보여드려요. 과거 결과는 현재 판단과 달라요.';
  work.append(badge(!available?'연결 확인 중':ctx.ended?'현재 조사 · 멈춤':'현재 조사 중'));
  const liveText=node('p',workExplanation.label);liveText.id='guide-work-live';work.append(liveText);updateLiveWork();
  if(workExplanation.purpose){
    const why=node('div',null,'work-purpose');why.dataset.workId=workExplanation.purpose_ref;
    const text=node('p');appendPurpose(text,ObserverMoa.currentItem(currentActivity().items),state.current);
    why.append(node('strong','확인하는 이유'),text);work.append(why);
  }else if(ObserverMoa.currentItem(currentActivity().items)?.kind==='tool'&&available){
    work.append(node('p','이 작업의 구체적인 검사 목적은 아직 기록되지 않았어요.','note'));
  }
  if(workExplanation.subjects?.length){
    const detail=node('details');detail.dataset.detail='current-input';detail.append(node('summary','검토 중인 단서와 요청 상태 보기'));
    const list=node('ul',null,'review-subjects');
    for(const s of workExplanation.subjects){
      const item=node('li');item.append(node('strong',s.label));
      if(s.paths.length)item.append(node('div',s.paths.join(' · '),'note'));
      if(s.omitted)item.append(node('div','검토 입력 기록 '+s.presented+'개 중 예시 '+s.examples.length+'개를 보여드려요.','note'));
      list.append(item);
    }
    detail.append(list);work.append(detail);
    const omitted=ObserverMoa.currentItem(currentActivity().items)?.context?.omitted_subject_count;
    if(omitted)work.append(node('p','이 화면에 연결하지 못한 검토 대상 '+omitted+'개가 더 있어요.','note'));
  }
  if(workExplanation.statusNote)work.append(node('p',workExplanation.statusNote,'note'));
  if(state.pinned)work.append(badge('과거 내용을 보고 있어요','warn'));
  if(past)work.append(node('p','설명을 넘겨 봐도 실제 조사는 바뀌지 않아요.','note'));
  renderPreviousWork(priorWork||ObserverMoa.completedItem(workHistory.items,{excludeId:ObserverMoa.currentItem(currentActivity().items)?.id}),!!priorWork,available);
  if(primary){
    const h=primary,isActive=!past&&h.key===activeExplanation?.key;
    $('hypothesis-summary-label').textContent=(h.assessment_current?'살펴보는 가능성과 이유':'마지막 검토한 가능성 · 새 근거 반영 필요')+' — '+(h.title||h.scope||'조사 내용');
    const bundle=ObserverBriefing.composeBundle(h,state.displayed,{ended:ctx.ended,historical:!!record?.archived,restricted:o=>state.isRestricted(o)});
    const bubble=node('article',null,'thought hypothesis '+(isActive?'active':past?'inactive':'viewing'));
    bubble.dataset.key=h.key;
    bubble.append(badge(past?'지난 가설 · 현재 판단이 아니에요':h.validity==='candidate'?'새 가설 · 아직 검토 전':!h.assessment_current?'마지막 가설 · 다시 확인해야 해요':isActive?'이번 검사와 관련된 가설':'지금 살펴보는 가설',!h.assessment_current?'warn':''));
    const label=h.assessment_current&&h.lifecycle==='refuted'?'근거와 맞지 않아 다시 본 가설':h.assessment_current&&h.judgment==='유력'?'지금 더 유력하게 보는 가설':'이 가능성을 살펴보고 있어요';
    bubble.append(node('h3',past||ctx.ended?'이 가능성을 살펴봤어요':label),line('조사 주제',h.title||h.scope));
    if(h.validity==='candidate')bubble.append(node('p','조사를 위해 떠올린 가능성이에요. 아직 맞는지 확인하지 않았어요.','note'));
    if(bundle.fact)bubble.append(line('왜 살펴보나요?',bundle.fact));
    if(bundle.reason)bubble.append(line('검토 중인 설명',bundle.reason));
    if(bundle.linkageGap)bubble.append(node('p',bundle.linkageGap,'note'));
    if(bundle.next)bubble.append(line(bundle.nextStatus,bundle.next));
    if(bundle.nextGap)bubble.append(node('p',bundle.nextGap,'note'));
    if(bundle.limitation)bubble.append(line('아직 모르는 점',bundle.limitation));
    const why=node('details');why.dataset.detail='thought-'+h.key;
    why.append(node('summary','왜 이렇게 생각하나요? · 근거와 판별 기준'));
    if(h.scope)why.append(line('살펴본 질문',h.scope));
    if(h.statement)why.append(node('p',h.statement));
    for(const text of h.counterarguments||[])if(text)why.append(line('다른 가능성',text));
    for(const text of h.gaps||[])if(text)why.append(node('p',text,'note'));
    if(bundle.claim)why.append(refButton(bundle.claim.key,'이 설명의 단서와 원문 보기'));
    if(bundle.test){
      for(const o of bundle.plan.outcomes){const row=node('div',null,'outcome '+o.kind);row.append(node('strong',o.label),node('p',o.text));why.append(row);}
      if(!bundle.plan.outcomes.length)why.append(node('p','판단을 바꿀 구체적인 결과 기준은 아직 없어요.','note'));
      if(bundle.plan.incomplete)why.append(node('p','일부 결과만 확보했어요. 전체 범위를 확인한 것은 아니에요.','note'));
      why.append(refButton(bundle.test.key,'연결된 검사 결과 보기'));
    }
    if(entry){
      why.append(node('p','당시에 보존한 설명이에요. 현재 작업이나 현재 판단으로 대신하지 않아요.','note'));
      for(const ref of saved.source_refs)if(state.displayed.objects[ref.key])why.append(refButton(ref.key,'당시 인용 · 현재 보존 원문 보기'));
    }
    bubble.append(why);
    bubble.append(button(past?'현재 가설과 비교하기':'가설과 근거 살펴보기',()=>openInspection(h.key),'thought-link'));
    rail.append(bubble);
  }
  else if(record?.archived){
    $('hypothesis-summary-label').textContent='지난 조사 설명';
    const bubble=node('article',null,'thought inactive');
    const error=historyErrors.get(record.token);
    bubble.append(badge(error?'지난 설명을 불러오지 못했어요':'지난 설명을 불러오는 중'),
      node('p',error||'원장에 보존된 당시 설명을 확인하고 있어요. 현재 설명으로 대체하지 않아요.'));
    if(error)bubble.append(button('다시 확인',()=>loadExplanation(record,true)));rail.append(bubble);
  }
  else {$('hypothesis-summary-label').textContent='살펴보는 가능성과 이유 · 아직 비교 전';rail.append(node('p','아직 근거와 대조한 설명이 없어요. 지금 하는 자료 확인은 아래에서 볼 수 있어요.','note'));}
  // Compatibility containers stay empty: facts/limits/tests share ONE bundle.
  clear('next-discriminator');n.hidden=true;
}
function renderPreviousWork(item,browsing,available){
  const n=clear('previous-work'),result=ObserverMoa.workResult(item);
  n.dataset.workId=item?.id||'';n.classList.toggle('browsing',browsing);
  n.append(badge(browsing?'지난 조사 작업':'이전 조사 결과'),
    node('h3',item?ObserverMoa.workTarget(item):result.label));
  if(item){
    n.append(node('p',result.label,'work-result'),node('p',result.detail));
    if(result.limitation)n.append(node('p',result.limitation,'note'));
    n.append(node('p',dt(item.completed_at||item.at)+(item.completed_at?' · 완료 기록':' · 상태 확인 기록'),'note'));
    if(item.purpose){const details=node('details');details.dataset.detail='previous-purpose-'+item.id;
      const p=node('p');appendPurpose(p,item,state.current);details.append(node('summary','왜 확인했나요?'),p);n.append(details);}
    const refs=(item.result_refs||[]).filter(r=>state.current.objects[r.key]?.version===r.version&&
      state.current.objects[r.key]?.source_version===r.source_version);
    if(refs.length){const details=node('details');details.dataset.detail='previous-result-'+item.id;
      details.append(node('summary','확보한 단서 보기'));for(const ref of refs.slice(0,3))details.append(button(state.current.objects[ref.key].title,()=>openPurposeSource(ref,state.current),'source-link'));n.append(details);}
    n.append(button('작업 상세 보기',()=>{
      $('activity-filter').value='all';renderActivity();$('work-history').open=true;$('work-dialog').showModal();
      for(const row of $('history').children)if(row.dataset.workId===item.id){row.querySelector('details').open=true;row.scrollIntoView({block:'nearest'});break;}
    },'work-detail'));
  }else n.append(node('p',result.detail,'note'));
  if(!available)n.append(badge('마지막으로 받은 결과 · 현재 상태 미확인','warn'));
}
async function loadExplanation(record,retry=false){
  if(!record?.archived||explanationHistory.loaded(record)||historyRequests.has(record.token))return;
  if(historyErrors.has(record.token)&&!retry)return;
  historyErrors.delete(record.token);historyRequests.add(record.token);
  try{
    const response=await read('/api/explanations/'+encodeURIComponent(record.view.envelope.projection_revision)+'/'+
      encodeURIComponent(record.owner.id)+'/'+record.revision);
    explanationHistory.accept(record,response);
  }catch(error){historyErrors.set(record.token,'당시 설명을 확인하지 못했어요. 최신 내용을 다시 확인하거나 재시도해 주세요.');}
  finally{historyRequests.delete(record.token);if(explanationHistory.selected?.token===record.token)render();}
}
function navigateExplanation(delta){
  const control=document.activeElement?.id;
  delta===null?workHistory.latest():workHistory.move(delta,{
    eligible:a=>a.id!==workHistory.currentId&&!!ObserverMoa.completedItem([a]),
    defaultId:ObserverMoa.completedItem(workHistory.items,{excludeId:workHistory.currentId})?.id});
  render();announce(delta===null?'지금 하는 작업으로 돌아왔어요.':'지난 작업을 보고 있어요. 실제 조사는 바뀌지 않아요.');
  if(control==='explanation-current'||control==='explanation-older'&&$('explanation-older').disabled)
    (delta===null?$('explanation-older'):$('explanation-newer')).focus({preventScroll:true});
}
function activityRow(a) {
  const n=node('div',null,'activity-item');
  n.dataset.workId=a.id;
  n.append(badge(execution[a.state]||'상태 미제공',a.state==='failed'?'error':''),node('strong',a.title),node('div',ObserverMoa.workTarget(a),'target'));
  if(a.failure)n.append(badge(a.failure_impact==='resolved'?'재시도로 회복된 과거 실패':a.failure_impact==='partial'?'일부 영향이 남은 실패':a.failure_impact==='unresolved'?'영향이 남은 실패':'과거 실패 · 현재 영향 미확인'),node('p',a.failure.reason),node('p',a.failure.impact,'note'));
  n.append(node('div',dt(a.at),'when'));
  const details=node('details');details.dataset.detail='activity-'+a.id;details.append(node('summary',a.error?'실패 원인·작업 참조':'작업 참조'),node('div',a.id,'target'));
  if(a.purpose){const p=node('p');appendPurpose(p,a,state.current);details.append(node('strong','확인하는 이유'),p);}
  for(const s of ObserverMoa.reviewSubjects(a)){
    details.append(line('검토 단서',s.title||s.id));
    for(const e of s.examples)details.append(node('p',e.label+(e.path?' · '+e.path:'')));
    if(s.omitted)details.append(node('p','입력의 다른 기록 '+s.omitted+'개는 이 요약에서 생략했어요.','note'));
  }
  if(a.error)details.append(node('p',a.error,'note'));n.append(details);return n;
}
function renderActivity() {
  // Global activity always refers to the newest received snapshot, not selected/pinned evidence.
  const data=currentActivity(), live=session?.mode==='read_only_live';
  const active=state.current.case.execution_end?[]:data.items.filter(a=>['running','waiting','validating','adopting'].includes(a.state));
  const n=clear('activity');
  n.append(node('p','작업 확인: '+dt(data.checked_at)+(live?' · 원장 관측':' · 보존 상태'),'note'));
  if(!active.length)n.append(empty(state.current.case.execution_end?
    (caseStates[state.current.case.status]||'실행 종료')+' · '+executionEnd(state.current.case):
    '이 관측 범위에서 실행 중으로 확인된 작업이 없습니다. 원장 미제공 상태를 실행 없음으로 단정하지 않습니다.'));
  if(!active.length&&data.items.length)n.append(node('p','최근 작업 기록 · 현재 실행 아님','note'));
  for(const a of (active.length?active:data.items).slice(0,5))n.append(activityRow(a));
  if(active.length>5)n.append(node('p','외 '+(active.length-5)+'개 · 작업 이력에서 확인','note'));
  const unknown=data.items.filter(a=>a.state==='input_registered').length;
  if(unknown)n.append(node('p','입력만 등록된 '+unknown+'건은 실제 요청·실행 여부 미확인입니다.','note'));
  const eta=node('details');eta.append(node('summary',state.current.case.execution_end?'실행 종료 · ETA 없음':'ETA 산정 불가'),node('p',data.eta_reason,'note'));n.append(eta);
  const filter=$('activity-filter').value, h=clear('history');
  const rows=data.items.filter(a=>filter==='all'||filter==='active'&&['running','waiting','input_registered','validating','adopting'].includes(a.state)||filter==='failed'&&a.state==='failed'||filter==='finished'&&['received','interrupted','cancelled','succeeded','covered','partial'].includes(a.state));
  for(const a of rows)h.append(activityRow(a));
  if(!rows.length)h.append(empty('해당 작업 없음'));
  $('last-progress').textContent=state.current.summary.last_meaningful_change?
    '마지막 원장 채택 '+dt(state.current.summary.last_meaningful_change)+' · 질문 해결·침해 규명과 별개입니다.':
    '채택된 판단 시점 미제공. 도구 실행이나 반복 검토를 의미 있는 진전으로 대신 집계하지 않습니다.';
  const changes=clear('changes');
  if(!state.changes.length)changes.append(node('p','접속 시 과거 발견을 새 발견으로 재연하지 않습니다.','note'));
  for(const c of state.changes.slice().reverse().slice(0,5)) {
    const d=node('details',null,'change');const summary=node('summary','판단 변경 · '+(c.next?.title||c.old.title));
    d.append(summary,node('p','이전: '+c.old.title),node('p','현재: '+(c.next?.title||'현재 묶음에서 제외')),
      node('p',c.next?.change_reason||'변경 사유 미제공. 단순 변경을 반박·입증으로 임의 분류하지 않습니다.','note'));
    changes.append(d);
  }
  if(state.changes.length>5) {
    const all=node('details');all.append(node('summary','나머지 판단 변경 '+(state.changes.length-5)+'개'));
    for(const c of state.changes.slice(0,-5).reverse())all.append(node('p',c.old.title+' → '+(c.next?.title||'현재 묶음에서 제외'),'change'));
    changes.append(all);
  }
}
function renderQuestions() {
  const qs=values('question'), filter=$('question-filter').value.toLocaleLowerCase();
  if(!selectedQuestion || !state.displayed.objects[selectedQuestion]) selectedQuestion=qs[0]?.key||null;
  $('question-count').textContent=qs.length+'개 질문 · 공백 포함';
  const n=clear('questions');
  qs.filter(q=>q.title.toLocaleLowerCase().includes(filter)).forEach((q,i)=>{
    const b=button(null,()=>{selectedQuestion=q.key;state.selection=null;renderQuestions();renderQuestion();renderSelection();renderTimeline();},'qbutton'+(q.key===selectedQuestion?' active':''));
    b.dataset.key=q.key;b.setAttribute('aria-current',q.key===selectedQuestion?'true':'false');
    b.append(node('span','QUESTION '+String(i+1).padStart(2,'0'),'qnum'),node('strong',q.source_kind==='case_question'?'사건 전체 · 침해 여부와 조사 범위':q.title),badge(questions[q.state]||q.state),
      node('span',q.next_test_state==='not_selected'?'다음 판별 검사 미선정':'연결된 검사 상태 확인','next'));
    n.append(b);
  });
  if(!qs.length)n.append(empty('기록된 질문이 없습니다. 화면이 질문이나 검사를 대신 만들지 않습니다.'));
}
function entityCard(o) {
  const b=button(null,()=>{state.select(o.key);renderQuestion();renderSelection();renderTimeline();},'entity-button'+(state.selection?.key===o.key?' selected':''));
  b.dataset.key=o.key;
  const stateLabel=o.type==='test'?execution[o.execution]||'미제공':validity[o.validity]||'원장 기록';
  b.append(badge(types[o.type]),node('strong',o.title),node('span',stateLabel,'meta'));
  if(o.type==='test')b.append(node('p','판별: '+(o.discrimination==='unassessed'?'미평가':o.discrimination),'meta'));
  else b.append(node('p','원문 관측 '+o.source_keys.length+'개 · 독립성 미평가','meta'));
  const usage=values('question').filter(q=>[...q.claim_keys,...q.hypothesis_keys,...q.test_keys].includes(o.key)).length;
  if(usage>1)b.append(node('span',usage+'개 질문의 공유 참조 · 별도 증거 아님','shared'));
  return b;
}
function renderQuestion() {
  const n=clear('question-detail'), q=state.displayed.objects[selectedQuestion];
  if(!q){n.append(empty('질문이 생성되기 전입니다.'));return;}
  n.append(badge('열람 중인 질문 · 실행 작업과 별개'),node('h2',q.source_kind==='case_question'?'사건 전체 · 침해 여부와 조사 범위':q.title));
  if(q.source_kind==='case_question'){const original=node('details');original.dataset.detail='original-question-'+q.key;original.append(node('summary','원래 조사 요청'),node('p',q.title));n.append(original);}
  const answer=node('div',null,'answer');answer.append(badge(questions[q.state]||q.state),node('p',q.answer),node('p','종결은 답변 범위의 상태이며 사건 전체 정상·침해 확정이 아닙니다.','note'));n.append(answer);
  if(q.contexts?.length>1)for(const c of q.contexts)n.append(node('p',(questions[c.state]||'미제공')+' · '+(c.answer||'이 문맥의 답 미기록'),'note'));
  for(const [title,keys,missing] of [
    ['01 · 확인한 단서',q.claim_keys,'이 질문에 연결된 단서 설명은 아직 없어요.'],
    ['02 · 살펴보는 가설',q.hypothesis_keys,'이 질문에 연결된 가설은 아직 없어요. 정상으로 판단했다는 뜻은 아니에요.'],
    ['03 · 무엇을 확인하나요?',q.test_keys,'다음에 무엇을 확인할지는 아직 정하지 못했어요.']]) {
    const group=node('section',null,'group');group.append(node('h3',title));
    for(const key of keys.slice(0,2))group.append(entityCard(state.displayed.objects[key]));
    if(keys.length>2){const rest=node('details');rest.dataset.detail='group-'+title+'-'+q.key;rest.append(node('summary','나머지 '+(keys.length-2)+'개'));for(const key of keys.slice(2))rest.append(entityCard(state.displayed.objects[key]));group.append(rest);}
    if(!keys.length)group.append(empty(missing));n.append(group);
  }
  const sources=node('details');sources.dataset.detail='sources-'+q.key;sources.append(node('summary','질문에 직접 연결된 원문 '+q.source_keys.length+'개'));
  for(const key of q.source_keys)sources.append(refButton(key));
  if(q.unlinked_source_ids.length)sources.append(node('p','연결 객체 미제공: '+q.unlinked_source_ids.join(', '),'note'));
  n.append(sources);
}
function addDefinition(dl,label,value) {
  dl.append(node('dt',label),node('dd',value===null||value===undefined||value===''?'미제공':typeof value==='object'?JSON.stringify(value,null,2):String(value)));
}
function renderSelection() {
  const n=clear('selection'), o=state.displayed.objects[state.selection?.key];
  if(!o)return;
  n.append(badge(types[o.type]),node('h3',o.title));
  const technical=node('details');technical.dataset.detail='technical-'+o.key;technical.append(node('summary','원장 참조·버전'),node('p',o.id+' · v '+o.version.slice(0,12),'source-locator'));n.append(technical);
  const annotation=(state.displayed.jev_annotations||[]).find(a=>a.subject_id===(o.owner_id||o.id));
  if(annotation){const jev=node('details');jev.dataset.detail='jev-'+annotation.id;
    jev.append(node('summary','Jev 단서 분류 · '+annotation.label),node('p',annotation.basis,'note'));n.append(jev);}
  if(state.isRestricted(o))n.append(empty('정정 영향: 이 항목은 현재 설명에 사용할 수 없습니다. 재동기화가 필요합니다.'));
  if(o.type==='claim'||o.type==='hypothesis') {
    if(o.type==='claim')n.append(node('p',ObserverBriefing.factText(o)));
    else if(o.scope)n.append(node('p',o.scope));
    if(o.interpretation){const interpretation=node('details');interpretation.dataset.detail='interpretation-'+o.key;interpretation.append(node('summary','AI 해석 · 사실과 구분'),node('p',o.interpretation));n.append(interpretation);}
    const dl=node('dl');addDefinition(dl,'검토 상태',validity[o.validity]);addDefinition(dl,o.type==='hypothesis'&&!o.assessment_current?'당시 판단':'기록된 판단',o.judgment);addDefinition(dl,'적용 범위',o.scope);addDefinition(dl,'지금도 유효한가요?',o.review_recency);n.append(dl);
    for(const [title,items] of [['왜 이렇게 생각하나요?',[o.ranking_reason||o.historical_reason||o.reason].filter(Boolean)],['다른 가능성은 없나요?',o.counterarguments],['무엇을 더 확인해야 하나요?',o.gaps]]) {
      n.append(node('h3',title));if(!items?.filter(Boolean).length)n.append(node('p','아직 기록된 설명이 없어요. 확인을 마쳤다는 뜻은 아니에요.','note'));
      else {const ul=node('ul');for(const text of items.filter(Boolean))ul.append(node('li',text));n.append(ul);}
    }
  }
  if(o.type==='incident'){
    n.append(node('p',o.statement),node('p',o.reason),node('p',o.limitation,'note'));
  }
  if(o.type==='test') {
    const dl=node('dl');addDefinition(dl,'검사 실행',execution[o.execution]);addDefinition(dl,'판별 결과',o.discrimination==='unassessed'?'미평가':o.discrimination);addDefinition(dl,'영향·차단',o.execution_reason);addDefinition(dl,'실제 작업',o.job_ids);addDefinition(dl,'검사 대상',o.request);addDefinition(dl,'지지 조건',o.conditions.success_condition);addDefinition(dl,'반박 조건',o.conditions.refutation_condition);addDefinition(dl,'판별 불가',o.conditions.inconclusive_condition);addDefinition(dl,'즉시 관측값',o.design.immediate_observable);n.append(dl);
    n.append(node('p','도구 실행 성공은 가설 입증이 아닙니다. 결과가 없거나 부분 검색인 경우 반박으로 승격하지 않습니다.','note'));
  }
  if(o.type==='observation') {
    n.append(node('p',o.limitation,'note'));
    const cached=sourceCache.get(o.source_version);
    const excerpt=node('pre',cached?.text??o.excerpt??'텍스트 발췌 미제공 · 원문 바이너리를 실행하지 않습니다.','excerpt');
    excerpt.dataset.scroll='excerpt-'+o.key;n.append(excerpt);
    if(o.excerpt_partial)n.append(node('p','부분 발췌입니다. 보존 필드 '+(o.excerpt_characters??'미상')+'자 중 표시 범위만 제공합니다.','note'));
    if(o.excerpt_partial&&state.displayed.envelope.data_mode!=='example'&&cached?.next_offset!==null){
      const more=button('보존 원문 더 읽기',async()=>{
        more.disabled=true;
        try{const p=await read('/api/sources/'+encodeURIComponent(state.displayed.envelope.projection_revision)+'/'+encodeURIComponent(o.id)+'?offset='+(cached?.next_offset??0)+'&limit=65536');
          if(p.source_version!==o.source_version)throw Error('원문 버전 변경');
          const text=(cached?.text||'')+p.text;
          if(text.length>2000000)throw Error('화면 읽기 한도 도달 · 별도 원문 열람 필요');
          sourceCache.set(o.source_version,{text,next_offset:p.next_offset});
          while(sourceCache.size>3)sourceCache.delete(sourceCache.keys().next().value);
          renderSelection();
        }catch(e){more.disabled=false;n.append(node('p','원문 추가 읽기 제한: '+e.message,'note'));}
      });n.append(more);
    }
    const dl=node('dl');addDefinition(dl,'원문 위치',o.source_location);for(const [key,value] of Object.entries(o.locator))addDefinition(dl,key,value);addDefinition(dl,'독립성',o.independence);addDefinition(dl,'기록 수',o.record_count);technical.append(dl);
    n.append(node('p','출처 해시는 바이트 식별자이며 진실성·독립 발생을 증명하지 않습니다. 증거 URL·명령은 연결하거나 실행하지 않습니다.','note'));
  }
  if(o.source_keys?.length){n.append(node('h3','연결 원문'));for(const key of o.source_keys)n.append(refButton(key));}
  const relations=state.displayed.relations.filter(r=>r.from===o.key||r.to===o.key);
  if(relations.some(r=>r.kind==='reuses_physical_result')) {
    n.append(node('h3','물리 결과 재사용'));
    for(const r of relations.filter(r=>r.kind==='reuses_physical_result'))n.append(node('p',r.job_id+' · 새 실행 '+String(r.new_execution)+' · 독립 증거 '+String(r.independent_evidence),'note'));
  }
}
function questionSources() {
  const v=state.displayed,q=v.objects[selectedQuestion], seen=new Set();
  if(!q)return seen;
  function walk(key){if(seen.has(key))return;seen.add(key);const o=v.objects[key];for(const r of o?.refs||[])walk(r.key);}
  walk(q.key);return seen;
}
function timeCard(t) {
  const o=state.displayed.objects[t.source_ref.key];
  const bundle=ObserverBriefing.timelineBundle(t,state.displayed);
  const interpretations=[];
  for(const m of t.members||[t]){
    const entry=ObserverBriefing.timelineBundle(m,state.displayed);
    if(!interpretations.some(x=>x.target===entry.target&&x.reason===entry.reason))interpretations.push(entry);
  }
  const multiple=interpretations.length>1;
  const cls='time-card '+t.lane+(t.estimated?' estimated':'');
  const b=multiple?node('article',null,cls):button(null,()=>openInspection(bundle.target),cls);
  b.dataset.key=t.id;
  b.append(badge(t.group_kind==='file_clocks'?'파일 시각 · '+new Set(t.members.map(m=>m.meaning)).size+'종':t.meaning,t.comparable?'':'warn'));
  if(t.lane==='file'&&!multiple&&o?.title){const path=node('span',o.title,'time-source');path.title=o.title;b.append(path);}
  if(t.source_refs?.length>1)b.append(node('span','같은 파일의 원문 '+t.source_refs.length+'곳 · 파일 시각은 한 번만 표시','note'));
  const clocks=t.group_kind==='file_clocks'?ObserverViewUtils.fileClockCells(t):[t];
  const table=t.group_kind==='file_clocks'?node('table',null,'file-clock-grid'):null;
  const tbody=table?node('tbody'):null;if(table)table.setAttribute('aria-label','파일 시간 · 원문 정밀도는 상세 참조');
  let row;
  for(const [i,clock] of clocks.entries()){
    let container=b;
    if(table){if(i%2===0){row=node('tr');tbody.append(row);}container=node('td');row.append(container);container.append(node('span',clock.label,'time-clock-kind'));}
    if(clock.meanings){const kinds=node('span',clock.meanings.join(' / '),'time-clock-kind');
      kinds.title=clock.assertions.map(a=>a.meaning+' — '+a.basis).join('\n');if(!table)container.append(kinds);}
    const qualifier=clock.shape==='unknown'?'시각 미제공':clock.shape==='candidates'?'복수 시각 후보':clock.shape==='interval'?'연속 구간':clock.estimated?'추정 시각':null;
    if(qualifier)container.append(node('span',qualifier,'time-basis'));
    const displayed=new Map();
    for(const raw of clock.raw_values){const label=ObserverViewUtils.clockText(raw),originals=displayed.get(label)||[];originals.push(raw);displayed.set(label,originals);}
    for(const [label,originals] of displayed){const value=node('span',label,'clock');value.title=originals.map(raw=>raw??'미상').join('\n');container.append(value);}
    if(clock.source_variants?.length>1)container.append(node('span',displayed.size===1?'원문 정밀도 차이':'출처별 시각 차이','time-basis'));
    if(!clock.raw_values.length)container.append(node('span','배치할 시각 없음','clock'));
  }
  if(table){table.append(tbody);b.append(table);}
  function appendInterpretation(container,entry){
    container.append(node('strong',entry.text,'time-title'));
    if(entry.reason)container.append(node('span','포식이가 살펴보는 이유: '+entry.reason,'time-reason'));
    else container.append(node('span','이 단서가 사건과 어떻게 연결되는지는 아직 확인이 필요해요.','time-explanation'));
  }
  if(multiple){
    b.append(node('strong',o?.title||'같은 파일에서 확인한 단서','time-title'));
    const details=node('details');details.dataset.detail='time-'+t.id;
    details.append(node('summary','연결된 설명 '+interpretations.length+'개 보기'));
    for(const entry of interpretations){const link=button(null,()=>openInspection(entry.target),'time-interpretation');appendInterpretation(link,entry);details.append(link);}
    b.append(details);
  }else appendInterpretation(b,bundle);
  if(questionSources().has(t.source_ref.key))b.append(badge('선택 질문과 연결'));
  return b;
}
function renderTimeline() {
  const sources=questionSources(),filter=$('timeline-filter').value;
  const list=state.displayed.timeline.filter(t=>filter==='all'||filter==='question'&&sources.has(t.source_ref.key)||filter==='core'&&t.core);
  const axis=clear('timeline'), other=clear('uncertain');
  const comparable=ObserverViewUtils.timeGroups(list.filter(t=>t.comparable),state.displayed),uncertain=ObserverViewUtils.timeGroups(list.filter(t=>!t.comparable),state.displayed);
  for(const t of comparable.slice(0,timeLimit))axis.append(timeCard(t));
  for(const t of uncertain.slice(0,uncertainLimit))other.append(timeCard(t));
  const more=clear('timeline-more');
  more.append(node('span','시간축 카드 '+Math.min(timeLimit,comparable.length)+' / '+comparable.length+'개 · 같은 단서의 파일 시각은 묶어 보여드려요','note'));
  if(comparable.length>timeLimit)more.append(button('시간순으로 12개 더 보기',()=>{timeLimit+=12;renderTimeline();}));
  if(uncertain.length>uncertainLimit)other.append(button('미상·비교 불가 12개 더 보기',()=>{uncertainLimit+=12;renderTimeline();},'time-more'));
  if(!list.some(t=>t.comparable))axis.append(empty(filter==='core'?'아직 시각을 배치할 수 있는 핵심 사실이 없습니다. 전체 연결 원문은 필터에서 볼 수 있습니다.':'이 범위에 공통 축으로 배치할 수 있는 시각이 없습니다.'));
  const count=uncertain.length;
  $('uncertain-count').textContent='시각 미상 · 절대 시각 비교 불가 '+count+'개 항목';
  if(!count)other.append(node('p','선택 질문의 표시 범위에서 없음 · 전체 자료의 시각 검토 완료를 뜻하지 않음','note'));
}
function renderReports() {
  const n=clear('reports'), reports=state.displayed.reports;
  n.append(node('p','보존된 역사적 산출물입니다. 보고서를 열기 위해 새 모델 호출이나 보고서 생성을 요청하지 않습니다.','note'));
  if(!reports.length){n.append(empty('미생성 — 이 스냅샷에 저장된 실제 보고서가 없습니다.'));return;}
  for(const r of reports) {
    const div=node('section',null,'report-row');
    const current=state.current.reports.find(x=>x.id===r.id)||r;
    div.append(node('strong',r.report_id),node('p',dt(r.generated_at),'note'),badge(current.correction_impact==='confirmed'?'정정 영향 있음':current.freshness==='same_ledger_scope'?'같은 원장 범위':current.freshness==='older_scope'?'이전 범위 · 정정 영향 가능':'최신성 미확인','warn'),node('p',r.claim_version_limitation,'note'));
    for(const name of Object.keys(r.formats)) {
      if(!['executive.html','analyst.html','executive.docx','analyst.docx'].includes(name))continue;
      const label=(name.startsWith('executive')?'임원용 요약':'분석가용 상세')+' · '+name.split('.')[1].toUpperCase();
      if(session.report_downloads_available&&state.displayed.envelope.data_mode!=='example'){
        const a=node('a',label);a.href='/api/reports/'+encodeURIComponent(r.id)+'/'+encodeURIComponent(name);a.download=name;div.append(a);
      } else div.append(node('p',label+' · 보존 파일 연결 미제공','note'));
    }
    n.append(div);
  }
}
function render() {
  // Poll/reconnect must not steal the current source position or keyboard focus.
  const active=document.activeElement, key=active?.dataset?.key, id=active?.id;
  const scope=active?.closest('[id]')?.id;
  const ordinal=key?[...($(scope)||document).querySelectorAll('[data-key]')].filter(e=>e.dataset.key===key).indexOf(active):-1;
  const positions=[...document.querySelectorAll('[data-scroll]')].map(e=>[e.dataset.scroll,e.scrollTop,e.scrollLeft]);
  const opened=new Set([...document.querySelectorAll('details[open][data-detail]')].map(e=>e.dataset.detail));
  renderSummary();renderNarrative();renderActivity();renderQuestions();renderQuestion();renderSelection();renderTimeline();renderReports();alerts();refreshPurposeSource();
  for(const e of document.querySelectorAll('details[data-detail]'))if(opened.has(e.dataset.detail))e.open=true;
  for(const [name,top,left] of positions){const el=[...document.querySelectorAll('[data-scroll]')].find(e=>e.dataset.scroll===name);if(el){el.scrollTop=top;el.scrollLeft=left;}}
  const target=key?[...($(scope)||document).querySelectorAll('[data-key]')].filter(e=>e.dataset.key===key)[Math.max(0,ordinal)]:id?$(id):null;
  if(target)target.focus({preventScroll:true});
  else if(active&&active!==document.body&&!active.isConnected){
    const visible=document.querySelector('dialog[open] button')||
      [...document.querySelectorAll('.explanation-buttons button')].find(b=>!b.hidden&&!b.disabled)||$('guide-title');
    if(visible=== $('guide-title'))visible.tabIndex=-1;
    visible?.focus({preventScroll:true});
  }
}
async function read(path){const r=await fetch(path,{cache:'no-store',credentials:'same-origin',redirect:'error',signal:AbortSignal.timeout(15000)});if(!r.ok)throw Error('화면 데이터 응답 '+r.status);return r.json();}
async function synchronize() {
  if(requestPending)return;requestPending=true;$('refresh').disabled=true;
  const before=state.changes.length;
  try {
    session=await read('/api/session');
    if(!session.latest)throw Error('읽기 전용 원장 관측 준비 중');
    state.beginUpdate(session.latest);
    if(state.current&&state.sync!=='ready'){renderSummary();renderNarrative();alerts();}
    if(!state.current||state.current.envelope.projection_revision!==session.latest.projection_revision||state.sync!=='ready'){
      const view=await read('/api/snapshots/'+encodeURIComponent(session.latest.projection_revision));
      state.accept(view,{resync:true});
    }
    if(session.activity)state.acceptActivity(session.activity);
    offline=false;render();
    if(state.changes.length>before)announce('주장·질문 또는 근거 참조 '+(state.changes.length-before)+'건 변경. 변경 이력을 확인하세요.');
  } catch(error) {
    offline=true;
    if(!state.current){$('critical').hidden=false;$('critical').textContent='보존 화면을 불러오지 못했습니다. '+error.message;}
    else {renderSummary();renderNarrative();alerts();}
    $('connection').textContent='연결 끊김 · 현재 상태 미확인';
  } finally {requestPending=false;$('refresh').disabled=false;schedulePoll();}
}
function schedulePoll(){
  clearTimeout(pollTimer);
  if(session?.mode==='read_only_live'&&!document.hidden)pollTimer=setTimeout(synchronize,Math.max(10,session.poll_seconds||30)*1000);
}
function updateLiveWork(){
  const line=$('guide-work-live');if(!line||!state.current)return;
  const available=!offline&&state.sync==='ready'&&!(session?.mode==='read_only_live'&&session.source_status!=='observed');
  const items=currentActivity().items,item=ObserverMoa.currentItem(items),now=Date.now();
  if(item?.id!==observedWork.id)observedWork={id:item?.id||null,since:now};
  const work=ObserverMoa.elapsedActivity(state.current,items,{available,now,observedSince:observedWork.since});
  line.textContent=work.text;line.title=work.timer_basis||'';
  updateFeedback(available,now);
}
// A local display clock only: no fetch, snapshot mutation or model request.
setInterval(()=>{if(!document.hidden)updateLiveWork();},1000);
$('refresh').onclick=synchronize;
$('purpose-source-close').onclick=()=>{$('purpose-source-dialog').close();purposeSource=null;};
$('purpose-source-dialog').addEventListener('close',()=>{purposeSource=null;});
$('explanation-older').onclick=()=>navigateExplanation(1);
$('explanation-newer').onclick=()=>navigateExplanation(-1);
$('explanation-current').onclick=()=>navigateExplanation(null);
$('pin').onclick=()=>{state.pin(!state.pinned);render();};
$('guide-size').onclick=()=>{focusedMode=focusedMode?0:1;$('workspace').classList.toggle('expanded',!!focusedMode);$('guide-size').textContent=focusedMode?'기본 배치':'설명 넓게';};
$('hide-moa').onclick=()=>{renderer.preferences({hidden:!renderer.hidden});$('hide-moa').setAttribute('aria-pressed',String(renderer.hidden));$('hide-moa').textContent=renderer.hidden?'표시':'숨김';};
$('motion').onchange=()=>{renderer.preferences({motion:$('motion').checked});previewPreferences();};
reducedMotion.addEventListener('change',e=>{renderer.preferences({reduced:e.matches});previewPreferences();});
document.addEventListener('visibilitychange',()=>{renderer.preferences({visible:!document.hidden});previewPreferences();schedulePoll();if(!document.hidden&&session?.mode==='read_only_live')synchronize();});
$('moa-preview-open').onclick=()=>{
  if(!$('moa-preview-avatar').children.length)$('moa-preview-avatar').append(document.querySelector('.moa svg').cloneNode(true));
  $('moa-preview-dialog').showModal();previewPreferences();previewPose('searching');
};
$('moa-preview-close').onclick=()=>{$('moa-preview-dialog').close();};
$('moa-preview-dialog').addEventListener('close',previewPreferences);
for(const [key,pose] of Object.entries(ObserverMoa.previews)){
  const b=button(pose.label,()=>previewPose(key));b.dataset.pose=key;b.setAttribute('aria-pressed','false');$('moa-preview-choices').append(b);
}
$('activity-filter').onchange=renderActivity;
$('question-filter').oninput=renderQuestions;
$('timeline-layout').onclick=()=>{const list=$('timeline').classList.toggle('list');$('timeline-layout').setAttribute('aria-pressed',String(list));$('timeline-layout').textContent=list?'가로축으로 보기':'목록으로 보기';};
$('timeline-filter').onchange=()=>{timeLimit=12;uncertainLimit=12;renderTimeline();};
$('reports-open').onclick=()=>{$('reports-dialog').showModal();};
$('reports-close').onclick=()=>{$('reports-dialog').close();};
$('inspection-open').onclick=()=>openInspection();
$('inspection-close').onclick=()=>{$('inspection-dialog').close();};
$('work-open').onclick=()=>{$('work-dialog').showModal();};
$('work-close').onclick=()=>{$('work-dialog').close();};
// Native buttons/summary provide keyboard selection/expand without pretending
// this is an ARIA tree. Focus alone never selects or executes an investigation.
synchronize();
