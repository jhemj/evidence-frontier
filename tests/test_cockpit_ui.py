"""Pure UI logic contracts; real browser QA is separately recorded."""
import json
import os
import shutil
import subprocess
from pathlib import Path
from html.parser import HTMLParser
import pytest

ROOT=Path(__file__).resolve().parents[1]
NODE=os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')


def js_test(files,script):
    if not NODE or not Path(NODE).exists():pytest.skip('Node runtime unavailable')
    source='const assert=require("node:assert/strict"); const vm=require("node:vm"); const context={assert,Intl,Date,BigInt}; vm.createContext(context);\n'
    for f in files:source+='vm.runInContext('+json.dumps((ROOT/f).read_text())+',context);\n'
    source+='vm.runInContext('+json.dumps(script)+',context);'
    result=subprocess.run([NODE,'-e',source],text=True,capture_output=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_cockpit_exact_time_order_and_unknown_clock():
    js_test(['dist/cockpit.js'],"""
      const cards=[
        {id:'a-late',event_time:{raw:'2026-01-01T00:00:00.000000009Z',time_kind:'occurred',epoch_nanoseconds:'1767225600000000009'}},
        {id:'z-early',event_time:{raw:'2026-01-01T00:00:00.000000001Z',time_kind:'occurred',epoch_nanoseconds:'1767225600000000001'}},
        {id:'metadata',event_time:{raw:'2025-01-01T00:00:00Z',time_kind:'unknown'}},
        {id:'naive',event_time:{raw:'2025-01-01T00:00:00',time_kind:'occurred'}}];
      if(orderClues(cards).map(c=>c.id).join(',')!=='z-early,a-late,metadata,naive')throw Error('chronology');
      if(clueLabel({semantic_judgment:'확인'})!=='확정'||clueLabel({semantic_judgment:'미확인'})!=='추정')throw Error('labels');
    """)


def test_eta_only_measured_phase_and_pause_suppression():
    js_test(['dist/progress.js'],"""
      const now=Date.parse('2026-01-01T00:01:00Z');
      const s={case:{status:'running'},evidence:[{id:'e'}],task:[{id:'t',evidence_id:'e',action:'integrity',status:'running',started_at:'2026-01-01T00:00:00Z',progress:{stage:'segment_hash',elapsed_seconds:60,bytes_done:50,total_bytes:100}}]};
      if(investigationProgress(s,now).eta!==60)throw Error('measured ETA');
      s.case.status='paused';if(investigationProgress(s,now).eta!==null)throw Error('paused ETA');
      s.case.status='running';s.task[0].progress.stage='ewf_verify';if(investigationProgress(s,now).eta!==null)throw Error('hash is not logical verify ETA');
    """)


def test_estimates_and_metadata_have_separate_safe_timeline_lanes():
    js_test(['dist/cockpit.js'],"""
      function esc(x){return String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
      const cards=[
        {id:'meta',event_time:{raw:'2025-01-01T00:00:00Z',time_kind:'file_metadata'}},
        {id:'estimate',event_time:{raw:'2026-01-01T00:00:00Z',time_kind:'estimated'}},
        {id:'event',event_time:{raw:'2026-01-02T00:00:00Z',time_kind:'occurred'}},
        {id:'unknown',event_time:{time_kind:'unknown'}}];
      assert.equal(orderClues(cards).map(c=>c.id).join(','),'event,estimate,meta,unknown');
      const html=renderClueList(orderClues(cards),'');
      for(const text of ['행위 기록 시각','추정 시각','파일 시각 참고','행위 시각 미확인','파일 참고 · 2025.','추정 · 2026.'])assert.ok(html.includes(text),text);
      const details=clueTimeDetails({event_time:{needs_time_followup:true,anchors:[{label:'ctime',raw:'2026-01-01T00:00:00Z',basis:'<script>x</script>',observation_id:'a" onclick="bad'}],next_checks:['<img>']}});
      assert.ok(!details.includes('<script>')&&!details.includes('<img>')&&!details.includes(' onclick="bad'));
      assert.ok(details.includes('시각 원문 ↗')&&details.includes('시각 추가 확인'));
    """)


def test_html_has_unique_controls_and_three_primary_areas():
    class IDs(HTMLParser):
        def __init__(self):super().__init__();self.ids=[]
        def handle_starttag(self,tag,attrs):self.ids.extend(v for k,v in attrs if k=='id')
    parser=IDs();html=(ROOT/'dist/index.html').read_text();parser.feed(html)
    assert len(parser.ids)==len(set(parser.ids))
    assert {'investigation-progress','triage-console','companion-chat','report-dock','evidence-drawer'}<=set(parser.ids)
    assert '>타임라인</h2>' in html


def test_large_pending_queues_are_separate_not_silently_cleared():
    js_test(['dist/cockpit.js'],"""
      const cards=Array.from({length:1800},(_,i)=>({id:String(i),kind:'dossier',unreviewed:true,display:{must_surface:true}}));
      cards.push({id:'h',kind:'hypothesis',unreviewed:true});
      cards.push({id:'assessed',kind:'dossier',unreviewed:true,change_history:[{revision:1}]});
      const lanes=clueLanes(cards);
      if(lanes.pending.length!==1800||lanes.visible.length!==2)throw Error('queue must not flood main timeline');
      if(lanes.pending.length+lanes.visible.length+lanes.quiet.length!==cards.length)throw Error('lost card');
      if(!lifecycleBadge('refuted').includes('반박됨')||!lifecycleBadge('supported').includes('뒷받침됨'))throw Error('lifecycle');
    """)


def test_actual_activity_cards_and_simple_history_escape_untrusted_text():
    js_test(['dist/activity.js'],"""
      function esc(x){return String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
      const now=Date.parse('2026-01-01T00:02:00Z');
      const rows=Array.from({length:8},(_,i)=>({id:'job-'+i,kind:'tool',title:'원문 읽기',target:'<img src=x onerror=alert(1)>',status:i?'done':'running',at:'2026-01-01T00:00:00Z',started_at:'2026-01-01T00:00:00Z',ended_at:i?'2026-01-01T00:01:00Z':null}));
      const html=workActivityMarkup({items:rows,all_total:8},false,now);
      if((html.match(/data-work-id=/g)||[]).length!==5)throw Error('exact recent five');
      if(html.includes('<img')||!html.includes('&lt;img'))throw Error('untrusted target');
      if(!html.includes('2분 0초 경과')||html.includes('job-5'))throw Error('elapsed/limit');
      if(workElapsed({...rows[0],started_at:null},now)!=='시작 시각 미기록')throw Error('invented start');
      if(workElapsed({...rows[0],status:'paused'},now)!=='소요 시간 미기록')throw Error('paused timer');
      if(workStatus(rows[0],true)!=='상태 확인 중')throw Error('stale liveness');
      const history=workHistoryMarkup([{...rows[0],error:'<script>bad</script>'}]);
      if(history.includes('<script>')||!history.includes('오류·제한 내용'))throw Error('error safe disclosure');
    """)
    html=(ROOT/'dist/index.html').read_text()
    assert 'work-status-filter' in html and 'work-kind-filter' in html and 'work-history-list' in html
    assert '상세 기록 ↗' not in (ROOT/'dist/progress.js').read_text()
    assert '<option value="running">수행 중</option>' in html


def test_eta_range_fallback_and_pause_stale_suppression():
    js_test(['dist/progress.js'],"""
      const s={case:{status:'running'},eta_estimate:{low_seconds:600,high_seconds:1800,scope:'AI 추가 조사 단계'}};
      const p={eta:null,etaScope:'단서 판단 단계'};
      if(conservativeEta(s,p).low_seconds!==600)throw Error('fallback range');
      if(conservativeEta(s,p,true)!==null)throw Error('stale estimate');
      s.case.status='paused';if(conservativeEta(s,p)!==null)throw Error('paused estimate');
      s.case.status='running';p.eta=120;p.etaScope='입력 해시 단계';
      if(conservativeEta(s,p).high_seconds<=120)throw Error('measured safety margin');
    """)


def test_progress_clock_ticks_do_not_replace_interactive_controls():
    js_test(['dist/progress.js','dist/activity.js'],"""
      function esc(x){return String(x??'');}
      let writes=0;
      const root={dataset:{},querySelector:()=>null,querySelectorAll:()=>[],set innerHTML(x){writes++;}};
      const document={getElementById:id=>id==='investigation-progress'?root:null,activeElement:null};
      const s={case:{id:'c',status:'running'},evidence:[],task:[],activity:{items:[],all_total:0}};
      renderInvestigationProgress(s);renderInvestigationProgress(s);
      if(writes!==1)throw Error('clock replaced button');
      s.activity.all_total=1;renderInvestigationProgress(s);
      if(writes!==2)throw Error('new work not rendered');
    """)
