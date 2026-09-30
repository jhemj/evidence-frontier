(function(root){
  'use strict';
  function purposeFragments(item,view){
    const text=typeof item?.purpose==='string'?item.purpose:'';
    const refs=new Map((item?.purpose_refs||[]).map(r=>[r.key,r]));
    const parts=[];let end=0;
    for(const m of text.matchAll(/\bOBSERVATION-[A-Za-z0-9_-]+\b/g)){
      if(m.index>end)parts.push({text:text.slice(end,m.index)});
      const key='observation:'+m[0],ref=refs.get(key),object=view?.objects?.[key];
      const bound=ref&&object?.type==='observation'&&object.version===ref.version&&
        object.source_version===ref.source_version;
      parts.push({text:m[0],ref:bound?ref:null,unresolved:!bound});end=m.index+m[0].length;
    }
    if(end<text.length)parts.push({text:text.slice(end)});
    return parts;
  }
  function timeGroups(entries,view){
    const groups=[],files=new Map(),seen=new Set();
    const clockSets=new Map();
    for(const t of entries.filter(t=>t.lane==='file')){
      const k=JSON.stringify(t.source_ref),clocks=clockSets.get(k)||[];
      clocks.push(JSON.stringify([t.meaning,t.shape,t.basis,
        t.comparable,t.estimated,t.clock_accuracy,t.limitation]));clockSets.set(k,clocks);
    }
    function fileIdentity(t){
      const o=view?.objects?.[t.source_ref.key],l=o?.locator;
      const inode=typeof l?.inode==='string'&&/^(0|[1-9]\d*)$/.test(l.inode)?Number(l.inode):l?.inode;
      if(o?.type!=='observation'||o.version!==t.source_ref.version||!o.evidence_id||
         typeof o.title!=='string'||!o.title.startsWith('/')||
         !Number.isSafeInteger(l?.partition_offset)||l.partition_offset<0||
         !Number.isSafeInteger(inode)||inode<0)return null;
      // Same preserved filesystem object and clock semantics, not equality of
      // all values. Native/focused metadata may have different precision; retain
      // those variants in each cell, never silently pick the more precise value.
      return [view.envelope?.case_id,view.envelope?.run_id,o.evidence_id,
        l.partition_offset,inode,o.title,
        [...new Set(clockSets.get(JSON.stringify(t.source_ref))||[])].sort()];
    }
    for(const t of entries){
      // Remove identical display assertions only, never equal bytes from different records.
      const exact=JSON.stringify([t.id,t.source_ref,t.representative_claim_ref,t.lane,t.meaning,
        t.shape,t.raw_values,t.normalized_ns,t.basis,t.comparable,t.estimated]);
      if(seen.has(exact))continue;seen.add(exact);
      if(t.lane!=='file'){groups.push(t);continue;}
      // One physical file clock is one display card; retain every exact claim
      // and its own rationale below it. This is not claim/evidence deduplication.
      const physical=fileIdentity(t);
      const key=JSON.stringify([physical||t.source_ref,physical?null:t.representative_claim_ref,!!t.comparable]);
      let group=files.get(key);
      if(!group){group={...t,id:'file-clocks:'+t.id,group_kind:'file_clocks',members:[],source_refs:[]};files.set(key,group);groups.push(group);}
      group.members.push(t);
      if(!group.source_refs.some(r=>r.key===t.source_ref.key&&r.version===t.source_ref.version))group.source_refs.push(t.source_ref);
    }
    return groups;
  }
  function fileClockLines(group){
    const lines=[],same=new Map();
    for(const t of group.members||[group]){
      const key=JSON.stringify([t.shape,t.raw_values,t.normalized_ns,!!t.estimated,!!t.comparable]);
      let line=same.get(key);
      if(!line){line={...t,meanings:[],assertions:[]};same.set(key,line);lines.push(line);}
      if(!line.meanings.includes(t.meaning))line.meanings.push(t.meaning);
      line.assertions.push({id:t.id,meaning:t.meaning,basis:t.basis,source_ref:t.source_ref});
    }
    return lines;
  }
  function clockText(raw){
    if(typeof raw!=='string')return '미상';
    const iso=/^(\d{4}-\d{2}-\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})$/.exec(raw);
    if(!iso)return raw; // A missing year/zone must not become an invented absolute time.
    const day=new Date(iso[1]+'T00:00:00Z');
    if(Number.isNaN(day.valueOf())||day.toISOString().slice(0,10)!==iso[1]||
       +iso[2]>23||+iso[3]>59||+iso[4]>59)return raw;
    const date=new Date(iso[1]+'T'+iso[2]+':'+iso[3]+':'+iso[4]+iso[5]);
    if(Number.isNaN(date.valueOf()))return raw;
    const kst=new Date(date.valueOf()+9*60*60*1000).toISOString();
    return kst.length===24?kst.slice(0,19).replace('T',' ')+' KST':raw;
  }
  function fileClockCells(group){
    const cells=[],byMeaning=new Map();
    for(const line of fileClockLines(group))for(const meaning of line.meanings){
      let cell=byMeaning.get(meaning);
      if(!cell){cell={...line,meaning,raw_values:[],source_variants:[],assertions:[],
        label:({'파일 ctime · 메타데이터 변경':'ctime · 변경','파일 mtime · 내용 수정':'mtime · 수정',
          '파일 atime · 접근':'atime · 접근','파일 crtime · 파일 생성':'crtime · 생성'})[meaning]||meaning};
        byMeaning.set(meaning,cell);cells.push(cell);}
      cell.source_variants.push({raw_values:line.raw_values,normalized_ns:line.normalized_ns,
        estimated:line.estimated,comparable:line.comparable,shape:line.shape});
      for(const raw of line.raw_values)if(!cell.raw_values.includes(raw))cell.raw_values.push(raw);
      cell.assertions.push(...line.assertions.filter(a=>a.meaning===meaning));
    }
    return cells;
  }
  class WorkHistory{
    constructor(){this.identity=null;this.items=[];this.selectedId=null;this.currentId=null;}
    update(envelope,items,current){
      const identity=JSON.stringify([envelope.case_id,envelope.run_id,envelope.data_mode]);
      if(this.identity!==identity){this.identity=identity;this.items=[];this.selectedId=null;}
      this.currentId=current?.id||null;
      const byId=new Map(this.items.map(a=>[a.id,a]));
      for(const a of items)if(['tool','model','task'].includes(a.kind))byId.set(a.id,a);
      this.items=[...byId.values()].sort((a,b)=>(Date.parse(b.at)||0)-(Date.parse(a.at)||0)||a.id.localeCompare(b.id)).slice(0,200);
      if(this.currentId){const active=this.items.find(a=>a.id===this.currentId);if(active)this.items=[active,...this.items.filter(a=>a!==active)];}
      if(this.selectedId&&!this.items.some(a=>a.id===this.selectedId))this.selectedId=null;
    }
    get index(){const index=this.items.findIndex(a=>a.id===(this.selectedId||this.currentId));return Math.max(0,index);}
    get selected(){return this.items[this.index]||null;}
    get past(){return !!this.selectedId&&this.selectedId!==this.currentId;}
    latest(){this.selectedId=null;return this.selected;}
    select(id){if(this.items.some(a=>a.id===id))this.selectedId=id;return this.selected;}
    move(delta,{eligible=null,defaultId=null}={}){
      const choices=eligible?this.items.filter(eligible):this.items;
      const index=eligible?Math.max(0,choices.findIndex(a=>a.id===(this.selectedId||defaultId||this.currentId))):this.index;
      const next=Math.min(choices.length-1,Math.max(0,index+delta));this.selectedId=choices[next]?.id||null;return this.selected;
    }
  }
  const api={purposeFragments,timeGroups,fileClockLines,fileClockCells,clockText,WorkHistory};
  if(typeof module!=='undefined')module.exports=api;else root.ObserverViewUtils=api;
})(typeof window!=='undefined'?window:globalThis);
