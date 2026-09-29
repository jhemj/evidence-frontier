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


def test_scenarios_top_three_five_all_ties_and_untrusted_content():
    js_test(['dist/scenarios.js'],"""
      function esc(x){return String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
      const s={scenarios:{cards:Array.from({length:13},(_,i)=>({id:String(i),rank:1,tied:true,title:'<script>bad</script>',lifecycle:i===12?'refuted':'strengthened',investigation_priority:'high',supporting_evidence_ids:['a" onclick="bad'],next_check:'승인 이력 대조',alternative_explanation:'정상 운영 가능성'}))}};
      assert.equal(scenarioCards(s).length,3);assert.equal(scenarioCards(s,5).length,5);
      assert.equal(scenarioCards(s,Infinity).length,13);assert.equal(scenarioCards(s,Infinity,'refuted').length,1);
      const html=scenarioCard(s.scenarios.cards[0]);assert.ok(html.includes('공동 1위')&&html.includes('우선 검증'));
      for(const word of ['지지 근거','반대 근거','다음 확인','승인 이력 대조','정상 운영 가능성'])assert.ok(html.includes(word));
      assert.ok(!html.includes('<script>')&&!html.includes(' onclick="bad')&&!html.includes('%'));
      assert.equal(s.scenarios.cards.length,13);
      assert.ok(scenarioCard({...s.scenarios.cards[0],rank:null}).includes('순위 보류'));
    """)


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


def test_open_objection_badge_and_source_links_are_visible_and_escaped():
    js_test(['dist/cockpit.js'],"""
      function esc(x){return String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
      const html=clueCard({id:'d',title:'Narrow fact',semantic_judgment:'확인',
        publication_limit:'범위 제한',open_objections:[{statement:'<script>untrusted</script>',observation_ids:['counter']} ]});
      assert.ok(html.includes('반론 검토 중 · 1건')&&html.includes('미해결 반론'));
      assert.ok(html.includes('data-ref="counter"')&&!html.includes('<script>'));
    """)


def test_eta_only_measured_phase_and_pause_suppression():
    js_test(['dist/progress.js'],"""
      const now=Date.parse('2026-01-01T00:01:00Z');
      const s={case:{status:'running'},evidence:[{id:'e'}],task:[{id:'t',evidence_id:'e',action:'integrity',status:'running',started_at:'2026-01-01T00:00:00Z',progress:{stage:'segment_hash',elapsed_seconds:60,bytes_done:50,total_bytes:100}}]};
      if(investigationProgress(s,now).eta!==60)throw Error('measured ETA');
      s.case.status='paused';if(investigationProgress(s,now).eta!==null)throw Error('paused ETA');
      s.case.status='running';s.task[0].progress.stage='ewf_verify';if(investigationProgress(s,now).eta!==null)throw Error('hash is not logical verify ETA');
    """)


def test_estimates_and_metadata_share_one_chronological_timeline_with_visible_labels():
    js_test(['dist/cockpit.js'],"""
      function esc(x){return String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
      const cards=[
        {id:'meta',event_time:{raw:'2025-01-01T00:00:00Z',time_kind:'file_metadata'}},
        {id:'estimate',event_time:{raw:'2026-01-01T00:00:00Z',time_kind:'estimated'}},
        {id:'event',event_time:{raw:'2026-01-02T00:00:00Z',time_kind:'occurred'}},
        {id:'between',event_time:{raw:'2025-12-31T23:59:00Z',time_kind:'occurred'}},
        {id:'late-meta',event_time:{raw:'2026-01-01T12:00:00Z',time_kind:'file_metadata'}},
        {id:'unknown',event_time:{time_kind:'unknown'}}];
      const expected='meta,between,estimate,late-meta,event,unknown';
      assert.equal(orderClues(cards).map(c=>c.id).join(','),expected);
      const html=renderClueList(cards,'');
      assert.equal([...html.matchAll(/data-clue-id="([^"]+)"/g)].map(m=>m[1]).join(','),expected);
      assert.equal((html.match(/class="clue-timeline"/g)||[]).length,1);
      for(const text of ['기록·추정 시각 · KST','추정 시각','파일 시각 기반 추정','행위 시각 미확인','실제 행위 시각은 미확인','2025.','2026.'])assert.ok(html.includes(text),text);
      assert.ok(html.indexOf('unknown-caption')>html.indexOf('data-clue-id="event"'));
      assert.ok(!html.includes('파일 시각 참고</div>'));
      const details=clueTimeDetails({event_time:{needs_time_followup:true,anchors:[{label:'ctime',raw:'2026-01-01T00:00:00Z',basis:'<script>x</script>',observation_id:'a" onclick="bad'}],next_checks:['<img>']}});
      assert.ok(!details.includes('<script>')&&!details.includes('<img>')&&!details.includes(' onclick="bad'));
      assert.ok(details.includes('시각 원문 ↗')&&details.includes('시각 추가 확인'));
    """)


def test_mixed_time_kinds_preserve_timezone_and_nanoseconds_even_without_epoch_field():
    js_test(['dist/cockpit.js'],"""
      const cards=[
        {id:'a-late',event_time:{raw:'2026-01-01T00:00:00.000000009Z',time_kind:'file_metadata',epoch_nanoseconds:'1767225600000000009'}},
        {id:'b-middle',event_time:{raw:'2026-01-01T09:00:00.000000005+09:00',time_kind:'occurred'}},
        {id:'z-early',event_time:{raw:'2026-01-01T00:00:00.000000001Z',time_kind:'estimated',epoch_nanoseconds:'1767225600000000001'}},
        {id:'invalid',event_time:{raw:'not-a-dateZ',time_kind:'estimated'}},
        {id:'naive',event_time:{raw:'2025-01-01T00:00:00',time_kind:'file_metadata'}}];
      const original=cards.map(c=>c.id).join(',');
      for(const rows of [cards,[...cards].reverse(),[cards[1],cards[3],cards[2],cards[4],cards[0]]]) {
        assert.equal(orderClues(rows).map(c=>c.id).join(','),'z-early,b-middle,a-late,invalid,naive');
      }
      assert.equal(cards.map(c=>c.id).join(','),original);
      const tied=['file_metadata','estimated','occurred'].map((time_kind,i)=>({id:String(i),event_time:{time_kind,raw:i?'2026-01-01T09:00:00+09:00':'2026-01-01T00:00:00Z'}}));
      assert.equal(orderClues(tied.reverse()).map(c=>c.id).join(','),'0,1,2');
    """)


def test_time_badges_are_independent_of_evidence_confidence_and_escape_provenance():
    js_test(['dist/cockpit.js'],"""
      function esc(x){return String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
      const card={id:'confirmed-with-estimated-time',semantic_judgment:'확인',event_time:{
        raw:'2026-01-01T00:00:00Z',time_kind:'file_metadata',time_label:'ctime',
        time_basis:'<img src=x onerror="bad">',limitation:'파일 시각은 실행 시각이 아닙니다.'}};
      const html=clueCard(card);
      assert.ok(html.includes('clue-card confirmed time-file_metadata'));
      assert.ok(html.includes('time-badge file_metadata')&&html.includes('파일 시각 기반 추정'));
      assert.ok(html.includes('ctime')&&html.includes('파일 시각은 실행 시각이 아닙니다.'));
      assert.ok(!html.includes('<img')&&!html.includes('onerror="bad"'));
      const unknown=clueCard({...card,event_time:{raw:'2026-01-01T00:00:00',time_kind:'estimated'}});
      assert.ok(unknown.includes('time-unknown')&&!unknown.includes('datetime=')&&!unknown.includes('time-badge'));
      assert.ok(renderClueList([card],'').includes('data-clue-id="confirmed-with-estimated-time"'));
      assert.ok(!renderClueList([card],'').includes('unknown-caption'));
    """)


def test_html_has_unique_controls_and_three_primary_areas():
    class IDs(HTMLParser):
        def __init__(self):super().__init__();self.ids=[]
        def handle_starttag(self,tag,attrs):self.ids.extend(v for k,v in attrs if k=='id')
    parser=IDs();html=(ROOT/'dist/index.html').read_text();parser.feed(html)
    assert len(parser.ids)==len(set(parser.ids))
    assert {'investigation-progress','triage-console','companion-chat','report-dock','evidence-drawer'}<=set(parser.ids)
    assert '>타임라인</h2>' in html


def test_incident_board_never_turns_fact_counts_into_intrusion_and_escapes_brief():
    js_test(['dist/incident.js'],"""
      function esc(x){return String(x??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');}
      const s={case:{status:'running'},triage:{cards:Array.from({length:80},()=>({kind:'dossier',state:'reviewed',semantic_judgment:'확인',relevance_score:100}))}};
      let html=incidentMarkup(s);
      assert.ok(html.includes('<h2>판단 대기</h2>')&&html.includes('단서 80건 검토'));
      assert.ok(!html.includes('80%'));
      s.incident_status={version:'incident-assessment-1',verdict:'probable',leading:{scope:'<img>',summary:'<script>bad</script> 원문에서 호출은 확인했습니다. 실행 결과를 추가 조사해야 합니다.',supporting_evidence_ids:['o" onclick="bad']},counts:{probable:1,refuted:2},assessments:[{},{},{}]};
      html=incidentMarkup(s,true);
      assert.ok(html.includes('<h2>침해 유력</h2>')&&html.includes('연결 지연 · 이전 판단'));
      assert.ok(html.includes('실행 결과를 추가 조사')&&html.includes('침해 확률이 아닙니다'));
      assert.ok(!html.includes('<script>')&&!html.includes('<img>')&&!html.includes(' onclick="bad'));
    """)


def test_report_preview_deduplicates_displays_loading_and_ignores_old_case():
    js_test(['dist/report.js'],"""
      const elements=new Map();
      function $(id){if(!elements.has(id))elements.set(id,{hidden:false,textContent:'',attrs:{},classList:{add(){},remove(){}},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(k){delete this[k];},contentWindow:{scrollY:10}});return elements.get(id);}
      let current='a',reportCase=null,selectedReport=null,reportReader='executive',snapshot={view_revision:'1'};
      class AbortController{constructor(){this.signal={};}abort(){}}
      const setTimeout=()=>1,clearTimeout=()=>{};
      const requests=[];
      function api(){return new Promise((resolve,reject)=>requests.push({resolve,reject}));}
      (async()=>{
        const first=loadReport(),same=loadReport();assert.equal(first,same);assert.equal(requests.length,1);
        assert.equal($('report-load-state').hidden,false);assert.ok($('report-load-message').textContent.includes('불러오고'));
        current='b';snapshot={view_revision:'2'};resetReportPreview();const second=loadReport();
        requests[0].resolve({text:async()=>'<h1>OLD</h1>',headers:{get:()=>null}});await first;
        assert.notEqual($('report-preview').srcdoc,'<h1>OLD</h1>');
        requests[1].resolve({text:async()=>'<h1>NEW</h1>',headers:{get:()=>null}});await second;
        assert.equal($('report-preview').srcdoc,'<h1>NEW</h1>');assert.equal($('report-preview').hidden,false);
        assert.equal($('report-load-state').hidden,true);assert.equal(reportLoading,false);assert.equal(reportLoadedRevision,'2');
        assert.ok(reportNextRefreshAt>Date.now());
        const third=loadReport();requests[2].reject(Error('offline'));await third;
        assert.equal($('report-load-state').hidden,false);assert.equal($('report-retry').hidden,false);
        assert.equal($('report-preview').srcdoc,'<h1>NEW</h1>');
        assert.ok($('report-load-message').textContent.includes('offline'));
      })();
    """)


def test_large_pending_queues_are_separate_not_silently_cleared():
    js_test(['dist/cockpit.js'],"""
      const cards=Array.from({length:1800},(_,i)=>({id:String(i),kind:'dossier',unreviewed:true,display:{must_surface:true}}));
      cards.push({id:'h',kind:'hypothesis',unreviewed:true});
      cards.push({id:'assessed',kind:'dossier',unreviewed:true,change_history:[{revision:1}]});
      const lanes=clueLanes(cards);
      if(lanes.pending.length!==1800||lanes.visible.length!==2)throw Error('queue must not flood main timeline');
      if(lanes.pending.length+lanes.visible.length+lanes.quiet.length!==cards.length)throw Error('lost card');
      if(!lifecycleBadge('refuted').includes('반증됨')||!lifecycleBadge('supported').includes('입증 · 해당 범위'))throw Error('lifecycle');
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
