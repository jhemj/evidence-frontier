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
  let eta = s.case.status === 'running' && currentTask?.action === 'ai_judgment' && processed > 0 && finishedBatches >= 3 && phaseSeconds >= 60 && processed < total
    ? phaseSeconds / processed * (total-processed) : null;
  let etaScope = '단서 판단 단계';
  const progress=currentTask?.progress || {};
  let fraction=null, units='';
  if (progress.stage==='segment_hash' && Number.isFinite(progress.bytes_done) && progress.total_bytes>0) {
    fraction=Math.min(1,Math.max(0,progress.bytes_done/progress.total_bytes));
    units=`${(progress.bytes_done/1e9).toFixed(1)} / ${(progress.total_bytes/1e9).toFixed(1)} GB`;
    if(s.case.status==='running' && progress.elapsed_seconds>=10 && fraction>0 && fraction<1){
      eta=progress.elapsed_seconds*(1-fraction)/fraction;etaScope='입력 해시 단계';
    }
  } else if(progress.stage==='linux_hunt' && progress.candidates>0) {
    fraction=Math.min(1,Math.max(0,(progress.files_done || 0)/progress.candidates));
    units=`${(progress.files_done || 0).toLocaleString()} / ${progress.candidates.toLocaleString()} 파일`;
  } else if(progress.stage==='linux_discovery') units=`${(progress.entries || 0).toLocaleString()}개 경로 확인`;
  else if(progress.stage==='linux_contents') units=`${(progress.files_done || 0).toLocaleString()}개 파일 · ${(progress.records || 0).toLocaleString()}개 기록`;
  const group = t => ['integrity','inventory','normalize','timeline','crosscheck','linux_scan','windows_scan'].includes(t.action) ? 0 : ({linux_investigate:1,windows_investigate:1,ai_judgment:2,investigation_report:3}[t.action] ?? 0);
  const stages = ['자료 수집·점검','AI 추가 탐색','단서 판단·반증','보고서 저장'].map((name,i) => {
    const ts = tasks.filter(t => group(t) === i);
    return {name,state:ts.some(t => t.status === 'running') ? 'active' : ts.length && ts.every(t => !['running','queued'].includes(t.status)) ? (ts.some(t=>['failed','blocked','unsupported','partial'].includes(t.status))?'gap':'done') : 'pending'};
  });
  return {live,elapsed,reviewed,failed,excluded,total,processed,eta,etaScope,stages,fraction,units,
    label:s.case.status === 'paused' ? '일시정지' : live ? s.case.investigation_stage || currentTask?.label || '조사 진행 중' : starts.length ? s.completion?.label || '조사 종료 · 결과와 미확인 범위 확인' : '조사 대기',
    percent:total ? Math.floor(processed/total*100) : null};
}
function progressDuration(seconds) {
  const minutes = Math.max(0,Math.ceil(seconds/60));
  return minutes >= 60 ? `${Math.floor(minutes/60)}시간 ${minutes%60}분` : `${minutes}분`;
}
function conservativeEta(s,p,stale=false) {
  if(stale||s.case.status!=='running')return null;
  if(p.eta!==null&&(p.etaScope==='입력 해시 단계'||!s.eta_estimate)) {
    return {low_seconds:Math.max(60,p.eta*1.15),high_seconds:Math.max(120,p.eta*1.75),
      scope:p.etaScope,basis:'실측 처리 속도 + 여유',assumptions:'현재 처리 단계의 추정 범위이며 전체 조사 종료 시각이 아닙니다.'};
  }
  return s.eta_estimate||null;
}
function renderInvestigationProgress(s, receivedAt = Date.now()) {
  const root = document.getElementById('investigation-progress');
  if (!root) return;
  const p = investigationProgress(s);
  const stale = Date.now()-receivedAt > 60000;
  if(typeof renderIncidentStatus==='function')renderIncidentStatus(s,stale);
  const estimate=conservativeEta(s,p,stale);
  const etaText=estimate?`${progressDuration(estimate.low_seconds)}–${progressDuration(estimate.high_seconds)}`:stale?'연결 지연':s.case.status==='pause_requested'?'—':p.live?'작업 준비 중':'—';
  // Keep interactive controls mounted during second-by-second clock ticks.
  // Replacing the button on every tick can swallow clicks/keyboard focus.
  const renderKey=JSON.stringify([p.label,p.units,p.fraction,p.total,p.processed,p.reviewed,p.failed,p.excluded,p.live,stale,
    s.case.status,progressDuration(p.elapsed),etaText,estimate?.scope,estimate?.basis,s.activity]);
  if(root.dataset.progressKey===renderKey){
    const rows=new Map((s.activity?.items||[]).map(r=>[r.id,r]));
    root.querySelectorAll('[data-work-id]').forEach(node=>{
      const row=rows.get(node.dataset.workId),duration=node.querySelector('.work-duration');
      if(row&&duration)duration.textContent=workElapsed(row,Date.now(),stale);
    });
    syncWorkHistory(s);return;
  }
  root.dataset.progressKey=renderKey;
  const stripScroll=root.querySelector('.work-strip')?.scrollLeft||0;
  const historyFocused=document.activeElement?.matches('[data-work-history]');
  const status=stale?'연결 확인 중':s.case.status==='pause_requested'?'중지 준비 중':p.live?'조사 중':s.case.status==='paused'?'일시정지':'현재 상태';
  root.innerHTML = `<div class="ip-main"><div class="ip-top"><span class="section-number">01 / PROGRESS</span><span class="ip-state ${p.live&&!stale?'live':''}">${status}</span><h2>${esc(p.label)}</h2><p class="ip-units">${esc(p.units || (p.total?`단서 ${p.processed.toLocaleString()} / ${p.total.toLocaleString()} 처리`:''))}</p></div>
    <div class="ip-metrics"><div><small>경과 시간</small><strong>${progressDuration(p.elapsed)}</strong></div><div><small>남은 시간 · ETA</small><strong class="ip-eta-range">${esc(etaText)}</strong><span title="${esc(estimate?estimate.basis+' · '+estimate.assumptions:'')}">${estimate?esc(estimate.scope)+' · 보수적 추정':'현재 단계 기준'}</span></div></div></div>
    ${p.fraction!==null?`<div class="ip-bar phase-bar" role="progressbar" aria-label="현재 단계 처리율" aria-valuenow="${Math.floor(p.fraction*100)}" aria-valuemin="0" aria-valuemax="100"><i style="width:${p.fraction*100}%"></i></div>`:p.total?`<div class="ip-bar" role="progressbar" aria-label="단서 처리율 · 검토 실패와 보류 포함" aria-valuenow="${p.processed}" aria-valuemin="0" aria-valuemax="${p.total}"><i style="width:${p.reviewed/p.total*100}%"></i><i class="failed" style="width:${p.failed/p.total*100}%"></i><i class="excluded" style="width:${p.excluded/p.total*100}%"></i></div>`:''}
    ${workActivityMarkup(s.activity,stale)}
    ${p.failed||p.excluded?`<div class="ip-alert">검토 실패 ${p.failed} · 보류 ${p.excluded}<span>완료 판정과 별개</span></div>`:''}`;
  syncWorkHistory(s);
  if(root.querySelector('.work-strip'))root.querySelector('.work-strip').scrollLeft=stripScroll;
  if(historyFocused)root.querySelector('[data-work-history]')?.focus({preventScroll:true});
}
