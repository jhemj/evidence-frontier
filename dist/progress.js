"use strict";
function investigationProgress(s, now = Date.now()) {
  const active = new Set(s.evidence.filter(e => e.connected !== false).map(e => e.id));
  const tasks = s.task.filter(t => active.has(t.evidence_id) && !t.superseded);
  const parse = x => Date.parse(x) || 0;
  const starts = tasks.map(t => parse(t.started_at)).filter(Boolean);
  const live = ['running','pause_requested'].includes(s.case.status);
  const stopped = !live && s.case.status !== 'paused';
  const end = stopped ? Math.max(0,...tasks.map(t => parse(t.ended_at))) : now;
  const elapsed = starts.length ? Math.max(0,(end-Math.min(...starts))/1000) : 0;
  const currentTask = tasks.find(t => t.status === 'running') || tasks.find(t => t.status === 'queued');
  const judgment = tasks.find(t => t.action === 'ai_judgment');
  const dossiers = (s.dossier || []).filter(d => d.task_id === judgment?.id && d.generation === (judgment?.retry_generation || 0));
  const reviewed = dossiers.filter(d => d.status === 'reviewed').length;
  const failed = dossiers.filter(d => d.status === 'model_failed').length;
  const excluded = dossiers.filter(d => ['deferred','unavailable'].includes(d.status)).length;
  const total = dossiers.length, processed = reviewed + failed + excluded;
  const phaseSeconds = judgment?.started_at ? Math.max(0,(now-parse(judgment.started_at))/1000) : 0;
  // Queue throughput includes failures, which are separately visible below.
  // This is not an estimate for collecting evidence or generating the report.
  const batches = (s.dossier_batch || []).filter(b => b.task_id === judgment?.id && b.generation === (judgment?.retry_generation || 0));
  const finishedBatches = batches.filter(b => ['done','failed'].includes(b.status)).length;
  const eta = live && currentTask?.action === 'ai_judgment' && processed > 0 && finishedBatches >= 3 && phaseSeconds >= 60 && processed < total
    ? phaseSeconds / processed * (total-processed) : null;
  const group = t => ['integrity','inventory','normalize','timeline','crosscheck','linux_scan'].includes(t.action) ? 0 : ({linux_investigate:1,ai_judgment:2,investigation_report:3}[t.action] ?? 0);
  const stages = ['자료 수집·점검','AI 추가 탐색','단서 판단·반증','보고서 저장'].map((name,i) => {
    const ts = tasks.filter(t => group(t) === i);
    return {name,state:ts.some(t => t.status === 'running') ? 'active' : ts.length && ts.every(t => !['running','queued'].includes(t.status)) ? 'done' : 'pending'};
  });
  return {live,elapsed,reviewed,failed,excluded,total,processed,eta,stages,
    label:s.case.status === 'paused' ? '일시정지' : live ? currentTask?.label || '조사 진행 중' : starts.length ? '조사 종료 · 결과와 미확인 범위 확인' : '조사 대기',
    percent:total ? Math.floor(processed/total*100) : null};
}
function progressDuration(seconds) {
  const minutes = Math.max(0,Math.ceil(seconds/60));
  return minutes >= 60 ? `${Math.floor(minutes/60)}시간 ${minutes%60}분` : `${minutes}분`;
}
function renderInvestigationProgress(s, receivedAt = Date.now()) {
  const root = document.getElementById('investigation-progress');
  if (!root) return;
  const p = investigationProgress(s);
  const stale = Date.now()-receivedAt > 60000;
  const eta = stale ? null : p.eta;
  const finish = eta === null ? '' : new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',hour:'2-digit',minute:'2-digit',hour12:false}).format(new Date(Date.now()+eta*1000));
  root.innerHTML = `<div class="ip-top"><div><span class="ip-eyebrow">LIVE INVESTIGATION</span><h2>${esc(p.label)}</h2></div><span class="ip-state ${p.live?'live':''}">${stale?'업데이트 지연':p.live?'진행 중 · 자동 갱신':'현재 결과'}</span></div>
    <div class="ip-metrics"><div><small>시작 후 경과</small><strong>${progressDuration(p.elapsed)}</strong><span>대기·일시정지 시간 포함</span></div><div><small>판단 종료까지 예상</small><strong>${eta===null?(p.live?'계산 중':'—'): '약 '+progressDuration(eta)}</strong><span>${eta===null?'처리 속도가 쌓이면 표시':`약 ${finish} KST · 보고서 저장 시간 별도`}</span></div><div><small>단서 처리</small><strong>${p.processed.toLocaleString()} <em>/ ${p.total?p.total.toLocaleString():'집계 중'}</em></strong><span>검토 완료와 실패·보류를 구분</span></div></div>
    <div class="ip-stage-list">${p.stages.map((x,i)=>`<span class="${x.state}"><b>${x.state==='done'?'✓':i+1}</b>${x.name}</span>`).join('')}</div>
    ${p.total?`<div class="ip-bar" role="progressbar" aria-label="단서 처리율 · 검토 실패와 보류 포함" aria-valuenow="${p.processed}" aria-valuemin="0" aria-valuemax="${p.total}"><i style="width:${p.reviewed/p.total*100}%"></i><i class="failed" style="width:${p.failed/p.total*100}%"></i><i class="excluded" style="width:${p.excluded/p.total*100}%"></i></div><div class="ip-counts"><span>검토 완료 <b>${p.reviewed}</b></span><span>검토 실패 <b>${p.failed}</b></span><span>보류·자료 부족 <b>${p.excluded}</b></span><span>남은 단서 <b>${p.total-p.processed}</b></span><strong>${p.percent}% 처리</strong></div>`:''}
    <p class="ip-note">ETA는 현재 판단 단계의 평균 처리 속도에 따른 추정입니다. 추가 검사·재시도로 변동하며, 처리율은 침해 확인율이 아닙니다.</p>`;
}
