"use strict";
// Presentation only: filtering never changes the evidence ledger or agent queue.
let clueFilter = 'all', companionView = 'chat', cockpitCase = null;
let previousClueVisibility = new Map(), resurfacedClues = new Set();
let cluePageSize=40, pendingPageSize=20, pendingExpanded=false;

function clueLanes(cards) {
  const pending=c=>c.kind==='dossier' && c.unreviewed && !(c.change_history || []).length;
  const quiet=c=>!pending(c) && Boolean(c.display?.collapsed_suggestion) && !c.display?.must_surface;
  return {visible:cards.filter(c=>!pending(c)&&!quiet(c)),pending:cards.filter(pending),quiet:cards.filter(quiet)};
}

const hypothesisStates = {investigating:'검증 중',strengthened:'근거 강화',weakened:'근거 약화',supported:'입증 · 해당 범위',refuted:'반증됨',inconclusive:'판단 보류'};
function lifecycleBadge(state) {
  const known=Object.hasOwn(hypothesisStates,state)?state:'investigating';
  return `<span class="lifecycle-badge ${known}">${hypothesisStates[known]}</span>`;
}
function changeHistory(card) {
  const rows=card.change_history || [];
  if(!rows.length)return '';
  return `<details class="card-history"><summary>판단 이력 ${rows.length}</summary><ol>${[...rows].reverse().map(r=>{
    const f=r.finding || r, added=(r.added_observation_ids || []).length;
    const type=r.change_type==='first_assessment'?'첫 판단':added?`인용 근거 ${added}건 추가`:r.change_type==='published'?'검토 반영':'해석 변경';
    return `<li><div><strong>${esc(type)}</strong>${r.lifecycle?lifecycleBadge(r.lifecycle):''}<time>${esc(r.at?new Date(r.at).toLocaleString('ko-KR',{timeZone:'Asia/Seoul'}):'')} KST</time></div><h5>${esc(f.title || '')}</h5><p>${esc(f.card_summary || r.change_reason || f.reason || f.reasoning || '')}</p>${r.published===false?'<small>후속 검토 전 중간 판단</small>':''}</li>`;
  }).join('')}</ol><p class="metric-note">인용 추가는 독립 증거의 증가를 뜻하지 않습니다. 변경 시각은 사건 발생 시각과 다릅니다.</p></details>`;
}

function clueLabel(card) {
  return ({'확인':'확정','확정':'확정','유력':'유력'})[card.semantic_judgment || card.label] || '추정';
}
function clueTime(card) {
  const event = card.event_time || {}, value = event.raw;
  // Sort all usable anchors together; their provenance stays visible on each card.
  if (!['occurred','estimated','file_metadata'].includes(event.time_kind) || !value || !/(Z|[+-]\d{2}:?\d{2})$/.test(value)) return null;
  const stamp = Date.parse(value);
  return Number.isFinite(stamp) ? stamp : null;
}
function orderClues(cards) {
  const preciseTime = (card,stamp) => {
    const ns=card.event_time.epoch_nanoseconds;
    if (/^-?\d+$/.test(ns)) return BigInt(ns);
    // Do not lose sub-millisecond order when an older snapshot has only raw time.
    const fraction=card.event_time.raw.match(/\.(\d+)(?:Z|[+-]\d{2}:?\d{2})$/)?.[1] || '';
    return BigInt(stamp)*1000000n + BigInt((fraction+'000000000').slice(3,9));
  };
  return [...cards].sort((a,b) => {
    const at = clueTime(a), bt = clueTime(b);
    if ((at===null)!==(bt===null)) return at===null?1:-1;
    if(at!==null&&bt!==null){
      const an=preciseTime(a,at), bn=preciseTime(b,bt);
      if(an!==bn)return an<bn?-1:1;
    }
    return String(a.id).localeCompare(String(b.id));
  });
}
function clueTimeDetails(card) {
  const t=card.event_time || {},anchors=t.anchors || [];
  if(!anchors.length&&!t.needs_time_followup&&!t.limitation)return '';
  return `<h4>시각 판단 근거</h4>${t.limitation?`<p class="metric-note">${esc(t.limitation)}</p>`:''}
    <ul class="time-anchors">${anchors.map(a=>`<li><strong>${esc(a.label || '기록 시각')}</strong><time>${esc(a.raw)}</time>${a.path?`<span>${esc(a.path)}</span>`:''}<span>${esc(a.basis)}</span><button data-ref="${esc(a.observation_id)}">시각 원문 ↗</button></li>`).join('')}</ul>
    ${t.anchors_omitted?`<p class="metric-note">추가 시각 근거 ${t.anchors_omitted}개는 원장에 보존되어 있습니다.</p>`:''}
    ${(t.next_checks||[]).length?`<h4>시각 추가 확인</h4><ul>${t.next_checks.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}`;
}
function fallbackClues(s) {
  // Older snapshots can show claims, but never an invented relevance score/time.
  return (s.judgments || []).flatMap(j => j.findings || []).map((f,i) => ({
    ...f,id:f.dossier_id || `legacy-${i}`,semantic_judgment:f.judgment,relevance_score:null,
    display:{collapsed_suggestion:false},state:'reviewed',event_time:null,time_kind:'unknown'
  }));
}
function clueCard(card) {
  const label = clueLabel(card), tone = {'확정':'confirmed','유력':'probable','추정':'tentative'}[label];
  const score = typeof card.relevance_score === 'number' && Number.isFinite(card.relevance_score)
    ? Math.min(100,Math.max(0,card.relevance_score)) : null;
  const refs = [...new Set(card.observation_ids || [])];
  const stamp = clueTime(card);
  const timeKind=stamp===null?'unknown':card.event_time.time_kind;
  const timeLabel={occurred:'기록 시각',estimated:'추정 시각',file_metadata:'파일 시각 기반 추정',unknown:'시각 미상'}[timeKind];
  const timeCaution=timeKind==='file_metadata'?'파일 시각을 기준으로 배치했습니다. 실제 행위 시각은 미확인입니다.':timeKind==='estimated'?'연도·시간대 등 추정 근거를 확인해야 합니다.':'';
  const timeTitle=[card.event_time?.raw,card.event_time?.time_label,card.event_time?.time_basis,timeCaution].filter(Boolean).join(' · ');
  const time = stamp === null ? '행위 시각 미확인' : new Intl.DateTimeFormat('ko-KR', {
    timeZone:'Asia/Seoul',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false
  }).format(new Date(stamp)) + ' KST';
  const state = ['model_failed','failed'].includes(card.state) ? '검토 실패' : card.state === 'deferred' ? '보류' : card.unreviewed ? '검토 중' : '';
  const change = card.revision>1 ? (card.change_type==='sources_added'?'근거 추가':card.change_type==='published'?'검토 반영':'해석 변경') : '';
  return `<article class="clue-card ${tone} time-${esc(timeKind)}" data-clue-id="${esc(card.id)}">
    <div class="clue-spine"><span class="clue-node"></span></div>
    <div class="clue-card-body"><div class="clue-meta"><span class="clue-badge"><i></i>${label}</span><span class="clue-clock"><time${stamp===null?'':` datetime="${esc(card.event_time.raw)}"`} title="${esc(timeTitle || timeLabel)}">${esc(time)}</time>${stamp===null?'':`<span class="time-badge ${timeKind}" title="${esc(timeTitle)}">${timeLabel}</span>`}</span>${resurfacedClues.has(card.id)?'<span class="resurfaced">새 연결</span>':''}${state?`<span class="clue-state">${state}</span>`:''}</div>
    ${card.kind==='hypothesis'?`<div class="hypothesis-card-label"><span>가설 해석</span>${lifecycleBadge(card.lifecycle)}</div>`:''}
    <h3>${esc(card.title || '검토 중인 단서')}</h3>
    <p class="clue-reason">${esc(card.card_summary || card.reason || '아직 판단이 등록되지 않았습니다.')}</p>
    ${(card.open_objections || []).length?`<div class="card-change"><strong>반론 검토 중 · ${card.open_objections.length}건</strong><p>${esc(card.publication_limit)}</p></div>`:''}
    ${change?`<div class="card-change"><strong>${change}</strong><p>${esc(card.change_reason || '연결된 원문을 바탕으로 판단이 갱신되었습니다.')}</p></div>`:''}
    <div class="clue-footer"><span class="relevance" title="사건 질문과의 근거 기반 관련성 · 침해 확률 아님">관련성 <strong>${score===null?'미평가':score}</strong>${score===null?'':'<span>/100</span>'}<i><b style="width:'+score+'%"></b></i></span><span class="source-count">근거 ${refs.length}</span></div>
    <details class="clue-detail" data-detail-key="${esc(card.id)}"><summary>근거와 가설 <span>↗</span></summary>
      <div class="clue-detail-content"><h4>판단 근거</h4><p>${esc(card.reason || '검토 전')}</p><div class="source-pills">${refs.map((id,i)=>`<button data-ref="${esc(id)}">원문 ${i+1} ↗</button>`).join('') || '<span class="subtle">직접 연결된 근거 없음</span>'}</div>
      ${(card.counterevidence_ids || []).length?`<h4>반대 근거</h4><div class="source-pills">${card.counterevidence_ids.map((id,i)=>`<button data-ref="${esc(id)}">반대 근거 ${i+1} ↗</button>`).join('')}</div>`:''}
      ${(card.open_objections || []).length?`<h4>미해결 반론</h4><ul>${card.open_objections.map(o=>`<li>${esc(o.statement)}<div class="source-pills">${(o.observation_ids || []).map(id=>`<button data-ref="${esc(id)}">반론 원문 ↗</button>`).join('')}</div></li>`).join('')}</ul>`:''}
      ${(card.alternatives || []).length?`<h4>가능한 다른 설명</h4><ul>${card.alternatives.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}
      ${(card.hypothesis_links || []).length?`<h4>연결된 가설</h4><ul class="linked-hypotheses">${card.hypothesis_links.map(x=>`<li>${lifecycleBadge(x.lifecycle)}<span>${esc(x.title || x.text || x.number)}</span>${x.card_summary?`<p>${esc(x.card_summary)}</p>`:''}</li>`).join('')}</ul>`:''}
      ${clueTimeDetails(card)}
      ${changeHistory(card)}
      ${(card.remaining_checks || []).length?`<h4>다음 확인</h4><ul>${card.remaining_checks.map(x=>`<li>${esc(x)}</li>`).join('')}</ul>`:''}
      <h4>사건 관련성</h4><p class="metric-note">직접 85 · 간접 55 · 참고 10. 침해 확률·사실 확신도와 별개입니다.</p><ul>${(card.why || []).map(x=>`<li>${esc(x.driver)}</li>`).join('') || '<li>관련성 미평가</li>'}</ul>
      ${(card.priority_reasons || []).length ? `<p class="metric-note">조사 우선 확인: ${card.priority_reasons.map(esc).join(' · ')}</p>` : ''}
      <button class="ask-clue" data-question="${esc('이 단서의 판단 근거와 다른 설명을 검토해줘: '+card.title+' (단서 ID: '+card.id+')')}">이 단서에 대해 질문하기 →</button></div>
    </details></div></article>`;
}
function renderClueList(cards, empty) {
  if (!cards.length) return `<div class="board-empty"><span class="empty-cross">＋</span><h3>${empty}</h3><p>근거가 연결되면 이곳에 나타납니다.</p></div>`;
  const ordered=orderClues(cards), dated=ordered.filter(c=>clueTime(c)!==null), undated=ordered.filter(c=>clueTime(c)===null);
  return `${dated.length?'<div class="timeline-caption">시간순<span>기록·추정 시각 · KST</span></div>':''}<div class="clue-timeline">
    ${dated.map(clueCard).join('')}
    ${undated.length?`<div class="timeline-caption unknown-caption">시각 미상<span>시각 확인 후 위 타임라인에 배치</span></div>${undated.map(clueCard).join('')}`:''}</div>`;
}
function renderCockpit(s) {
  if (cockpitCase !== s.case.id) {
    cockpitCase=s.case.id; previousClueVisibility=new Map(); resurfacedClues=new Set(); clueFilter='all'; reportLoadedRevision=null;
    resetReportPreview();
    cluePageSize=40;pendingPageSize=20;pendingExpanded=false;$('pending-clues').open=false;
    document.querySelectorAll('[data-clue-filter]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.clueFilter==='all'));
  }
  const cards = orderClues(s.triage?.cards || fallbackClues(s));
  const quiet = c=>Boolean(c.display?.collapsed_suggestion) && !c.display?.must_surface;
  for (const c of cards) {
    if (previousClueVisibility.get(c.id)===false && !quiet(c)) resurfacedClues.add(c.id);
    previousClueVisibility.set(c.id,!quiet(c));
  }
  const selected = cards.filter(c=>clueFilter==='all'||clueLabel(c)===clueFilter);
  const lanes=clueLanes(selected), visible=lanes.visible, hidden=lanes.quiet;
  const signature = JSON.stringify([current,clueFilter,selected,[...resurfacedClues],cluePageSize,pendingPageSize,pendingExpanded]);
  if ($('triage-console').dataset.signature!==signature) {
    const expanded = new Set([...document.querySelectorAll('.clue-detail[open]')].map(x=>x.dataset.detailKey));
    $('triage-console').innerHTML=renderClueList(visible.slice(0,cluePageSize),lanes.pending.length?'단서의 의미를 검토하고 있습니다':cards.length?'이 등급의 주요 단서가 없습니다':'단서를 연결하고 있습니다');
    $('quiet-clue-list').innerHTML=$('quiet-clues').open?renderClueList(hidden.slice(0,cluePageSize),'접어둔 단서가 없습니다'):'';
    $('pending-clue-list').innerHTML=pendingExpanded?renderClueList(lanes.pending.slice(0,pendingPageSize),'검토 대기 항목이 없습니다'):'';
    document.querySelectorAll('.clue-detail').forEach(x=>{x.open=expanded.has(x.dataset.detailKey);});
    $('triage-console').dataset.signature=signature;
  }
  $('quiet-count').textContent=hidden.length;
  $('quiet-clues').hidden=!hidden.length;
  $('pending-count').textContent=lanes.pending.length;
  $('pending-clues').hidden=!lanes.pending.length;
  $('pending-more').hidden=lanes.pending.length<=pendingPageSize;
  $('clue-more').hidden=visible.length<=cluePageSize && hidden.length<=cluePageSize;
  $('clue-more').textContent=`다음 단서 보기 · 주요 ${Math.min(cluePageSize,visible.length)}/${visible.length}`;
  const allLanes=clueLanes(cards);
  $('clue-count').textContent=`주요 ${allLanes.visible.length} · 검토 대기 ${allLanes.pending.length} · 전체 ${cards.length}`;
  renderScenarios(s);
  const latest=s.report?.filter(r=>r.reader_contract).at(-1);
  $('live-report').innerHTML=`<span class="report-symbol">▤</span><span><strong>현재까지의 보고서</strong><small>${latest?'저장본 있음 · 최신 초안 확인':'조사와 함께 작성 중'}</small></span><span>↗</span>`;
  $('companion-state').textContent=['running','pause_requested'].includes(s.case.status)?'에이전트 조사 중':'Frontier';
  if (companionView==='report' && !selectedReport && reportLoadedRevision!==(s.report_scope_revision ?? s.view_revision) && !reportLoading && Date.now()>=reportNextRefreshAt) {
    perform(()=>loadReport());
  }
}
async function switchCompanion(view) {
  companionView=view;
  $('companion-chat').hidden=view!=='chat';
  $('panel-report').hidden=view!=='report';
  document.querySelectorAll('.companion-tabs [data-companion]').forEach(b=>{
    b.classList.toggle('active',b.dataset.companion===view);b.setAttribute('aria-pressed',b.dataset.companion===view);
  });
  if(view==='report') await loadReport();
}
function initializeCockpit() {
  initializeScenarios();
  $('report-dock').append($('panel-report'));
  $('clue-help').onclick=()=>{ $('clue-help-text').hidden=!$('clue-help-text').hidden; $('clue-help').setAttribute('aria-expanded',!$('clue-help-text').hidden); };
  $('report-live').onclick=()=>perform(async()=>{selectedReport=null;await loadReport();});
  $('report-retry').onclick=()=>perform(()=>loadReport());
  $('clue-more').onclick=()=>{cluePageSize+=40;if(snapshot)renderCockpit(snapshot);};
  $('pending-more').onclick=()=>{pendingPageSize+=20;if(snapshot)renderCockpit(snapshot);};
  $('pending-clues').addEventListener('toggle',()=>{pendingExpanded=$('pending-clues').open;if(snapshot)renderCockpit(snapshot);});
  $('quiet-clues').addEventListener('toggle',()=>{$('triage-console').dataset.signature='';if(snapshot)renderCockpit(snapshot);});
  document.addEventListener('click',e=>{
    const b=e.target.closest('button');if(!b || b.disabled)return;
    if(b.dataset.clueFilter) {clueFilter=b.dataset.clueFilter;document.querySelectorAll('[data-clue-filter]').forEach(x=>x.setAttribute('aria-pressed',x===b));if(snapshot)renderCockpit(snapshot);}
    if(b.dataset.companion)perform(()=>switchCompanion(b.dataset.companion));
    if(b.dataset.question){perform(async()=>{await switchCompanion('chat');$('message').focus();});}
  });
  $('message').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!busy&&$('message').value.trim())$('chat-form').requestSubmit();}});
}
if (typeof document !== 'undefined') initializeCockpit();
