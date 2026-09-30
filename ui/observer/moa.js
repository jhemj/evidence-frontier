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
    linux_scan:['searching','Linux 로그·설정·파일에서 조사할 단서를 찾고 있어요.', '접속, 실행, 자동 실행, 통신에 관한 기록을 확보하기 위해서예요.', '기록을 찾은 뒤 정상 작업인지 의심 활동인지 대조해야 해요. 수집만으로 침해를 확정하지 않아요.'],
    windows_scan:['searching','Windows 이벤트와 설정·파일에서 조사할 단서를 찾고 있어요.', '접속·실행·자동 실행에 관한 기록을 확보하기 위해서예요.', '발견된 기록을 정상 작업과 대조한 뒤 의미를 판단해야 해요.'],
    linux_investigate:['thinking','모인 단서를 대조하며 추가로 확인할 내용을 살펴보고 있어요.', '정상 작업과 의심 활동을 구별할 수 있는 근거를 찾기 위해서예요.', '검사 계획과 실제 실행은 달라요. 필요한 자료가 없으면 공백으로 남겨요.'],
    windows_investigate:['thinking','모인 단서를 대조하며 추가로 확인할 내용을 살펴보고 있어요.', '서로 다른 설명을 구별할 근거를 찾기 위해서예요.', '검사 결과가 무엇을 뒷받침하는지는 따로 평가해야 해요.'],
    ai_judgment:['thinking','단서가 무엇을 뜻하는지 정상·의심 설명을 대조하고 있어요.', '기록의 존재와 실제 성공·승인 여부를 구분하기 위해서예요.', 'AI 응답은 검증을 거쳐 채택돼야 해요. 작업 완료가 가설 입증은 아니에요.'],
    investigation_report:['organizing','확인한 내용과 남은 의문을 보고서로 정리하고 있어요.', '근거와 불확실성을 함께 전달하기 위해서예요.', '보고서가 생성돼도 조사 공백이 없어지는 것은 아니에요.']
  };
  function compact(value){return typeof value==='string'?value.replace(/[\r\n\t\x00-\x1f]/g,' ').trim().slice(0,90):'';}
  function currentItem(items){
    // A concrete tool/model request is more specific than its long-running parent task.
    return items.find(a=>a.kind==='tool'&&a.state==='running')||
      items.find(a=>a.kind==='tool'&&a.state==='waiting')||
      items.find(a=>a.kind==='model'&&a.state==='running')||
      items.find(a=>a.kind==='model'&&a.state==='input_registered')||
      items.find(a=>a.state==='running');
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
        const subject=target&&target!=='범위는 검사 상세 참조'?'‘'+target+'’':'';
        const action=current.title==='search'?'를 검색하고 있어요.':current.title==='read_file'?' 파일 내용을 읽고 있어요.':'의 기록을 확인하고 있어요.';
        return {base:current.state==='waiting'?'waiting':'searching',
          label:current.state==='waiting'?(subject||'검사')+' 결과를 기다리고 있어요.':subject?subject+action:'기록에서 단서를 찾고 있어요.',
          why:compact(current.purpose)||'연결된 가설을 구별할 기록을 찾기 위해서예요.',
          meaning:'결과를 확보한 뒤 어떤 설명을 뒷받침하는지 대조해요.'};
      }
      if(current.state==='input_registered')return {base:'waiting',label:
        (target&&target!=='구조화 판단'?'‘'+target+'’에 대한':'단서에 대한')+' AI 검토를 기다리고 있어요.'};
      const stage=stages[current.title];
      if(stage)return {base:stage[0],label:stage[1],why:stage[2],meaning:stage[3]};
      if(current.purpose)return {base:current.kind==='model'?'thinking':'searching',label:current.purpose,
        why:'원장에 기록된 이번 작업 목적이에요. 어떤 가설과 연결되는지는 아래 설명에서 확인할 수 있어요.',
        meaning:'결과의 지지·반박 여부는 검사 이후의 판단을 확인해야 해요.'};
      if(current.kind==='model')return {base:'thinking',label:'근거를 대조하고 있어요'};
      if(current.kind==='report'||current.title==='investigation_report')return {base:'organizing',label:'확인한 내용을 정리하고 있어요'};
      if(current.kind==='tool'||current.kind==='collection'||['integrity','inventory','normalize','timeline','linux_scan','windows_scan'].includes(current.title))
        return {base:'searching',label:'기록에서 단서를 찾고 있어요'};
      return {base:'working',label:'조사를 진행하고 있어요'};
    }
    if(items.some(a=>['waiting','input_registered'].includes(a.state)))
      return {base:'waiting',label:'다음 응답을 기다려요 · 전송 여부는 아직 미확인'};
    if(items.some(a=>a.state==='failed'))return {base:'concerned',label:'실패한 작업의 영향 확인이 필요해요'};
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
      known&&item.timer_origin==='input_registered'?'입력 준비 후 '+seconds+'초 · ':'확인 후 '+seconds+'초 · ';
    const basis=known&&item.timer_origin==='execution'?'원장에 기록된 실행 시작 기준':
      known&&item.timer_origin==='dispatch'?'검색·검사 요청 전송 기준 · 실제 실행 시간과 다를 수 있음':
      known&&item.timer_origin==='input_registered'?'입력 등록 기준 · 모델 실행 시간이나 전송 확인을 뜻하지 않음':
      fallback?'이 화면에서 현재 작업을 확인한 뒤 경과한 시간':'작업 시작 시각 미제공';
    return {...shown,text:prefix+shown.label,timer_basis:basis,id:item.id};
  }
  // User-selected appearance examples only. They never change a case or verdict.
  const previews=Object.freeze({
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
  if(typeof module!=='undefined')module.exports={judgment,activity,elapsedActivity,currentItem,previews};else root.ObserverMoa={judgment,activity,elapsedActivity,currentItem,previews};
})(typeof window!=='undefined'?window:globalThis);
