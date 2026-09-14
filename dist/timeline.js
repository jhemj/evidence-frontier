"use strict";
let timelineState = { caseId: null, mode: "핵심", selected: null, limit: 12, bucket: null, signature: null };
const timelineDate = value => value ? new Intl.DateTimeFormat("ko-KR", {timeZone:"Asia/Seoul",year:"2-digit",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(new Date(value)) : "시각 미확인";
const timelineFullDate = value => value ? new Intl.DateTimeFormat("ko-KR", {timeZone:"Asia/Seoul",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(new Date(value)) : "시각 미확인";
function renderTimeline(data, investigation) {
  const root = document.getElementById("visual-timeline");
  if (!root || !data) return;
  if (timelineState.caseId !== investigation.id) timelineState = {caseId:investigation.id,mode:"핵심",selected:null,limit:12,bucket:null,signature:null};
  const signature = JSON.stringify([data,investigation.status,timelineState.mode,timelineState.selected,timelineState.limit,timelineState.bucket]);
  if (timelineState.signature === signature) return;
  const priorIds = new Set(Array.from(root.querySelectorAll("[data-timeline-card]"),n=>n.dataset.timelineCard));
  const historyOpen = root.querySelector(".tl-history")?.open || false;
  timelineState.signature = signature;
  const levelClass = level => ({"확인":"confirmed","유력":"likely","미확인":"unknown"}[level] || "unknown");
  const active = data.cards.filter(c=>c.role!=="반증됨" && !c.provisional && c.review_status==="reviewed");
  const start = Date.parse(data.start), end = Date.parse(data.end), span = Math.max(1,end-start);
  const position = card => Math.max(0,Math.min(100,(Date.parse(card.start)-start)/span*100));
  let cards = data.cards.filter(c=>timelineState.mode==="전체" || (timelineState.mode==="시각 미확인" ? !c.start : c.role===timelineState.mode));
  if (timelineState.bucket!==null) cards = cards.filter(c=>c.start && Date.parse(c.start)<=start+span*(timelineState.bucket+1)/60 && Date.parse(c.end)>=start+span*timelineState.bucket/60);
  cards.sort((a,b)=>(a.start ? Date.parse(a.start):Infinity)-(b.start?Date.parse(b.start):Infinity)||a.id.localeCompare(b.id));
  const visible = cards.slice(0,timelineState.limit);
  const selected = data.cards.find(c=>c.id===timelineState.selected);
  const max = Math.max(1,...data.bins);
  const running = ["running","pause_requested"].includes(investigation.status);
  const progress = data.review_progress;
  const historicalOpen = root.querySelector('.tl-review-history')?.open || false;
  const reviewSummary = progress?.total ? `<section class="tl-review-summary" aria-label="누적 검토 현황"><div><strong>한 번 이상 검토 ${progress.ever_reviewed} / ${progress.total}</strong><span>이번 검토 ${progress.current_reviewed} · 아직 미검토 ${progress.never_reviewed}</span></div><p>${esc(progress.scope)}</p>${progress.historical_only?`<details class="tl-review-history" ${historicalOpen?'open':''}><summary>이전 판단만 있는 단서 ${progress.historical_only}개 · 이번 재검증 아님</summary>${progress.historical_assessments.slice(0,50).map(h=>`<article><small>이전 ${esc(h.finding.judgment)} · AI 해석·정상성 미검증 · ${timelineFullDate(h.reviewed_at)}</small><strong>${esc(h.finding.title)}</strong><p>${esc(h.finding.reason)}</p>${h.finding.observation_ids.slice(0,4).map(id=>`<button data-ref="${esc(id)}">당시 근거 열기 ↗</button>`).join('')}</article>`).join('')}${progress.historical_only>50?'<p>50개 표시 · 전체 이력은 보고서에 보존</p>':''}</details>`:''}</section>` : '';
  const counts = ["확인","유력","미확인"].map(level=>`<span class="tl-count ${levelClass(level)}"><i></i>${level} <b>${active.filter(c=>c.judgment===level).length}</b></span>`).join("");
  root.innerHTML = `<div class="tl-heading"><div><span class="tl-eyebrow">EVIDENCE IN TIME</span><h2>흔적이 사건으로 연결되는 순간</h2><p>기록의 흐름을 따라, 근거와 판단의 변화를 한눈에.</p></div><span class="tl-live ${running?"is-live":""}"><i></i>${running?"조사 중 · 자동 갱신":"현재 조사 결과"}</span></div>
    <div class="tl-flow" aria-label="조사 흐름">${["자료 정리","기본 점검","정황 연결","반증·판단"].map((label,i)=>`<span class="${i===data.progress?.index?'active':i<(data.progress?.index||0)?'processed':''}"><i>${i+1}</i>${label}</span>`).join("")}</div><div class="tl-progress" role="status">${esc(data.progress?.text||"")}</div>
    <div class="tl-counts">${counts}<span class="tl-zone">사건 시각 · KST</span></div>
    <p class="tl-grade-note">등급 집계는 이번에 검토를 마친 주장만 포함합니다. 검토 전·실패·진행 중·과거 판단은 제외합니다. 정상성은 별도 미검증이며 침해 건수가 아닙니다.</p>
    ${reviewSummary}
    ${data.coverage_map?`<details class="tl-coverage"><summary>자료 처리 범위 · 중요 미처리 ${data.coverage_map.critical_gap_count}개</summary><p>읽은 자료와 AI가 검토한 단서는 서로 다른 범위입니다.</p>${Object.entries(data.coverage_map.families).map(([name,f])=>`<p><strong>${esc(name)}</strong> 발견 ${f.discovered} · 전체 읽음 ${f.fully_scanned} · 텍스트 파싱 ${f.text_parsed} · 미처리 ${f.unprocessed}</p>`).join('')}<p>${esc(data.coverage_map.time_coverage)}</p></details>`:''}
    <div class="tl-overview" aria-label="전체 기록 분포"><div class="tl-chart-caption"><span>전체 기록의 흐름</span><small>시간 확인된 관측 묶음 ${data.dated_groups.toLocaleString()}개</small></div>
      <div class="tl-histogram">${data.bins.map((count,i)=>`<button data-timeline-bucket="${i}" class="${timelineState.bucket===i?"selected":""}" style="--height:${Math.max(3,Math.sqrt(count/max)*100)}%" aria-label="${timelineDate(new Date(start+span*i/60).toISOString())} 부근 ${count}개 관측, 구간 보기" title="${count}개 관측"><span></span></button>`).join("") || '<p class="tl-empty">시간이 확인된 기록을 수집하면 흐름이 여기에 나타납니다.</p>'}</div>
      <div class="tl-rails">${["확인","유력","미확인"].map(level=>`<div class="tl-rail ${levelClass(level)}"><span>${level}</span><div>${active.filter(c=>c.start && c.judgment===level).slice(0,100).map(c=>`<button class="tl-dot ${c.provisional?"provisional":""}" style="left:${position(c)}%" data-timeline-select="${esc(c.id)}" aria-label="${esc(c.title)} · ${level} · ${timelineDate(c.start)}" title="${esc(c.title)}"></button>`).join("")}</div></div>`).join("")}</div>
      <div class="tl-axis"><span>${timelineFullDate(data.start)}</span><span>${timelineFullDate(data.end)}</span></div></div>
    <div class="tl-toolbar"><div class="tl-filters" aria-label="타임라인 필터">${["핵심","전체","시각 미확인","반증됨"].map(mode=>`<button data-timeline-mode="${mode}" aria-pressed="${timelineState.mode===mode}">${mode==="반증됨"?"반증된 해석":mode}</button>`).join("")}</div><span>${cards.length}개 근거 카드${timelineState.bucket!==null?'<button class="tl-reset" data-timeline-reset="true">전체 기간 ↗</button>':""}</span></div>
    <div class="tl-body"><div class="tl-events">${visible.map(c=>`<article data-timeline-card="${esc(c.id)}" class="tl-event ${levelClass(c.judgment)} ${c.role==='반증됨'?'refuted':''} ${timelineState.selected===c.id?'selected':''} ${priorIds.size&&!priorIds.has(c.id)?'arriving':''}"><div class="tl-time">${timelineDate(c.start)}${c.start&&c.end!==c.start?`<small>~ ${timelineDate(c.end)}</small>`:""}<i></i></div><button class="tl-card" data-timeline-select="${esc(c.id)}" aria-expanded="${timelineState.selected===c.id}"><span class="tl-card-meta"><span class="tl-level">${esc(c.judgment || "검토 전")}</span><span>${c.review_status==='model_failed'?'검토 실패 · 원문 보존':c.review_status==='deferred'?'검토 한도로 대기':c.provisional?'정황 검토 중':c.role==='반증됨'?'반증 근거로 해석 변경':'AI 검토됨'}</span></span><strong>${esc(c.title)}</strong>${c.sources[0]?.path?`<span class="tl-card-origin" title="${esc(c.sources[0].path)}">${esc(c.sources[0].path)}</span>`:""}<p>${esc(c.reason)}</p><small>연결 근거 ${c.sources.length}개 · 자세히 보기 ↗</small></button></article>`).join("") || `<div class="tl-empty-state"><span>◎</span><strong>${timelineState.mode==='반증됨'?'아직 반증으로 변경된 해석이 없습니다.':'근거를 연결하고 있습니다.'}</strong><p>${timelineState.bucket!==null?'다른 구간이나 전체 기간을 선택해 보세요.':'원문 점검 결과가 들어오면 핵심 단서가 여기에 쌓입니다.'}</p></div>`}${cards.length>visible.length?`<button class="tl-more" data-timeline-more="true">다음 근거 ${Math.min(12,cards.length-visible.length)}개 보기 ↓</button>`:""}</div>
    ${selected?`<aside class="tl-detail" aria-label="선택한 근거 상세"><button class="tl-detail-close" data-timeline-close="true" aria-label="근거 상세 닫기">×</button><span class="tl-eyebrow">EVIDENCE DETAIL</span><h3>${esc(selected.title)}</h3><p>${esc(selected.reason)}</p>${(selected.stages||[]).map(s=>`<p><strong>${esc(({configuration:"설정",invocation:"호출",execution:"실행",connection:"통신",objective:"목적 달성",intent:"의도"})[s.stage])} · ${esc(s.judgment)}</strong><br>${esc(s.statement)}</p>`).join("")}<div class="tl-time-note">${esc(selected.time_basis)}</div>${selected.sources.map(o=>`<div class="tl-source"><time>${timelineFullDate(o.timestamp)}</time><strong>${esc(o.path)}</strong>${o.time_basis?`<small>${esc(o.time_basis)}</small>`:""}<p>${esc(o.excerpt)}</p><button data-ref="${esc(o.id)}">원문 근거 열기 ↗</button></div>`).join("")||'<p>직접 연결할 기록이 부족합니다.</p>'}</aside>`:""}</div>
    <details class="tl-history" ${historyOpen?'open':''}><summary>판단 변경 이력 <span>${data.history_total}</span></summary><p>원문은 유지됩니다. 아래 시간은 AI가 판단을 갱신한 시각입니다.</p>${data.history.slice().reverse().map(h=>`<article><time>${timelineFullDate(h.at)}</time><strong>${esc(h.title)}</strong><small>${h.before===h.after?`판단 유지 · 설명 보완 (${esc(h.after)})`:`${esc(h.before)} → ${esc(h.after)}`}</small><p>${esc(h.reason)}</p></article>`).join("")||'<p>첫 판단 이후 변경이 생기면 여기에 기록됩니다.</p>'}${data.history_total>100?'<p>최근 100개 변경 표시 · 전체 판단은 조사 원장에 보존</p>':""}</details>
    <p class="tl-footnote">시각 미확인 관측 묶음 ${data.undated_groups.toLocaleString()}개 · ${esc(data.scope)}<br>선과 시간상 인접성은 인과관계를 뜻하지 않습니다. 날짜 없는 근거에는 임의 시각을 넣지 않습니다.</p>`;
}
document.addEventListener("click",e=>{
  const b=e.target.closest("button");if(!b)return;
  let handled=true;
  if(b.dataset.timelineMode){timelineState.mode=b.dataset.timelineMode;timelineState.limit=12;}
  else if(b.dataset.timelineSelect){timelineState.selected=timelineState.selected===b.dataset.timelineSelect?null:b.dataset.timelineSelect;}
  else if(b.dataset.timelineBucket!==undefined){timelineState.bucket=Number(b.dataset.timelineBucket);timelineState.limit=12;}
  else if(b.dataset.timelineReset){timelineState.bucket=null;}
  else if(b.dataset.timelineMore){timelineState.limit+=12;}
  else if(b.dataset.timelineClose){timelineState.selected=null;}
  else handled=false;
  if(handled && snapshot)renderTimeline(snapshot.visual_timeline,snapshot.case);
});
