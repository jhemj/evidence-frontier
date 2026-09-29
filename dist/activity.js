"use strict";
const workStates={running:'수행 중',waiting:'응답 대기',done:'완료',partial:'범위 제한',failed:'실패',paused:'일시정지',interrupted:'중단됨'};
const workKinds={collection:'자료 수집',tool:'추가 검사',model:'AI 분석',report:'보고서'};
const workHistory={caseId:null,status:'',kind:'',items:[],total:0,offset:0,hasMore:false,revision:null,request:0,loading:false};
function workTime(value) {
  const ms=Date.parse(value);
  return Number.isFinite(ms)?new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(ms):'시각 미기록';
}
function workElapsed(row,now=Date.now(),stale=false) {
  if(!row.started_at)return '시작 시각 미기록';
  const live=['running','waiting'].includes(row.status);
  if(live&&stale)return '연결 확인 중';
  if(!row.ended_at&&!live)return '소요 시간 미기록';
  const end=row.ended_at?Date.parse(row.ended_at):now;
  const seconds=Math.max(0,Math.floor((end-Date.parse(row.started_at))/1000));
  if(!Number.isFinite(seconds))return '소요 시간 미기록';
  const duration=seconds<60?`${seconds}초`:seconds<3600?`${Math.floor(seconds/60)}분 ${seconds%60}초`:`${Math.floor(seconds/3600)}시간 ${Math.floor(seconds%3600/60)}분`;
  return duration+(live?' 경과':'');
}
function workStatus(row,stale=false) {
  return stale&&['running','waiting'].includes(row.status)?'상태 확인 중':(workStates[row.status]||'상태 미확인');
}
function workActivityMarkup(activity,stale=false,now=Date.now()) {
  const rows=(activity?.items||[]).slice(0,5);
  return `<div class="work-heading"><h3>실제 작업 <span>최신 → 이전</span></h3><button type="button" data-work-history>더보기 <span>${activity?.all_total||0}</span> ↗</button></div>
    <ol class="work-strip" aria-label="최근 실제 작업, 왼쪽이 최신">${rows.map((r,i)=>`<li class="work-card ${esc(r.status)} ${stale?'stale':''}" data-work-id="${esc(r.id)}">
      <div class="work-card-top"><span class="work-state"><i aria-hidden="true"></i>${esc(workStatus(r,stale))}</span><span>${esc(workKinds[r.kind]||'작업')}</span></div>
      <h4>${esc(r.title)}</h4><p class="work-target" title="${esc(r.target)}">${esc(r.target||'—')}</p>
      <div class="work-card-time"><time datetime="${esc(r.at)}">${esc(workTime(r.at).split(' ').slice(-1).join(' '))}</time><span class="work-duration">${esc(workElapsed(r,now,stale))}</span></div>
    </li>`).join('')}</ol>${!rows.length?'<p class="work-empty">아직 실행된 작업이 없습니다.</p>':''}`;
}
function workHistoryMarkup(rows,stale=false) {
  return rows.map(r=>`<li class="work-history-row ${esc(r.status)}" data-work-id="${esc(r.id)}">
    <span class="work-history-marker" aria-hidden="true"></span><div class="work-history-body"><div class="work-history-title"><strong>${esc(r.title)}</strong><span class="work-state">${esc(workStatus(r,stale))}</span>${r.archived_scope?'<small>이전 범위</small>':''}</div>
    ${r.target?`<p title="${esc(r.target)}">${esc(r.target)}</p>`:''}<div class="work-history-meta"><span>${esc(workKinds[r.kind]||'작업')}</span><time datetime="${esc(r.at)}">${esc(workTime(r.at))} KST</time><span>${esc(workElapsed(r,Date.now(),stale))}</span></div>
    ${r.error?`<details class="work-error"><summary>오류·제한 내용</summary><p>${esc(r.error)}</p></details>`:''}</div></li>`).join('');
}
function renderWorkHistory() {
  const list=document.getElementById('work-history-list');if(!list)return;
  const stale=typeof progressReceivedAt==='number'&&Date.now()-progressReceivedAt>60000;
  list.innerHTML=workHistoryMarkup(workHistory.items,stale);
  document.getElementById('work-history-count').textContent=`${workHistory.items.length} / ${workHistory.total}개`;
  document.getElementById('work-history-empty').hidden=workHistory.items.length>0||workHistory.loading;
  const more=document.getElementById('work-history-more');more.hidden=!workHistory.hasMore;more.disabled=workHistory.loading;
  document.getElementById('work-history-loading').hidden=!workHistory.loading;
}
async function loadWorkHistory(append=false) {
  const request=++workHistory.request,caseId=workHistory.caseId;
  if(!caseId)return;
  workHistory.loading=true;renderWorkHistory();
  document.getElementById('work-history-error').textContent='';
  const params=new URLSearchParams({status:workHistory.status,kind:workHistory.kind,offset:String(append?workHistory.offset:0),limit:'50'});
  try {
    const data=await api(`/cases/${encodeURIComponent(caseId)}/activity?${params}`);
    if(request!==workHistory.request||caseId!==workHistory.caseId)return;
    workHistory.items=append?[...new Map([...workHistory.items,...data.items].map(r=>[r.id,r])).values()]:data.items;
    workHistory.total=data.total;workHistory.offset=data.offset+data.items.length;workHistory.hasMore=data.has_more;
  } catch(error) {
    if(request===workHistory.request)document.getElementById('work-history-error').textContent='작업 이력을 불러오지 못했습니다. 새로고침으로 다시 확인하세요.';
  } finally {
    if(request===workHistory.request){workHistory.loading=false;renderWorkHistory();}
  }
}
function syncWorkHistory(s) {
  const dialog=document.getElementById('work-history');if(!dialog?.open)return;
  if(workHistory.caseId!==s.case.id){dialog.close();workHistory.caseId=null;++workHistory.request;return;}
  // Preserve filters, loaded pages and scroll while polling. Offer explicit
  // refresh instead of rebuilding/focusing controls beneath the user.
  const button=document.getElementById('work-history-refresh');
  button.textContent=workHistory.revision!==s.view_revision?'새 작업 확인 ↻':'새로고침 ↻';
}
if(typeof document!=='undefined') {
  document.addEventListener('click',event=>{
    if(event.target.closest('[data-work-history]')) {
      const dialog=document.getElementById('work-history');
      Object.assign(workHistory,{caseId:snapshot.case.id,status:'',kind:'',items:[],total:0,offset:0,hasMore:false,revision:snapshot.view_revision});
      document.getElementById('work-status-filter').value='';document.getElementById('work-kind-filter').value='';
      dialog.showModal();loadWorkHistory();
    }
    if(event.target.closest('#work-history-more')&&!workHistory.loading)loadWorkHistory(true);
    if(event.target.closest('#work-history-refresh')) {workHistory.revision=snapshot.view_revision;loadWorkHistory();}
  });
  document.addEventListener('change',event=>{
    if(!['work-status-filter','work-kind-filter'].includes(event.target.id))return;
    workHistory.status=document.getElementById('work-status-filter').value;workHistory.kind=document.getElementById('work-kind-filter').value;
    workHistory.items=[];workHistory.offset=0;loadWorkHistory();
  });
}
