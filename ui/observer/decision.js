(function(root){
  'use strict';
  // Optional D1 presentation component. Not connected to the live observer;
  // no network, model requests, scheduler action, evidence removal or verdict.
  function renderOpinion(view, doc){
    const element=(tag,text)=>{const n=doc.createElement(tag);if(text!==undefined)n.textContent=String(text);return n;};
    const panel=element('section');panel.className='decision-advisory';
    panel.append(element('h3',view.title),element('p',view.basis));
    if(view.provenance==='fixture')panel.append(element('strong','시험용 예시 · 실제 모델 판단 아님'));
    const available=['ok','abstained'].includes(view.status)&&view.distribution_available===true&&
      !(view.data_mode==='live'&&view.provenance==='fixture');
    if(!available){
      panel.append(element('p',view.status==='stale'?'바뀐 근거로 다시 확인해야 해요':'선택지 점수는 아직 제공되지 않았어요'));
      panel.append(element('p',view.reason));
    }
    const list=element('ul');
    for(const c of Array.isArray(view.candidates)?view.candidates:[]){
      const row=element('li');row.append(element('span',c.label));
      const valid=available&&Number.isFinite(c.probability)&&c.probability>=0&&c.probability<=1;
      if(valid){
        const meter=element('meter');meter.min=0;meter.max=1;meter.value=c.probability;
        meter.setAttribute('aria-label',c.label+' · 조건부 선택 분포');
        row.append(meter,element('span',(c.probability*100).toFixed(1)+'%'));
      }else row.append(element('span','점수 미제공'));
      row.append(element('p',c.description));list.append(row);
    }
    panel.append(list,element('p','이 의견만으로 단서를 버리거나 필수 검토를 생략하지 않아요.'));
    return panel;
  }
  if(typeof module!=='undefined')module.exports={renderOpinion};else root.ObserverDecision={renderOpinion};
})(typeof window!=='undefined'?window:globalThis);
