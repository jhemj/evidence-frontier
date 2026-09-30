(function(root){
  'use strict';
  const labels={undetermined:'침해 여부는 아직 판단 전',suspected:'의심 정황을 확인 중',
    probable:'해당 범위에서 침해 유력',confirmed:'해당 범위에서 침해 확인',stale:'판단을 다시 확인 중'};
  function judgment(view,{available=true,restricted=()=>false}={}){
    if(!available)return {level:'stale',label:labels.stale,scope:'연결·정정 확인 뒤 현재 판단을 표시해요.'};
    const status=view?.summary?.intrusion,ref=status?.leading_ref,obj=ref&&view.objects[ref.key];
    if(!ref)return {level:'undetermined',label:labels.undetermined,scope:''};
    if(!obj||obj.version!==ref.version||obj.type!=='incident'||obj.validity!=='adopted'||
       !obj.assessment_current||restricted(obj)||!obj.refs.every(r=>view.objects[r.key]?.version===r.version&&!restricted(view.objects[r.key])))
      return {level:'stale',label:labels.stale,scope:'근거가 바뀌어 이전 판단을 그대로 보여드릴 수 없어요.'};
    if(!['suspected','probable','confirmed'].includes(obj.verdict))
      return {level:'undetermined',label:labels.undetermined,scope:'전체 사건의 정상 여부는 아직 알 수 없어요.'};
    return {level:obj.verdict,label:labels[obj.verdict],scope:obj.scope,key:obj.key};
  }
  const stages={
    integrity:['searching','원본 이미지의 해시를 확인하고 있어요.', '분석할 원본이 바뀌지 않았는지 확인하기 위해서예요.', '해시 확인은 자료의 무결성 점검이지 침해 여부 판단은 아니에요.'],
    inventory:['searching','이미지의 파티션과 파일시스템을 살펴보고 있어요.', '어떤 영역과 기록을 조사할 수 있는지 파악하기 위해서예요.', '읽을 수 있는 영역과 아직 살펴보지 못한 영역을 구분하게 돼요.'],
    normalize:['organizing','파일과 로그 기록을 읽기 쉬운 형태로 정리하고 있어요.', '서로 다른 기록을 대조할 준비를 하기 위해서예요.', '기록이 정리돼도 그 내용이 침해를 뜻하는지는 따로 확인해야 해요.'],
    timeline:['organizing','기록에 남은 시각을 정리하고 있어요.', '관련 기록의 순서와 시각의 한계를 함께 살펴보기 위해서예요.', '파일 시각은 실제 행동 시각과 다르고, 시간대가 없으면 순서를 확정할 수 없어요.'],
    crosscheck:['searching','이미지의 저장 구조를 다른 방식으로 대조하고 있어요.', '자료를 읽은 방식에 모순이 없는지 확인하기 위해서예요.', '지원하지 않는 구조가 있으면 미확인 범위로 남겨요.'],
    linux_scan:['gathering','Linux 로그·설정·파일에서 조사할 단서를 찾고 있어요.', '접속, 실행, 자동 실행, 통신에 관한 기록을 확보하기 위해서예요.', '기록을 찾은 뒤 정상 작업인지 의심 활동인지 대조해야 해요. 수집만으로 침해를 확정하지 않아요.'],
    windows_scan:['gathering','Windows 이벤트와 설정·파일에서 조사할 단서를 찾고 있어요.', '접속·실행·자동 실행에 관한 기록을 확보하기 위해서예요.', '발견된 기록을 정상 작업과 대조한 뒤 의미를 판단해야 해요.'],
    linux_investigate:['thinking','모인 단서를 대조하며 추가로 확인할 내용을 살펴보고 있어요.', '정상 작업과 의심 활동을 구별할 수 있는 근거를 찾기 위해서예요.', '검사 계획과 실제 실행은 달라요. 필요한 자료가 없으면 공백으로 남겨요.'],
    windows_investigate:['thinking','모인 단서를 대조하며 추가로 확인할 내용을 살펴보고 있어요.', '서로 다른 설명을 구별할 근거를 찾기 위해서예요.', '검사 결과가 무엇을 뒷받침하는지는 따로 평가해야 해요.'],
    ai_judgment:['thinking','단서가 무엇을 뜻하는지 정상·의심 설명을 대조하고 있어요.', '기록의 존재와 실제 성공·승인 여부를 구분하기 위해서예요.', 'AI 응답은 검증을 거쳐 채택돼야 해요. 작업 완료가 가설 입증은 아니에요.'],
    investigation_report:['organizing','확인한 내용과 남은 의문을 보고서로 정리하고 있어요.', '근거와 불확실성을 함께 전달하기 위해서예요.', '보고서가 생성돼도 조사 공백이 없어지는 것은 아니에요.']
  };
  function compact(value){return typeof value==='string'?value.replace(/[\r\n\t\x00-\x1f]/g,' ').trim().slice(0,90):'';}
  // The saved request purpose is explanation, not a fresh model inference.
  // Preserve its qualifications; the short target-label limit must not clip it.
  function requestPurpose(item){return typeof item?.purpose==='string'?
    item.purpose.replace(/[\r\n\t\x00-\x1f]/g,' ').trim().slice(0,1000)||null:null;}
  function reviewSubjects(item){
    return (item?.context?.subjects||[]).map(s=>({id:s.id,title:s.title,
      examples:s.examples||[],presented:s.presented_records,omitted:s.omitted_examples,
      label:[...new Set((s.examples||[]).map(e=>e.label))].join(' / ')||s.title||'검토 내용 미제공',
      paths:[...new Set((s.examples||[]).map(e=>e.path).filter(Boolean))]}));
  }
  function workTarget(item){
    const subjects=reviewSubjects(item);
    if(subjects.length)return compact(subjects[0].label)+(subjects.length>1?' 외 '+(subjects.length-1)+'개 단서':'');
    return compact(item?.target)||'검토 대상 미제공';
  }
  function failure(item){
    if(!item||item.state!=='failed')return null;
    const affected=item.failure?.subject_ids||[],subjects=reviewSubjects(item).filter(s=>affected.includes(s.id));
    const target=subjects.length?compact(subjects[0].label)+(subjects.length>1?' 외 '+(subjects.length-1)+'개 단서':''):workTarget(item);
    return {label:'‘'+target+'’ · '+(item.failure?.title||'작업 실패'),
      reason:item.failure?.reason||'상세 실패 원인은 작업 기록에서 확인할 수 있어요.',
      impact:item.failure?.impact||'이 작업이 어느 판단에 영향을 주는지는 아직 연결되지 않았어요.'};
  }
  function currentItem(items){
    // A concrete tool/model request is more specific than its long-running parent task.
    return items.find(a=>a.kind==='tool'&&a.state==='running')||
      items.find(a=>a.kind==='model'&&a.state==='running')||
      items.find(a=>a.kind==='model'&&['validating','adopting'].includes(a.state))||
      items.find(a=>a.kind==='model'&&a.state==='waiting'&&['dispatch_attempted','response_waiting','delivery_unknown'].includes(a.phase))||
      items.find(a=>a.kind==='model'&&a.state==='waiting'&&a.timer_origin==='dispatch')||
      items.find(a=>a.kind==='tool'&&a.state==='waiting')||
      items.find(a=>a.kind==='model'&&a.state==='waiting')||
      items.find(a=>a.kind==='model'&&a.state==='input_registered')||
      items.find(a=>a.kind==='task'&&a.state==='waiting')||
      items.find(a=>a.state==='running');
  }
  function recentFailure(items){
    return items.filter(a=>a.state==='failed'&&a.failure&&a.failure_impact!=='resolved')
      .sort((a,b)=>(Date.parse(b.at)||0)-(Date.parse(a.at)||0))[0]||null;
  }
  function completedItem(items,{excludeId=null}={}){
    return items.filter(a=>a.id!==excludeId&&['tool','model','task'].includes(a.kind)&&
      (['succeeded','covered','covered_zero','partial','failed','interrupted','cancelled','unsupported'].includes(a.state)||
       a.state==='received'&&a.kind==='model'&&!a.phase)&&Number.isFinite(Date.parse(a.at)))
      .sort((a,b)=>Date.parse(b.at)-Date.parse(a.at)||a.id.localeCompare(b.id))[0]||null;
  }
  class ResultReaction{
    constructor(){this.scope=null;this.initialized=false;this.token=null;this.until=0;}
    update(item,{scope='',available=true,now=Date.now()}={}){
      if(this.scope!==scope||!available){this.scope=scope;this.initialized=false;this.token=null;this.until=0;}
      const token=item?item.id+' / '+item.state+' / '+(item.failure_impact||''):null;
      const failed=available&&item?.state==='failed'&&item.failure_impact!=='resolved';
      if(token!==this.token||!this.initialized){
        this.until=this.initialized&&failed?now+8000:0;
        this.token=token;this.initialized=available;
      }
      // Historical unresolved warnings do not override the ongoing activity.
      // A newer success/recovery immediately clears a short disappointment.
      return {transient:failed&&now<this.until?'disappointed':'none',severity:failed?'warning':'info'};
    }
  }
  function workResult(item){
    if(!item)return {label:'아직 이전 결과가 없어요',detail:'첫 조사 결과가 도착하면 여기에 보여드려요.'};
    if(!['failed','cancelled','interrupted','received','succeeded','covered','covered_zero','partial','unsupported'].includes(item.state))
      return {label:'아직 완료 결과가 없어요',detail:'당시 요청 상태를 보여드려요. 이 작업의 완료 결과는 확인되지 않았어요.'};
    if(item.state==='unsupported')return {label:'이 방식으로는 확인하지 못했어요',detail:'해당 검사는 지원되지 않는다고 기록됐어요. 다른 자료나 확인 방법이 필요해요.'};
    if(item.state==='failed'){
      const f=failure(item);
      return {label:item.failure_impact==='resolved'?'이 시도는 실패했지만 재시도로 회복됐어요':'이 시도에서는 확인하지 못했어요',detail:f.reason,limitation:f.impact};
    }
    if(item.state==='cancelled'||item.state==='interrupted')return {label:'이 작업은 중단됐어요',detail:'완료된 조사 결과로 대신하지 않아요.'};
    if(item.kind==='model'){
      if(item.state==='received')return {label:'검토 답변을 받았어요',detail:'답변의 검증·채택 여부는 아직 확인되지 않았어요.'};
      return {label:item.state==='partial'?'검토 내용 일부를 반영했어요':'검토 결과를 반영했어요',
        detail:item.result_adopted===true?'해당 검토 범위의 결과가 채택됐어요. 사건 전체가 입증됐다는 뜻은 아니에요.':'검토 작업은 끝났어요. 채택된 판단 범위는 작업 상세에서 확인해 주세요.'};
    }
    const scope=item.result_summary;
    if(scope?.basis==='retained_tool_result_scope'){
      const limitations=[];
      if(scope.complete===false||scope.truncated===true||scope.has_more===true||scope.remaining_matches_unknown===true)
        limitations.push('확인한 범위의 일부 결과예요. 아직 확인하지 못한 내용이 남아 있을 수 있어요.');
      if(item.title==='search'&&scope.matches_scope==='this_search_page'&&Number.isSafeInteger(scope.matches)&&scope.matches>=0)
        return {label:scope.matches?'검색 조건과 맞는 기록을 찾았어요':'이번 검색에서는 일치한 기록을 찾지 못했어요',
          detail:'이번 검색 페이지에서 '+scope.matches+'건이 일치했어요.',
          limitation:[...limitations,'검색 결과만으로 실행 성공이나 기록의 부재를 확정하지 않아요.'].join(' ')};
      if(Number.isSafeInteger(scope.recorded_reference_count)&&scope.recorded_reference_count>0)
        return {label:item.state==='partial'?'자료의 일부를 읽었어요':'자료 확인 결과를 받았어요',
          detail:'이 작업에서 단서 '+scope.recorded_reference_count+'개를 기록했어요.',
          limitation:[...limitations,'단서 수가 독립 증거나 실제 발생 횟수를 뜻하지는 않아요.'].join(' ')};
      if(scope.metadata_available===true)return {label:item.state==='partial'?'일부 결과를 받았어요':'자료 확인 결과를 받았어요',
        detail:'이 작업의 결과 상태가 기록됐어요. 구체적인 내용은 작업 상세에서 확인해 주세요.',limitation:limitations.join(' ')};
    }
    return {label:item.state==='partial'?'일부 결과를 확보했어요':'자료 확인을 마쳤어요',
      detail:'이 작업의 구체적인 결과 요약은 아직 제공되지 않았어요. 완료만으로 가설이 입증되지는 않아요.'};
  }
  function activity(view,items,{available=true}={}){
    if(!available)return {base:'offline',label:'새 상태를 받지 못했어요'};
    const c=view?.case||{};
    if(c.execution_end||['complete','completed','quiescent','resource_limit','failed','paused'].includes(c.status))
      return {base:c.status==='failed'?'concerned':'ended',label:'조사가 멈춰 있어요'};
    const current=currentItem(items);
    if(current){
      const target=compact(current.target);
      if(current.kind==='tool'){
        const purpose=requestPurpose(current);
        const subject=target&&target!=='범위는 검사 상세 참조'?'‘'+target+'’':'';
        const action=current.title==='search'?'를 검색하고 있어요.':current.title==='read_file'?' 파일 내용을 읽고 있어요.':'의 기록을 확인하고 있어요.';
        const executionKnown=current.timer_origin==='execution';
        return {base:current.state==='waiting'||!executionKnown?'waiting':'searching',
          label:current.state==='waiting'||!executionKnown?(subject||'검사')+' 확인 요청의 결과를 확인하고 있어요.':subject?subject+action:'기록에서 단서를 찾고 있어요.',
          purpose,purpose_ref:purpose?current.id:null,
          statusNote:!executionKnown?'요청 뒤 경과한 시간이에요. 작업자의 현재 실행 상태와 완료 결과는 아직 확인되지 않았어요.':null,
          why:purpose||'이 작업의 구체적인 검사 목적은 아직 기록되지 않았어요.',
          meaning:'결과를 확보한 뒤 어떤 설명을 뒷받침하는지 대조해요.'};
      }
      if(current.kind==='model'){
        const subjects=reviewSubjects(current),subject=subjects.length?'‘'+workTarget(current)+'’':
          target&&target!=='구조화 판단'?'‘'+target+'’':'단서';
        const descriptions={resource_queued:'의 검토 순서를 기다리고 있어요.',availability_check:'를 검토할 AI 연결을 확인하고 있어요.',
          validating:'의 답변이 원문·검토 범위와 맞는지 확인하고 있어요.',validated_output:'의 검증된 결과를 반영하고 있어요.',
          delivery_unknown:'의 검토 요청 상태를 확인하고 있어요.'};
        return {base:current.state==='input_registered'||current.phase==='resource_queued'?'waiting':'thinking',
          label:subject+(descriptions[current.phase]||(current.state==='input_registered'?'의 검토 입력이 준비됐어요.':'를 검토하고 있어요.')),
          subjects,
          why:'이 기록이 어떤 설명을 뒷받침하는지, 다른 설명과 모순되는 부분은 없는지 대조하는 검토예요.',
          statusNote:current.state==='input_registered'?'검토 입력 준비 상태예요. 아직 요청 전송은 확인되지 않았어요.':
            current.state==='waiting'&&current.timer_origin==='dispatch'?'검토 요청은 시작됐어요. AI가 답변을 생성하는 세부 상태는 제공되지 않아요.':null};
      }
      const stage=stages[current.title];
      if(current.kind==='task'&&current.phase==='worker_status_unobserved')
        return {base:['linux_scan','windows_scan'].includes(current.title)?'gathering_waiting':'waiting',label:'‘'+(compact(current.target)||'자료 확인')+'’ 요청의 결과를 확인하고 있어요.',
          why:stage?.[2]||'요청한 자료 확인이 끝났는지 확인하기 위해서예요.',
          meaning:stage?.[3]||'결과를 확보한 뒤 어떤 설명을 뒷받침하는지 대조해요.',
          statusNote:'작업자 요청이 남아 있어요. 전송 성공과 실제 실행 상태는 이 기록만으로 확인되지 않아요.'};
      if(stage)return {base:stage[0],label:stage[1],why:stage[2],meaning:stage[3]};
      if(current.purpose)return {base:current.kind==='model'?'thinking':'searching',label:current.purpose,
        why:'원장에 기록된 이번 작업 목적이에요. 어떤 가설과 연결되는지는 아래 설명에서 확인할 수 있어요.',
        meaning:'결과의 지지·반박 여부는 검사 이후의 판단을 확인해야 해요.'};
      if(current.kind==='report'||current.title==='investigation_report')return {base:'organizing',label:'확인한 내용을 정리하고 있어요'};
      if(current.kind==='tool'||current.kind==='collection'||['integrity','inventory','normalize','timeline','linux_scan','windows_scan'].includes(current.title))
        return {base:'searching',label:'기록에서 단서를 찾고 있어요'};
      return {base:'working',label:'조사를 진행하고 있어요'};
    }
    if(items.some(a=>['waiting','input_registered'].includes(a.state)))
      return {base:'waiting',label:'다음 응답을 기다려요 · 전송 여부는 아직 미확인'};
    const failed=items.find(a=>a.state==='failed'&&['unresolved','partial'].includes(a.failure_impact));
    if(failed){const f=failure(failed);return {base:'idle',label:'지금 실행 중인 작업은 없어요',why:f.reason,meaning:f.impact};}
    return {base:'idle',label:'지금 실행 중인 작업은 없어요'};
  }
  function elapsedActivity(view,items,{available=true,now=Date.now(),observedSince=null}={}){
    const shown=activity(view,items,{available}),item=currentItem(items);
    if(!available||['ended','offline','concerned','idle'].includes(shown.base)||!item)
      return {...shown,text:shown.label,timer_basis:null,id:null};
    const at=item.timer_at?Date.parse(item.timer_at):NaN;
    const known=Number.isFinite(at),fallback=Number.isFinite(observedSince);
    const seconds=known?Math.max(0,Math.floor((now-at)/1000)):fallback?Math.max(0,Math.floor((now-observedSince)/1000)):null;
    const prefix=seconds===null?'':known&&item.timer_origin==='execution'?seconds+'초째 ':
      known&&item.timer_origin==='dispatch'?'요청 후 '+seconds+'초 · ':
      known&&item.timer_origin==='stage'?'현재 단계 '+seconds+'초 · ':
      known&&item.timer_origin==='input_registered'?'입력 준비 후 '+seconds+'초 · ':'확인 후 '+seconds+'초 · ';
    const basis=known&&item.timer_origin==='execution'?'원장에 기록된 실행 시작 기준':
      known&&item.timer_origin==='dispatch'?'검색·검사 요청 전송 기준 · 실제 실행 시간과 다를 수 있음':
      known&&item.timer_origin==='stage'?'원장에 기록된 현재 검증·채택 단계 시작 기준':
      known&&item.timer_origin==='input_registered'?'입력 등록 기준 · 모델 실행 시간이나 전송 확인을 뜻하지 않음':
      fallback?'이 화면에서 현재 작업을 확인한 뒤 경과한 시간':'작업 시작 시각 미제공';
    return {...shown,text:prefix+shown.label,timer_basis:basis,id:item.id};
  }
  // User-selected appearance examples only. They never change a case or verdict.
  const previews=Object.freeze({
    gathering:{label:'자료 더미에서 단서 찾기',base:'gathering'},
    gathering_waiting:{label:'수집 결과 기다리기',base:'gathering_waiting'},
    searching:{label:'돋보기로 탐색',base:'searching'},
    thinking:{label:'고민 중',base:'thinking'},
    working:{label:'자료 대조',base:'working'},
    organizing:{label:'메모 정리',base:'organizing'},
    waiting:{label:'응답 기다리기',base:'waiting'},
    concerned:{label:'난감함',base:'concerned'},
    updated:{label:'새 내용에 놀람',base:'idle',transient:'updated'},
    counterpoint:{label:'다시 살펴보기',base:'thinking',transient:'counterpoint'},
    corrected:{label:'내용 고치기',base:'organizing',transient:'corrected'},
    happy:{label:'기쁨',base:'idle',transient:'happy'},
    idle:{label:'차분히 쉬기',base:'idle'},
    ended:{label:'조사 멈춤',base:'ended'}
  });
  const api={judgment,activity,elapsedActivity,currentItem,recentFailure,completedItem,ResultReaction,workResult,reviewSubjects,workTarget,failure,requestPurpose,previews};
  if(typeof module!=='undefined')module.exports=api;else root.ObserverMoa=api;
})(typeof window!=='undefined'?window:globalThis);
