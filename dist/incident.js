"use strict";
const incidentLevels=['undetermined','suspected','probable','confirmed'];
const incidentLabels={undetermined:'판단 대기',suspected:'침해 의심',probable:'침해 유력',confirmed:'침해 확정'};
function incidentMarkup(s,stale=false) {
  const data=s.incident_status || {}, counts=data.counts || {};
  // Older engines have narrow fact judgments only. Never reinterpret their
  // confirmed cards/relevance scores as an intrusion verdict.
  const valid=data.version==='incident-assessment-1'&&data.leading&&incidentLevels.includes(data.verdict);
  const verdict=valid?data.verdict:'undetermined', rank=incidentLevels.indexOf(verdict);
  const assessed=(data.assessments || []).length;
  const facts=(s.triage?.cards || []).filter(c=>c.kind==='dossier'&&!c.unreviewed&&c.state==='reviewed').length;
  const supporting=['suspected','probable','confirmed'].reduce((n,k)=>n+(counts[k]||0),0), refuted=counts.refuted||0;
  const remaining=(counts.undetermined||0)+(data.pending||0);
  const segments=[['지지',supporting,'support'],['반박',refuted,'refute'],['보류',remaining,'held']];
  const total=segments.reduce((n,x)=>n+x[1],0);
  const lead=valid?data.leading:null;
  return `<div class="incident-heading"><span>침해 판단</span><span class="incident-live">${stale?'연결 지연 · 이전 판단':['running','pause_requested'].includes(s.case.status)?'LIVE':'현재까지'}</span></div>
    <div class="incident-result ${verdict}"><span class="incident-signal" aria-hidden="true"></span><h2>${incidentLabels[verdict]}</h2></div>
    <ol class="incident-rail" aria-label="침해 판단 단계 · 확률이 아닙니다">${incidentLevels.map((key,i)=>`<li class="${key===verdict?'current ':''}${i>0&&i<=rank?'reached':''}"${key===verdict?' aria-current="step"':''}><i></i><span>${['판단 중','의심','유력','확정'][i]}</span></li>`).join('')}</ol>
    <p class="incident-scope">${esc(lead?.scope || '사건 전체 침해 여부')}</p>
    <p class="incident-brief" aria-label="현재까지의 AI 판단">${esc(lead?.summary || (lead?'아래 판단 근거에 현재 확인 범위와 한계가 기록되어 있습니다. 짧은 AI 요약은 아직 작성되지 않았습니다.':'개별 단서의 사실관계를 검토하고 있으며, 사건 전체의 침해 여부는 아직 종합되지 않았습니다. 지지 근거와 반대 설명을 대조한 뒤 현재 판단과 다음 조사 항목을 정리합니다.'))}</p>
    <div class="incident-votes" aria-label="질문별 침해 평가 현황 · 독립 증거나 확률이 아님">${segments.map(([label,n,tone])=>`<div class="${tone}"><strong>${n}</strong><span>${label}</span></div>`).join('')}</div>
    <div class="incident-tally" aria-hidden="true">${total?segments.map(([,n,tone])=>`<i class="${tone}" style="flex:${n}"></i>`).join(''):'<i class="empty"></i>'}</div>
    <div class="incident-foot"><span>침해 가설 ${assessed}건 평가</span><span>단서 ${facts}건 검토</span></div>
    <details class="incident-basis"><summary>판단 근거 ↗</summary><p>${esc(lead?.rationale || '검증된 침해 평가가 도착하면 갱신됩니다. 단서의 확정 등급만으로 침해를 확정하지 않습니다.')}</p><div class="source-pills">${(lead?.supporting_evidence_ids||[]).map((id,i)=>`<button data-ref="${esc(id)}">지지 원문 ${i+1} ↗</button>`).join('')}${(lead?.refuting_evidence_ids||[]).map((id,i)=>`<button data-ref="${esc(id)}">반대 원문 ${i+1} ↗</button>`).join('')}</div><p>질문별 판단 수이며 침해 확률이 아닙니다. 일부 가설의 반박은 전체 정상 판정이 아닙니다.</p></details>`;
}
function renderIncidentStatus(s,stale=false) {
  const root=document.getElementById('incident-status');if(!root)return;
  const html=incidentMarkup(s,stale);if(root.dataset.markup===html)return;
  const open=root.querySelector('details')?.open;
  root.dataset.markup=html;root.innerHTML=html;
  if(open)root.querySelector('details').open=true;
}
