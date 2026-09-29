"use strict";
let scenarioLimit=3,scenarioCase=null,scenarioSnapshot=null;
const scenarioStates={investigating:'검증 중',strengthened:'근거 강화',weakened:'근거 약화',supported:'입증 · 해당 범위',refuted:'반증됨',inconclusive:'판단 보류'};
function scenarioCard(c){
  const state=Object.hasOwn(scenarioStates,c.lifecycle)?c.lifecycle:'investigating';
  const rank=Number.isInteger(c.rank)&&c.rank>0?`${c.tied?'공동 ':''}${c.rank}위`:'순위 보류';
  const priority={high:'우선 검증',normal:'일반 검증',low:'후순위 검증'}[c.investigation_priority] || '일반 검증';
  const movement=(c.rank_history || []).at(-1);
  const changed=movement&&movement.previous_rank&&movement.rank&&movement.previous_rank!==movement.rank&&movement.previous_question===movement.comparison_question;
  const refs=(ids,label)=>(ids||[]).map((id,i)=>`<button data-ref="${esc(id)}">${label} ${i+1} ↗</button>`).join('');
  return `<article class="scenario-card ${state}" data-scenario-id="${esc(c.id)}">
    <div class="scenario-meta"><span class="scenario-rank">${rank}</span><span class="lifecycle-badge ${state}">${scenarioStates[state]}</span></div>
    <p class="scenario-question">${esc(c.comparison_question || '비교 질문 평가 대기')}</p>
    <h3>${esc(c.title || '검증 중인 시나리오')}</h3><p class="scenario-brief">${esc(c.summary || '원문 대조 중입니다.')}</p>
    ${changed&&c.assessment_current?`<p class="scenario-movement">${movement.previous_rank}위 → ${movement.rank}위 · ${esc(movement.reason)}</p>`:''}
    <details data-scenario-detail="${esc(c.id)}"><summary>근거 · 다음 확인 <span class="scenario-priority">${priority}</span></summary>
      <dl><dt>증거 설명력</dt><dd>${esc(c.ranking_reason || '비교 평가 대기')}</dd>
      <dt>지지 근거</dt><dd class="source-pills">${refs(c.supporting_evidence_ids,'지지')||'직접 근거 미확인'}</dd>
      <dt>반대 근거 · 대안</dt><dd>${esc(c.alternative_explanation || '추가 대조가 필요합니다.')}<div class="source-pills">${refs(c.refuting_evidence_ids,'반대')}</div></dd>
      <dt>다음 확인</dt><dd>${esc(c.next_check || '판별 검사 계획 중')}</dd>
      <dt>${priority} 이유</dt><dd>${esc(c.priority_reason || '조사 우선순위 평가 대기')}</dd></dl>
      ${c.change_reason?`<p class="scenario-movement">최근 판단 변경 · ${esc(c.change_reason)}</p>`:''}
      <button class="ask-clue" data-question="${esc('이 시나리오의 반대 근거와 다음 판별 검사를 설명해줘: '+c.title+' (가설 ID: '+c.id+')')}">이 시나리오 질문하기 →</button>
    </details></article>`;
}
function scenarioCards(s,limit=3,filter='all'){
  const cards=s.scenarios?.cards || [];
  return cards.filter(c=>filter==='all'||filter==='active'&&!['refuted','inconclusive'].includes(c.lifecycle)||filter===c.lifecycle).slice(0,limit);
}
function renderScenarios(s){
  if(scenarioCase!==s.case.id){scenarioCase=s.case.id;scenarioLimit=3;$('scenario-dialog').close();}
  scenarioSnapshot=s;
  const cards=scenarioCards(s,scenarioLimit),signature=JSON.stringify(cards);
  const target=$('active-hypotheses');
  if(target.dataset.signature!==signature){
    const expanded=new Set([...target.querySelectorAll('details[open]')].map(x=>x.dataset.scenarioDetail));
    target.innerHTML=cards.map(scenarioCard).join('')||'<p class="scenario-empty">원문에 근거한 시나리오가 만들어지면 표시됩니다.</p>';
    target.querySelectorAll('details').forEach(x=>{x.open=expanded.has(x.dataset.scenarioDetail);});
    target.dataset.signature=signature;
  }
  const total=s.scenarios?.cards.length || 0;
  $('hypothesis-count').textContent=`${cards.length} / ${total}`;
  $('scenario-more').hidden=total<=3;
  $('scenario-more').textContent=scenarioLimit===3?'5개까지 보기':'3개로 접기';
  $('scenario-all').hidden=!total;
  if($('scenario-dialog').open)renderAllScenarios();
}
function renderAllScenarios(){
  const cards=scenarioCards(scenarioSnapshot,Infinity,$('scenario-filter').value),target=$('scenario-all-list');
  const signature=JSON.stringify(cards);
  if(target.dataset.signature===signature)return;
  const expanded=new Set([...target.querySelectorAll('details[open]')].map(x=>x.dataset.scenarioDetail));
  target.innerHTML=cards.map(scenarioCard).join('')||'<p class="scenario-empty">해당 상태의 시나리오가 없습니다.</p>';
  target.querySelectorAll('details').forEach(x=>{x.open=expanded.has(x.dataset.scenarioDetail);});
  target.dataset.signature=signature;
}
function initializeScenarios(){
  $('scenario-more').onclick=()=>{scenarioLimit=scenarioLimit===3?5:3;renderScenarios(scenarioSnapshot);};
  $('scenario-all').onclick=()=>{renderAllScenarios();$('scenario-dialog').showModal();};
  $('scenario-filter').onchange=renderAllScenarios;
  $('scenario-close').onclick=()=>$('scenario-dialog').close();
}
