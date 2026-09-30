'use strict';
const state=new ObserverState.ViewState();
const $=id=>document.getElementById(id);
let session=null, selectedQuestion=null, requestPending=false, offline=false, focusedMode=0, pollTimer=null;
let timeLimit=12, uncertainLimit=12;
let observedWork={id:null,since:null};
const explanationHistory=new ObserverBriefing.ExplanationHistory();
const historyRequests=new Set(), historyErrors=new Map();
const sourceCache=new Map();
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
const execution={candidate:'확인 후보',queued:'실행 예정',running:'확인 중',blocked:'자료·도구 부족',
  succeeded:'검사 완료',covered:'요청 범위 처리 완료',partial:'일부 결과 확보',failed:'검사 실패',waiting:'응답 대기',
  received:'응답 도착 · 검토 필요',input_registered:'요청 준비 · 전송 여부 미확인',interrupted:'중단',cancelled:'취소',unknown:'상태 미확인'};
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
  if(!value)return '미제공';const date=new Date(value);return Number.isNaN(date.valueOf())?String(value):date.toLocaleString('ko-KR',{hour12:false})+' · 표시 장치 시각';
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
  const failures=state.current?currentActivity().items.filter(a=>a.state==='failed'):[];
  for(const a of failures.slice(0,2)){
    const f=ObserverMoa.failure(a);
    warnings.push('지난 실패 기록: '+f.label+' — '+f.reason+' '+f.impact);
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
  const work=ObserverMoa.activity(state.current,currentActivity().items,{available});
  renderer.update({base:work.base,
    transient:'none',severity:warnings.length?'warning':'info'});
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
    line('남은 확인',s.unassessed_tests?'아직 판단하지 못한 검사가 있어요 · '+s.unassessed_tests+'개':
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
  $('workspace').classList.toggle('reading-past',past);
  $('explanation-position').textContent=record?(past?'이전 설명':'현재 설명')+' · '+(explanationHistory.index+1)+' / '+explanationHistory.items.length:'현재 설명';
  $('explanation-at').textContent=record?.at?dt(record.at):'';
  $('explanation-older').disabled=!available||!record||explanationHistory.index>=explanationHistory.items.length-1;
  $('explanation-newer').disabled=!available||!past;
  $('explanation-current').hidden=!past;
  const historyNote=$('explanation-history-note');historyNote.hidden=!past;
  historyNote.textContent=record?.removed?'이 설명은 현재 판단에서 바뀌거나 제외됐어요. 당시 기록을 보고 있으며, 현재 판단과는 달라요.':
    '당시에 정리한 설명을 보고 있어요. 현재 조사 상태·사건 시간축·보고서는 그대로 유지돼요.';
  work.append(badge(real?'지금 조사 중':!available?'연결 확인 중':ctx.ended?'조사가 멈춰 있어요':'현재 상태'));
  const liveText=node('p',workExplanation.label);liveText.id='guide-work-live';work.append(liveText);updateLiveWork();
  if(workExplanation.subjects?.length){
    const list=node('ul',null,'review-subjects');
    for(const s of workExplanation.subjects){
      const item=node('li');item.append(node('strong',s.label));
      if(s.paths.length)item.append(node('div',s.paths.join(' · '),'note'));
      if(s.omitted)item.append(node('div','검토 입력 기록 '+s.presented+'개 중 예시 '+s.examples.length+'개를 보여드려요.','note'));
      list.append(item);
    }
    work.append(list);
    const omitted=ObserverMoa.currentItem(currentActivity().items)?.context?.omitted_subject_count;
    if(omitted)work.append(node('p','이 화면에 연결하지 못한 검토 대상 '+omitted+'개가 더 있어요.','note'));
  }
  if(workExplanation.statusNote)work.append(node('p',workExplanation.statusNote,'note'));
  if(state.pinned)work.append(badge('과거 내용을 보고 있어요','warn'));
  if(past)work.append(node('p','설명을 넘겨 봐도 실제 조사는 바뀌지 않아요.','note'));
  if(primary){
    const h=primary,isActive=!past&&h.key===activeExplanation?.key;
    const bubble=node('article',null,'thought hypothesis '+(isActive?'active':past?'inactive':'viewing'));
    bubble.dataset.key=h.key;
    bubble.append(badge(past?'지난 가설 · 현재 판단이 아니에요':h.validity==='candidate'?'새 가설 · 아직 검토 전':!h.assessment_current?'마지막 가설 · 다시 확인해야 해요':isActive?'이번 검사와 관련된 가설':'지금 살펴보는 가설',!h.assessment_current?'warn':''));
    const label=h.assessment_current&&h.lifecycle==='refuted'?'근거와 맞지 않아 다시 본 가설':h.assessment_current&&h.judgment==='유력'?'지금 더 유력하게 보는 가설':'이 가능성을 살펴보고 있어요';
    bubble.append(node('h3',past||ctx.ended?'이 가능성을 살펴봤어요':label),node('strong',h.title||h.scope));
    if(h.validity==='candidate')bubble.append(node('p','조사를 위해 떠올린 가능성이에요. 아직 맞는지 확인하지 않았어요.','note'));
    else if(!h.assessment_current&&!past)bubble.append(node('p','새 근거가 반영됐는지 다시 확인해야 해요. 지난 판단을 그대로 확정할 수는 없어요.','note'));
    const why=node('details');why.dataset.detail='thought-'+h.key;
    why.append(node('summary','가설 설명과 남은 의문'));
    if(h.scope)why.append(line('살펴본 질문',h.scope));
    if(h.statement)why.append(node('p',h.statement));
    for(const text of h.counterarguments||[])if(text)why.append(line('다른 가능성',text));
    for(const text of h.gaps||[])if(text)why.append(node('p',text,'note'));bubble.append(why);
    bubble.append(button(past?'현재 가설과 비교하기':'가설과 근거 살펴보기',()=>openInspection(h.key),'thought-link'));
    rail.append(bubble);
  }
  else if(record?.archived){
    const bubble=node('article',null,'thought inactive');
    const error=historyErrors.get(record.token);
    bubble.append(badge(error?'지난 설명을 불러오지 못했어요':'지난 설명을 불러오는 중'),
      node('p',error||'원장에 보존된 당시 설명을 확인하고 있어요. 현재 설명으로 대체하지 않아요.'));
    if(error)bubble.append(button('다시 확인',()=>loadExplanation(record,true)));rail.append(bubble);
  }
  else {const bubble=node('article',null,'thought '+(real&&available?'active':'inactive'));
    bubble.append(badge('지금 하는 일'),node('h3',workExplanation.label),
      node('p',workExplanation.why||'현재 근거로 비교한 가설은 아직 없어요. 다음 검사와 근거의 연결을 확인해야 해요.'));
    if(workExplanation.meaning)bubble.append(node('p',workExplanation.meaning,'note'));rail.append(bubble);}
  if(primary){
    const why=node('article',null,'thought '+(primary.assessment_current?'reason':'inactive'));
    why.append(badge(primary.assessment_current?'왜 이렇게 생각하나요?':'당시에는 왜 이렇게 생각했나요?'),
      node('p',primary.ranking_reason||primary.historical_reason||primary.reason||'가설의 이유가 아직 기록되지 않았어요.'));
    if(!record.archived)why.append(refButton(primary.key,'근거와 다른 가능성 보기'));rail.append(why);
  }
  const relatedClaim=available&&!record?.archived&&ObserverBriefing.claimFor(primary,state.displayed);
  const list=relatedClaim&&!state.isRestricted(relatedClaim)?[{kind:'adopted_claim',text:ObserverBriefing.factText(relatedClaim),
    limitations:relatedClaim.gaps,refs:[{key:relatedClaim.key,version:relatedClaim.version}],dedupe_key:relatedClaim.key}]:primary?[]:record?[]:currentNarratives().filter(item=>item.kind!=='status').slice(0,1);
  if(entry){
    const bubble=node('article',null,'thought inactive');bubble.append(badge('당시 정리한 내용 · 확정 사실과는 달라요'),
      node('p',entry.statement||'당시 단서 요약은 기록되지 않았어요.'));
    for(const ref of saved.source_refs){
      if(state.displayed.objects[ref.key])bubble.append(refButton(ref.key,'연결된 원문 보기 · 현재 보존 버전'));
    }
    if(entry.observation_ids.length>saved.source_refs.length)bubble.append(node('p','당시 인용 중 일부는 이 화면에 연결되지 않았어요.','note'));
    bubble.append(node('p','원문 링크는 현재 보존된 버전이에요. 당시 원문의 버전·최신성까지 확인됐다는 뜻은 아니에요.','note'));n.append(bubble);
  }
  else if(!list.length&&workExplanation.meaning&&!primary){
    const bubble=node('article',null,'thought fact');bubble.append(badge('이 작업으로 무엇을 알 수 있나요?'),node('p',workExplanation.meaning));n.append(bubble);
  }
  else if(!list.length)n.append(empty(record?.archived?'당시 설명을 불러오면 함께 보여드릴게요.':primary?'이 가설에 연결된 단서 설명은 아직 확인하지 못했어요.':'단서를 찾고 있어요. 검토된 내용이 생기면 근거와 함께 설명할게요.'));
  for(const item of list) {
    const c=item.refs.map(r=>state.displayed.objects[r.key]).find(o=>o?.type==='claim');
    const bubble=node('article',null,'thought '+(past?'inactive':'fact'));bubble.append(badge(past?'이 설명에 연결된 단서 · 현재 보존 버전':item.kind==='adopted_claim'?'어떤 단서를 찾았나요?':'지금 알 수 있는 것'),node('p',c?ObserverBriefing.factText(c):item.text));
    if(item.limitations.length) {
      const limits=node('details');limits.append(node('summary','아직 확인할 내용 '+item.limitations.length+'개'));
      limits.dataset.detail='narrative-'+item.dedupe_key;
      for(const text of item.limitations)limits.append(node('p',text,'note'));bubble.append(limits);
    }
    for(const ref of item.refs)bubble.append(refButton(ref.key,'단서 자세히 보기'));n.append(bubble);
  }
  const next=clear('next-discriminator');
  const t=record?.archived||primary?.key!==ctx.primary?.key?null:ctx.test;
  if(available&&t){
    const plan=ObserverBriefing.testExplanation(t,{ended:ctx.ended});
    next.append(badge(past?'이 설명에 연결된 검사 · 현재 작업과 별개':plan.status,t.execution==='blocked'?'warn':''),node('h3',past||ctx.ended?'무엇을 확인하려 했나요?':'무엇을 확인하나요?'),node('p',plan.purpose));
    const results=node('details');results.dataset.detail='outcomes-'+t.key;results.open=true;
    results.append(node('summary','결과에 따라 이렇게 판단할 수 있어요'));
    for(const o of plan.outcomes){const row=node('div',null,'outcome '+o.kind);row.append(node('strong',o.label),node('p',o.text));results.append(row);}
    if(!plan.outcomes.length)results.append(node('p','가설 판단을 바꿀 구체적인 결과 기준은 아직 없어요.','note'));
    results.append(node('p',plan.incomplete?'일부 결과만 있어요. 전체 범위를 확인한 것처럼 판단하지 않을게요.':'이것은 확인 기준이에요. 결과를 해석해야 실제 판단이 달라져요.','note'));
    next.append(results,refButton(t.key,'검사 결과와 근거 보기'));
  }
  else if(available&&(primary?.next_discriminator||primary?.historical_next_discriminator))next.append(badge(past?'당시 제안한 확인 방법 · 실행 여부는 별도':'확인 후보 · 아직 시작하지 않았어요'),node('p',primary.next_discriminator||primary.historical_next_discriminator));
  else next.append(empty(available?'다음에 무엇을 확인할지는 아직 정하지 못했어요.':'연결이 확인되면 조사 내용을 보여드릴게요.'));
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
  const record=delta===null?explanationHistory.latest():explanationHistory.move(delta);
  render();loadExplanation(record);announce(delta===null?'현재 설명으로 돌아왔어요.':'다른 설명을 보고 있어요. 실제 조사는 바뀌지 않아요.');
  if(control==='explanation-current'||control==='explanation-older'&&$('explanation-older').disabled)
    (delta===null?$('explanation-older'):$('explanation-newer')).focus({preventScroll:true});
}
function activityRow(a) {
  const n=node('div',null,'activity-item');
  n.append(badge(execution[a.state]||'상태 미제공',a.state==='failed'?'error':''),node('strong',a.title),node('div',ObserverMoa.workTarget(a),'target'));
  if(a.failure)n.append(node('p',a.failure.reason),node('p',a.failure.impact,'note'));
  n.append(node('div',dt(a.at),'when'));
  const details=node('details');details.append(node('summary',a.error?'실패 원인·작업 참조':'작업 참조'),node('div',a.id,'target'));
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
  const active=state.current.case.execution_end?[]:data.items.filter(a=>['running','waiting'].includes(a.state));
  const n=clear('activity');
  n.append(node('p','작업 확인: '+dt(data.checked_at)+(live?' · 원장 관측':' · 보존 상태'),'note'));
  if(!active.length)n.append(empty(state.current.case.execution_end?
    (caseStates[state.current.case.status]||'실행 종료')+' · '+executionEnd(state.current.case):
    '이 관측 범위에서 실행 중으로 확인된 작업이 없습니다. 원장 미제공 상태를 실행 없음으로 단정하지 않습니다.'));
  if(!active.length&&data.items.length)n.append(node('p','최근 작업 기록 · 현재 실행 아님','note'));
  for(const a of (active.length?active:data.items).slice(0,5))n.append(activityRow(a));
  if(active.length>5)n.append(node('p','외 '+(active.length-5)+'개 · 전체 이력에서 확인','note'));
  const unknown=data.items.filter(a=>a.state==='input_registered').length;
  if(unknown)n.append(node('p','입력만 등록된 '+unknown+'건은 실제 요청·실행 여부 미확인입니다.','note'));
  const eta=node('details');eta.append(node('summary',state.current.case.execution_end?'실행 종료 · ETA 없음':'ETA 산정 불가'),node('p',data.eta_reason,'note'));n.append(eta);
  const filter=$('activity-filter').value, h=clear('history');
  const rows=data.items.filter(a=>filter==='all'||filter==='active'&&['running','waiting','input_registered'].includes(a.state)||filter==='failed'&&a.state==='failed'||filter==='finished'&&['received','interrupted','cancelled','succeeded','covered','partial'].includes(a.state));
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
  const b=button(null,()=>openInspection(t.claim_refs.find(r=>state.displayed.objects[r.key]?.display_binding?.status==='bound')?.key||o.key),'time-card '+t.lane+(t.estimated?' estimated':''));
  b.dataset.key=t.id;
  b.append(badge(t.meaning,t.comparable?'':'warn'));
  b.append(node('span',t.shape==='unknown'?'시각 미제공':t.shape==='candidates'?'복수 시각 후보':t.shape==='interval'?'연속 구간':t.estimated?'추정 시각':'기록된 값','time-basis'));
  for(const raw of t.raw_values)b.append(node('span',raw??'미상','clock'));
  if(!t.raw_values.length)b.append(node('span','배치할 시각 없음','clock'));
  const claim=t.claim_refs.map(r=>state.displayed.objects[r.key]).find(c=>c?.display_binding?.status==='bound'&&c.validity==='adopted');
  b.append(node('strong',claim?ObserverBriefing.factText(claim):t.title||o.title,'time-title'));
  if(t.relevance_reason)b.append(node('span','포식이가 살펴보는 이유: '+t.relevance_reason,'time-reason'));
  else b.append(node('span','이 단서가 사건과 어떻게 연결되는지는 아직 확인이 필요해요.','time-explanation'));
  if(questionSources().has(t.source_ref.key))b.append(badge('선택 질문과 연결'));
  return b;
}
function renderTimeline() {
  const sources=questionSources(),filter=$('timeline-filter').value;
  const list=state.displayed.timeline.filter(t=>filter==='all'||filter==='question'&&sources.has(t.source_ref.key)||filter==='core'&&t.core);
  const axis=clear('timeline'), other=clear('uncertain');
  const comparable=list.filter(t=>t.comparable),uncertain=list.filter(t=>!t.comparable);
  for(const t of comparable.slice(0,timeLimit))axis.append(timeCard(t));
  for(const t of uncertain.slice(0,uncertainLimit))other.append(timeCard(t));
  const more=clear('timeline-more');
  more.append(node('span','시간 단서 '+Math.min(timeLimit,comparable.length)+' / '+comparable.length+'개 · 실제 행동 횟수와는 달라요','note'));
  if(comparable.length>timeLimit)more.append(button('시간순으로 12개 더 보기',()=>{timeLimit+=12;renderTimeline();}));
  if(uncertain.length>uncertainLimit)other.append(button('미상·비교 불가 12개 더 보기',()=>{uncertainLimit+=12;renderTimeline();},'time-more'));
  if(!list.some(t=>t.comparable))axis.append(empty(filter==='core'?'아직 시각을 배치할 수 있는 핵심 사실이 없습니다. 전체 연결 원문은 필터에서 볼 수 있습니다.':'이 범위에 공통 축으로 배치할 수 있는 시각이 없습니다.'));
  const count=list.filter(t=>!t.comparable).length;
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
  renderSummary();renderNarrative();renderActivity();renderQuestions();renderQuestion();renderSelection();renderTimeline();renderReports();alerts();
  for(const e of document.querySelectorAll('details[data-detail]'))if(opened.has(e.dataset.detail))e.open=true;
  for(const [name,top,left] of positions){const el=[...document.querySelectorAll('[data-scroll]')].find(e=>e.dataset.scroll===name);if(el){el.scrollTop=top;el.scrollLeft=left;}}
  const target=key?[...($(scope)||document).querySelectorAll('[data-key]')].filter(e=>e.dataset.key===key)[Math.max(0,ordinal)]:id?$(id):null;
  target?.focus({preventScroll:true});
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
}
// A local display clock only: no fetch, snapshot mutation or model request.
setInterval(()=>{if(!document.hidden)updateLiveWork();},1000);
$('refresh').onclick=synchronize;
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
