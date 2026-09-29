(function(root) {
  'use strict';
  function check(view) {
    const e = view?.envelope;
    if (!e || e.schema_version !== 'observer-view-1' || !['replay','example','live'].includes(e.data_mode)
        || !e.case_id || !e.run_id || view.case?.id !== e.case_id
        || !Number.isSafeInteger(e.sequence) || e.sequence < 1 || !e.projection_revision
        || !view.objects || !Array.isArray(view.narrative) || !Array.isArray(view.timeline)) throw Error('화면 계약 불일치');
    if(e.data_mode==='live'&&!e.source_binding)throw Error('실시간 원장 결속 미제공');
    const refOK = r => view.objects[r.key]?.version === r.version;
    for (const [key,o] of Object.entries(view.objects)) {
      if (o.key !== key || !o.version || !Array.isArray(o.refs) || !o.refs.every(refOK)) throw Error('객체 참조 버전 불일치');
    }
    if (!view.narrative.every(n => n.refs.every(refOK))) throw Error('설명 참조 버전 불일치');
    if (!view.timeline.every(t => [t.source_ref,...t.claim_refs].every(refOK))) throw Error('시간축 참조 버전 불일치');
    return view;
  }
  const identity = v => [v.envelope.case_id,v.envelope.run_id,v.envelope.data_mode,v.envelope.source_binding||''].join('|');
  class ViewState {
    constructor() {
      this.current=null; this.pinned=null; this.selection=null; this.changes=[];
      this.omittedChanges=0;
      this.invalidated=new Map(); this.seenEvents=new Set(); this.sync='empty'; this.eventSequence=0;
    }
    accept(view, {resync=false}={}) {
      check(view);
      const old=this.current, e=view.envelope;
      if (old && identity(old)!==identity(view)) throw Error('다른 사건·차수·자료 모드 혼입 차단');
      if (old && e.sequence<old.envelope.sequence) {this.sync='resync_required';return false;}
      if (e.sequence<this.eventSequence) {this.sync='resync_required';return false;}
      if (old && e.sequence===old.envelope.sequence) {
        if(e.projection_revision!==old.envelope.projection_revision) throw Error('동일 순서의 상충 스냅샷');
        this.sync=this.invalidated.size?'resync_required':'ready';return false;
      }
      if(old && e.sequence>old.envelope.sequence+1 && !resync) {this.sync='resync_required';return false;}
      const changed=[];
      if(old) for(const [key,obj] of Object.entries(old.objects)) {
        const next=view.objects[key];
        if(['claim','hypothesis','question'].includes(obj.type) && next?.version!==obj.version) {
          changed.push({key, old:obj, next:next||null, kind:!next?'removed':next.validity==='invalidated'?'invalidated':'revised',
                        at:e.captured_at, snapshot:e.projection_revision});
        }
      }
      this.current=view;
      // No celebration on first connection/replay; only prior visible judgments have corrections.
      this.changes.push(...changed);
      if(this.changes.length>200){this.omittedChanges+=this.changes.length-200;this.changes.splice(0,this.changes.length-200);}
      for(const [key,invalid] of this.invalidated) {
        if(e.sequence>=invalid.sequence && view.objects[key]?.version!==invalid.version) this.invalidated.delete(key);
      }
      this.sync=this.invalidated.size?'resync_required':'ready';
      return true;
    }
    beginUpdate(envelope) {
      if(!this.current)return;
      if(identity(this.current)!==identity({envelope}))throw Error('다른 사건·차수·자료 모드 혼입 차단');
      if(envelope.sequence<this.current.envelope.sequence)throw Error('이전 순서 수신 · 재동기화 필요');
      if(envelope.projection_revision!==this.current.envelope.projection_revision)this.sync='resync_required';
    }
    acceptActivity(activity) {
      if(!this.current || identity({envelope:activity})!==identity(this.current))throw Error('작업 관측 범위 불일치');
      if(this.liveActivity && activity.checked_at<this.liveActivity.checked_at)throw Error('이전 작업 관측 수신');
      this.liveActivity=activity;
    }
    invalidate(event) {
      if(!this.current || event.case_id!==this.current.envelope.case_id || event.run_id!==this.current.envelope.run_id
          || event.data_mode!==this.current.envelope.data_mode || !event.id
          || !Number.isSafeInteger(event.sequence) || event.sequence<1 || !Array.isArray(event.refs)
          || !event.refs.every(r=>r && typeof r.key==='string' && typeof r.version==='string' && r.key && r.version)) throw Error('정정 이벤트 범위 불일치');
      if(this.seenEvents.has(event.id)) return false;
      this.seenEvents.add(event.id);
      if(event.sequence<this.current.envelope.sequence) return false;
      for(const ref of event.refs) this.invalidated.set(ref.key,{version:ref.version,sequence:event.sequence});
      this.eventSequence=Math.max(this.eventSequence,event.sequence);
      this.sync='resync_required';return true;
    }
    pin(value) {this.pinned=value?this.current:null;}
    get displayed() {return this.pinned||this.current;}
    select(key) {
      const object=this.displayed?.objects[key];
      if(object) this.selection={key,version:object.version};
    }
    isRestricted(object) {
      if(!object) return true;
      return this.invalidated.has(object.key) || object.refs.some(r=>this.invalidated.has(r.key));
    }
    narratives() {
      if(this.sync!=='ready') return [];
      return (this.displayed?.narrative||[]).filter(n=>n.refs.every(r=>!this.isRestricted(this.displayed.objects[r.key])));
    }
    pinnedChanged() {
      return !!this.pinned && this.pinned.envelope.projection_revision!==this.current.envelope.projection_revision;
    }
  }
  const api={ViewState,check,identity};
  if(typeof module!=='undefined') module.exports=api;
  else root.ObserverState=api;
})(typeof window!=='undefined'?window:globalThis);
