"use strict";
let reportLoadedRevision=null, reportLoading=false, reportNextRefreshAt=0;
let reportRequest=null, reportRequestSequence=0, reportDisplayedKey=null;
function resetReportPreview() {
  ++reportRequestSequence;reportRequest?.abort.abort();reportRequest=null;reportDisplayedKey=null;
  reportLoading=false;reportLoadedRevision=null;reportNextRefreshAt=0;
  $('report-preview').removeAttribute('srcdoc');$('report-preview').hidden=true;
  $('report-load-state').hidden=true;$('report-snapshot-status').textContent='';
}

// A changing activity revision must not regenerate a large report every poll.
// Keep the last good document while refreshing; never label it as the new one.
function loadReport() {
  if(reportCase!==current){reportCase=current;selectedReport=null;}
  const caseId=current, recordId=selectedReport, reader=reportReader;
  const key=JSON.stringify([caseId,recordId,reader]);
  if(reportRequest?.key===key)return reportRequest.promise;
  reportRequest?.abort.abort();
  const sequence=++reportRequestSequence, abort=new AbortController();
  const revision=snapshot?.report_scope_revision ?? snapshot?.view_revision;
  const frame=$('report-preview'), state=$('report-load-state');
  const stillCurrent=()=>sequence===reportRequestSequence&&caseId===current&&recordId===selectedReport&&reader===reportReader;
  if(reportDisplayedKey!==key){frame.removeAttribute('srcdoc');frame.hidden=true;$('report-snapshot-status').textContent='';}
  reportLoading=true;state.hidden=false;state.classList.remove('failed');
  $('report-load-message').textContent=reportDisplayedKey===key?'최신 내용 확인 중 · 아래는 이전 초안입니다.':'보고서를 불러오고 있습니다. 근거가 많으면 잠시 걸릴 수 있습니다.';
  $('report-retry').hidden=true;frame.setAttribute('aria-busy','true');
  $('reader-executive').setAttribute('aria-pressed',reader==='executive');
  $('reader-analyst').setAttribute('aria-pressed',reader==='analyst');
  const timeout=setTimeout(()=>abort.abort(),60000);
  const promise=(async()=>{
    try {
      const response=await api(recordId?`/reports/${recordId}/files/${reader}.html`:`/cases/${caseId}/report-preview?reader=${reader}`,'GET',undefined,true,abort.signal);
      const html=await response.text();
      if(!stillCurrent())return;
      // Preserve the reader's scroll position across background refreshes.
      const scroll=reportDisplayedKey===key?frame.contentWindow?.scrollY||0:0;
      frame.onload=()=>{if(stillCurrent())try{frame.contentWindow?.scrollTo(0,scroll);}catch{/* sandbox may forbid access */}};
      frame.srcdoc=html;frame.hidden=false;reportDisplayedKey=key;state.hidden=true;
      reportLoadedRevision=revision;
      $('report-snapshot-status').textContent=recordId?`저장본${response.headers.get('X-Report-Scope-Changed')==='true'?' · 더 최신 조사 결과 있음':''}`:`진행 중 초안 · ${new Date().toLocaleTimeString('ko-KR',{timeZone:'Asia/Seoul',hour12:false})} KST 갱신`;
    } catch(error) {
      if(!stillCurrent())return;
      state.hidden=false;state.classList.add('failed');$('report-retry').hidden=false;
      $('report-load-message').textContent=error.name==='AbortError'?'보고서 응답이 지연되고 있습니다. 잠시 후 다시 불러와 주세요.':`보고서를 불러오지 못했습니다: ${error.message}`;
    } finally {
      clearTimeout(timeout);
      if(sequence===reportRequestSequence){reportLoading=false;reportRequest=null;reportNextRefreshAt=Date.now()+60000;frame.setAttribute('aria-busy','false');}
    }
  })();
  reportRequest={key,promise,abort};return promise;
}
