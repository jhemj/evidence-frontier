'use strict';
const state=new ObserverState.ViewState();
const $=id=>document.getElementById(id);
let session=null, selectedQuestion=null, requestPending=false, offline=false, focusedMode=0, pollTimer=null;
const renderer=new ObserverRenderer.RendererHost(({available,failed,hidden})=>{
  document.querySelector('.moa').hidden=!!hidden||available;
  $('renderer-status').textContent=failed?'렌더러 오류 · 정적 안내자로 대체':available?'로컬 렌더러 연결됨':'정적 안내자 · Live2D 모델 미연결';
});
const reducedMotion=matchMedia('(prefers-reduced-motion: reduce)');
renderer.preferences({reduced:reducedMotion.matches,visible:!document.hidden});
const execution={candidate:'검사 후보',queued:'실행 예정',running:'실행 중',blocked:'입력·능력 차단',
  succeeded:'실행 성공',covered:'지정 범위 처리',partial:'부분 결과',failed:'실패',waiting:'응답·실행 대기',
  received:'응답 수신 · 채택과 별개',input_registered:'입력 등록 · 요청 상태 미제공',interrupted:'중단',cancelled:'취소',unknown:'상태 미제공'};
const questions={open:'조사 중',reopened:'재검토',held:'판단 보류',input_wait:'입력 대기',scoped_answered:'현재 범위 종결',unknown:'미제공'};
const validity={adopted:'원장 채택',candidate:'후보 · 검토 전',invalidated:'무효화',unresolved_references:'출처 연결 공백',historical_assessment:'보존된 해석'};
const caseStates={running:'조사 중',paused:'일시정지',completed:'실행 종료',failed:'실행 실패',created:'시작 전'};
const types={claim:'주장',observation:'원문 관측',test:'판별 검사',hypothesis:'경쟁 설명',question:'질문'};
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
function refButton(key,label) {
  const o=state.displayed.objects[key];
  if(!o)return node('span','연결되지 않은 참조','note');
  const b=button(label||o.title,()=>{state.select(key);renderSelection();renderTimeline();},'source-link');b.dataset.key=key;return b;
}
function safeFreshness() {
  if(offline)return '연결 끊김 — 마지막 보존 스냅샷';
  if(state.sync!=='ready')return '정합성 재동기화 필요 — 최신 표시 제한';
  if(session?.mode==='read_only_live')return session.source_status==='observed'?
    '읽기 전용 관측 · '+session.poll_seconds+'초 간격 · 마지막 확인 '+dt(session.observed_at):'원장 관측 지연·실패 — 현재 상태 미확인';
  return '보존 데이터 연결됨 · 실시간 아님';
}
function currentNarratives(){return offline||session?.mode==='read_only_live'&&session.source_status!=='observed'?[]:state.narratives();}
function currentActivity(){return state.liveActivity||state.current.activity;}
function alerts() {
  const warnings=[];
  if(offline)warnings.push('화면 데이터 연결 끊김. 마지막으로 받은 보존 시점의 내용입니다. 현재 작업 상태는 알 수 없습니다.');
  if(state.sync!=='ready')warnings.push('정정 또는 수신 순서 공백을 확인했습니다. 근거 설명을 제한하고 스냅샷 재동기화가 필요합니다.');
  if(session?.mode==='read_only_live'&&session.source_status!=='observed')warnings.push('원장 관측이 지연되거나 실패했습니다. 현재 실행 상태를 단정하지 않습니다. 마지막 확인: '+dt(session.observed_at));
  if(state.pinnedChanged())warnings.push('고정한 과거 스냅샷입니다. 이후 판단·근거 참조 변경이 있습니다. 현재 읽는 원문과 위치는 유지했습니다.');
  if(state.changes.length)warnings.push('기존 주장·질문 표시 '+state.changes.length+'건이 변경됐습니다(근거 참조 포함). 모두 사실관계 정정을 뜻하지는 않습니다. 이전 내용은 변경 이력에서 확인할 수 있습니다.');
  if(state.omittedChanges)warnings.push('이 브라우저의 변경 이력은 최근 200건만 표시합니다. 앞선 '+state.omittedChanges+'건은 원장 이력에서 확인해야 합니다.');
  const failures=state.current?currentActivity().items.filter(a=>a.state==='failed'):[];
  if(failures.length)warnings.push('보존된 실패 이력 '+failures.length+'건 · 후속 복구 여부는 미평가. 현재 전체 조사의 실패를 뜻하지 않습니다. 작업 이력에서 영향 범위를 확인하세요.');
  const c=clear('critical');c.hidden=!warnings.length;for(const w of warnings)c.append(node('p',w));
  $('connection').textContent=safeFreshness();
  renderer.update({base:offline?'offline':currentActivity().items.some(a=>a.state==='running')?'working':'idle',
    transient:'none',severity:warnings.length?'warning':'info'});
}
function renderSummary() {
  const v=state.displayed, s=v.summary;
  const narrative=currentNarratives().find(item=>item.refs.length);
  $('mode').textContent=v.envelope.data_mode==='example'?'예시 · 실제 사건 아님':v.envelope.data_mode==='live'?'실제 조사 · 읽기 전용 관찰':'실데이터 보존 재생';
  $('case-name').textContent=v.case.name;$('run-name').textContent=v.envelope.run_id;
  $('basis').textContent='판단 기준 '+dt(v.envelope.captured_at);
  $('revision').textContent=v.envelope.case_id+' / '+v.envelope.run_id+' / snapshot '+v.envelope.projection_revision.slice(0,12);
  $('summary-title').textContent=state.pinned?'고정한 과거 판단 요약':'현재 판단 요약';
  const summary=clear('summary');
  summary.append(line('대표 명제',narrative?.text||'현재 설명에 사용할 채택 주장이 없습니다.'),
    line('남은 공백',narrative?.limitations[0]||'연결된 한계 미제공 · 사실관계가 모두 확인됐다는 뜻이 아닙니다.'),
    line('조사 상태','판단 기준 시점 '+(caseStates[v.case.status]||'상태 미제공')+(v.envelope.data_mode==='live'?' · 작업 관측 시점은 별도 표시':' · 현재 실행은 이 화면에서 확인하지 않습니다.')),
    node('p','원장 채택 주장 '+s.adopted_claims+'개 · 검사 결과 미평가 '+s.unassessed_tests+'개 · 출처 연결 공백 '+s.missing_reference_claims+'개. 침해 확률·사건 규명률이 아닙니다.','note'));
  $('pin').setAttribute('aria-pressed',String(!!state.pinned));$('pin').textContent=state.pinned?'최신 보존본으로 돌아가기':'이 스냅샷 고정';
  $('report-count').textContent=v.reports.length?v.reports.length+'개':'미생성';
}
function renderNarrative() {
  const n=clear('narrative'), list=currentNarratives();
  if(state.pinned)n.append(badge('고정한 과거 설명 · 현재 판단과 구별','warn'));
  if(!list.length)n.append(node('p','연결·정정 상태를 확인하는 동안 판단 설명을 제한합니다. 아래 원장은 과거 보존 내용으로만 열람할 수 있습니다.'));
  for(const item of list) {
    n.append(node('p',item.text));
    n.append(node('p','원문 기록 확인과 실제 실행 성공·침해 확정은 다릅니다.','note'));
    if(item.limitations.length) {
      const limits=node('details');limits.append(node('summary','연결된 한계·남은 확인 '+item.limitations.length+'개'));
      limits.dataset.detail='narrative-'+item.dedupe_key;
      for(const text of item.limitations)limits.append(node('p',text,'note'));n.append(limits);
    }
    for(const ref of item.refs)n.append(refButton(ref.key,'연결된 주장 보기'));
  }
  n.append(node('p','조사 설명 · 별도 모델 생성 없음','note'));
}
function activityRow(a) {
  const n=node('div',null,'activity-item');
  n.append(badge(execution[a.state]||'상태 미제공',a.state==='failed'?'error':''),node('strong',a.title),node('div',a.target,'target'));
  if(a.error)n.append(node('p',a.error,'note'));
  n.append(node('div',dt(a.at),'when'));
  n.append(node('div',a.id,'target'));return n;
}
function renderActivity() {
  // Global activity always refers to the newest received snapshot, not selected/pinned evidence.
  const data=currentActivity(), live=session?.mode==='read_only_live';
  const active=data.items.filter(a=>['running','waiting'].includes(a.state));
  const n=clear('activity');
  n.append(node('p','작업 확인: '+dt(data.checked_at)+(live?' · 원장 관측':' · 보존 상태'),'note'));
  for(const a of active.slice(0,5))n.append(activityRow(a));
  if(active.length>5)n.append(node('p','외 '+(active.length-5)+'개 · 전체 이력에서 확인','note'));
  if(!active.length)n.append(empty('이 관측 범위에서 실행 중으로 확인된 작업이 없습니다. 원장 미제공 상태를 실행 없음으로 단정하지 않습니다.'));
  const unknown=data.items.filter(a=>a.state==='input_registered').length;
  if(unknown)n.append(node('p','입력만 등록된 '+unknown+'건은 실제 요청·실행 여부 미확인입니다.','note'));
  n.append(node('p','ETA 산정 불가 · '+data.eta_reason,'note'));
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
    b.append(node('span','QUESTION '+String(i+1).padStart(2,'0'),'qnum'),node('strong',q.title),badge(questions[q.state]||q.state),
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
  n.append(badge('열람 중인 질문 · 실행 작업과 별개'),node('h2',q.title));
  const answer=node('div',null,'answer');answer.append(badge(questions[q.state]||q.state),node('p',q.answer),node('p','종결은 답변 범위의 상태이며 사건 전체 정상·침해 확정이 아닙니다.','note'));n.append(answer);
  for(const [title,keys,missing] of [
    ['01 · 좁은 주장',q.claim_keys,'명시적으로 연결된 주장이 없습니다. 원문에서 해석을 자동 생성하지 않습니다.'],
    ['02 · 경쟁 설명',q.hypothesis_keys,'명시적으로 연결된 경쟁 설명이 없습니다. 정상 설명이 확인됐다는 뜻은 아닙니다.'],
    ['03 · 판별 검사',q.test_keys,'다음 판별 검사가 선정되지 않았습니다. 진행 중인 것처럼 약속하지 않습니다.']]) {
    const group=node('section',null,'group');group.append(node('h3',title));
    for(const key of keys)group.append(entityCard(state.displayed.objects[key]));
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
  n.append(badge(types[o.type]),node('h3',o.title),node('p',o.id+' · v '+o.version.slice(0,12),'source-locator'));
  if(state.isRestricted(o))n.append(empty('정정 영향: 이 항목은 현재 설명에 사용할 수 없습니다. 재동기화가 필요합니다.'));
  if(o.type==='claim'||o.type==='hypothesis') {
    n.append(node('p',o.statement||o.reason||'설명 미제공'));
    const dl=node('dl');addDefinition(dl,'유효성',validity[o.validity]);addDefinition(dl,'원장 판단',o.judgment);addDefinition(dl,'주장 종류',o.claim_kind||'해석');addDefinition(dl,'적용 범위',o.scope);addDefinition(dl,'최신성',o.review_recency);n.append(dl);
    for(const [title,items] of [['판단 근거',[o.reason].filter(Boolean)],['경쟁 설명·반론',o.counterarguments],['미확인·남은 검사',o.gaps]]) {
      n.append(node('h3',title));if(!items?.filter(Boolean).length)n.append(node('p','미제공 · 부재로 해석하지 않음','note'));
      else {const ul=node('ul');for(const text of items.filter(Boolean))ul.append(node('li',text));n.append(ul);}
    }
  }
  if(o.type==='test') {
    const dl=node('dl');addDefinition(dl,'검사 실행',execution[o.execution]);addDefinition(dl,'판별 결과',o.discrimination==='unassessed'?'미평가':o.discrimination);addDefinition(dl,'영향·차단',o.execution_reason);addDefinition(dl,'실제 작업',o.job_ids);addDefinition(dl,'검사 대상',o.request);addDefinition(dl,'지지 조건',o.conditions.success_condition);addDefinition(dl,'반박 조건',o.conditions.refutation_condition);addDefinition(dl,'판별 불가',o.conditions.inconclusive_condition);addDefinition(dl,'즉시 관측값',o.design.immediate_observable);n.append(dl);
    n.append(node('p','도구 실행 성공은 가설 입증이 아닙니다. 결과가 없거나 부분 검색인 경우 반박으로 승격하지 않습니다.','note'));
  }
  if(o.type==='observation') {
    n.append(node('p',o.limitation,'note'));
    const excerpt=node('pre',o.excerpt===null||o.excerpt===undefined?'텍스트 발췌 미제공 · 원문 바이너리를 실행하지 않습니다.':o.excerpt,'excerpt');
    excerpt.dataset.scroll='excerpt-'+o.key;n.append(excerpt);
    if(o.excerpt_partial)n.append(node('p','부분 발췌입니다. 보존 필드 '+(o.excerpt_characters??'미상')+'자 중 표시 범위만 제공합니다.','note'));
    const dl=node('dl');addDefinition(dl,'원문 위치',o.source_location);for(const [key,value] of Object.entries(o.locator))addDefinition(dl,key,value);addDefinition(dl,'독립성',o.independence);addDefinition(dl,'기록 수',o.record_count);n.append(dl);
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
  const b=button(null,()=>{state.select(o.key);renderSelection();},'time-card '+t.lane+(t.estimated?' estimated':''));
  b.dataset.key=t.id;
  b.append(badge(t.meaning,t.comparable?'':'warn'));
  b.append(node('span',t.shape==='unknown'?'시각 미제공':t.shape==='candidates'?'복수 시각 후보':t.shape==='interval'?'연속 구간':t.estimated?'추정 시각':'기록된 값','time-basis'));
  for(const raw of t.raw_values)b.append(node('span',raw??'미상','clock'));
  if(!t.raw_values.length)b.append(node('span','배치할 시각 없음','clock'));
  b.append(node('span',o.title,'time-title'),node('span',t.basis,'time-basis'));
  return b;
}
function renderTimeline() {
  const sources=questionSources(), list=state.displayed.timeline.filter(t=>sources.has(t.source_ref.key));
  const axis=clear('timeline'), other=clear('uncertain');
  for(const t of list)(t.comparable?axis:other).append(timeCard(t));
  if(!list.some(t=>t.comparable))axis.append(empty('선택 질문에 공통 축으로 배치할 수 있는 시각이 없습니다.'));
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
    div.append(node('strong',r.report_id),node('p',dt(r.generated_at),'note'),badge(current.freshness==='same_ledger_scope'?'같은 원장 범위':current.freshness==='older_scope'?'이전 범위 · 정정 영향 가능':'최신성 미확인','warn'),node('p',r.claim_version_limitation,'note'));
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
$('refresh').onclick=synchronize;
$('pin').onclick=()=>{state.pin(!state.pinned);render();};
$('guide-size').onclick=()=>{focusedMode=(focusedMode+1)%3;$('workspace').className='workspace'+(focusedMode===1?' focus':focusedMode===2?' expanded':'');$('guide-size').textContent=['집중 모드','대화 확장','기본 배치'][focusedMode];};
$('hide-moa').onclick=()=>{renderer.preferences({hidden:!renderer.hidden});$('hide-moa').setAttribute('aria-pressed',String(renderer.hidden));$('hide-moa').textContent=renderer.hidden?'표시':'숨김';};
$('motion').onchange=()=>renderer.preferences({motion:$('motion').checked});
reducedMotion.addEventListener('change',e=>renderer.preferences({reduced:e.matches}));
document.addEventListener('visibilitychange',()=>{renderer.preferences({visible:!document.hidden});schedulePoll();if(!document.hidden&&session?.mode==='read_only_live')synchronize();});
$('activity-filter').onchange=renderActivity;
$('question-filter').oninput=renderQuestions;
$('timeline-layout').onclick=()=>{const list=$('timeline').classList.toggle('list');$('timeline-layout').setAttribute('aria-pressed',String(list));$('timeline-layout').textContent=list?'가로축으로 보기':'목록으로 보기';};
$('reports-open').onclick=()=>{$('reports-dialog').showModal();};
$('reports-close').onclick=()=>{$('reports-dialog').close();};
// Native buttons/summary provide keyboard selection/expand without pretending
// this is an ARIA tree. Focus alone never selects or executes an investigation.
synchronize();
