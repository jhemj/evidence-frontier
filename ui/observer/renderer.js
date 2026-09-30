(function(root){
  'use strict';
  // A future bundled/licensed adapter is passed explicitly, never loaded from
  // evidence, model text, a URL or an untrusted manifest. No SDK ships here.
  class RendererHost {
    constructor(onStatic){this.onStatic=onStatic;this.adapter=null;this.visible=true;this.hidden=false;this.motion=false;this.reduced=false;this.input=Object.freeze({base:'idle',transient:'none',severity:'info',motionAllowed:false});this.failed=false;}
    attach(adapter){
      this.detach();
      if(!adapter || !['update','pause','destroy'].every(k=>typeof adapter[k]==='function'))throw Error('Renderer adapter contract');
      this.adapter=adapter;this.failed=false;this.refresh();
    }
    detach(){const old=this.adapter;this.adapter=null;if(old)try{Promise.resolve(old.destroy()).catch(()=>{});}catch(_){} }
    fallback(){this.failed=true;this.detach();this.onStatic({available:false,failed:true});}
    call(method,arg){const adapter=this.adapter;try{Promise.resolve(adapter[method](arg)).catch(()=>{if(this.adapter===adapter)this.fallback();});}catch(_){if(this.adapter===adapter)this.fallback();}}
    update(input){
      // Exactly four display inputs cross the boundary; text/IDs/graphs do not.
      this.input=Object.freeze({base:['working','searching','thinking','organizing','concerned','waiting','offline','ended','idle'].includes(input.base)?input.base:'idle',
        transient:['updated','counterpoint','corrected','happy'].includes(input.transient)&&
          !(input.transient==='happy'&&['warning','error'].includes(input.severity))?input.transient:'none',
        severity:['warning','error'].includes(input.severity)?input.severity:'info',
        motionAllowed:!!(this.motion&&!this.reduced&&this.visible&&!this.hidden)});
      this.refresh();
    }
    preferences({motion=this.motion,reduced=this.reduced,visible=this.visible,hidden=this.hidden}){
      Object.assign(this,{motion,reduced,visible,hidden});this.update(this.input);
    }
    refresh(){
      if(this.adapter){
        this.call('pause',!this.input.motionAllowed);
        if(this.adapter && this.visible&&!this.hidden)this.call('update',this.input);
      }
      this.onStatic({available:!!this.adapter,failed:this.failed,hidden:this.hidden});
    }
  }
  if(typeof module!=='undefined')module.exports={RendererHost};else root.ObserverRenderer={RendererHost};
})(typeof window!=='undefined'?window:globalThis);
