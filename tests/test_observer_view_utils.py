import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def run(code):
    binary=os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')
    assert binary, 'Node is required for the display contract'
    subprocess.run([binary,'-e',code],cwd=ROOT,check=True)


def test_purpose_ref_uses_exact_current_source_and_does_not_auto_link_markup():
    run("""
const a=require('node:assert/strict'),u=require('./ui/observer/view-utils.js');
const key='observation:OBSERVATION-012345abcdef',ref={key,version:'v',source_version:'raw-v'},
 view={objects:{[key]:{type:'observation',version:'v',source_version:'raw-v'}}};
const item={purpose:'OBSERVATION-012345abcdef의 기록을 확인합니다. https://untrusted.invalid <img onerror=x>',purpose_refs:[ref]};
let parts=u.purposeFragments(item,view);a.equal(parts.filter(x=>x.ref).length,1);
a.equal(parts.map(x=>x.text).join(''),item.purpose);
a.ok(!parts.some(x=>x.ref&&x.text.includes('https')));
for(const patch of [{version:'changed'},{source_version:'changed'},{type:'claim'}]){
 parts=u.purposeFragments(item,{objects:{[key]:{...view.objects[key],...patch}}});
 a.equal(parts.filter(x=>x.ref).length,0);a.equal(parts.filter(x=>x.unresolved).length,1);
}
a.equal(u.purposeFragments({...item,purpose_refs:[]},view).filter(x=>x.ref).length,0);
""")


def test_file_clock_grouping_does_not_merge_record_time_or_distinct_sources():
    run("""
const a=require('node:assert/strict'),u=require('./ui/observer/view-utils.js');
const base={source_ref:{key:'observation:O',version:'v'},representative_claim_ref:{key:'claim:C',version:'cv'},
 lane:'file',comparable:true,shape:'point',raw_values:['2026-01-01T00:00:00Z'],normalized_ns:['1'],estimated:false};
const clocks=['ctime','mtime','crtime'].map(meaning=>({...base,id:meaning,meaning:'파일 '+meaning,basis:meaning+' metadata'}));
const atime={...base,id:'atime',meaning:'파일 atime',raw_values:['2026-01-02T00:00:00Z'],normalized_ns:['2']};
const record={...base,id:'record',lane:'record',meaning:'기록 시각'};
let grouped=u.timeGroups([...clocks,atime,record,{...clocks[0]}]);
a.equal(grouped.length,2);a.equal(grouped[0].members.length,4);a.equal(grouped[1].lane,'record');
const lines=u.fileClockLines(grouped[0]);a.equal(lines.length,2);
a.equal(lines[0].meanings.length,3);a.equal(lines[0].assertions.length,3);
for(const patch of [{source_ref:{key:'observation:OTHER',version:'v'}},
 {source_ref:{key:'observation:O',version:'v2'}},
 {representative_claim_ref:{key:'claim:C',version:'changed'}},{comparable:false}]){
 grouped=u.timeGroups([clocks[0],{...clocks[1],...patch}]);a.equal(grouped.length,2);
}
const estimates=u.fileClockLines({members:[clocks[0],{...clocks[1],estimated:true}]});
a.equal(estimates.length,2); // uncertainty must not be flattened into a known clock
const repeated=u.timeGroups([record,{...record,id:'separate-occurrence'}]);
a.equal(repeated.length,2); // identical wording/time is not evidence of one occurrence
""")


def test_current_source_popup_keeps_historical_selection_and_no_auto_fetch():
    js=(ROOT/'ui/observer/observer.js').read_text()
    popup=js.split('function openPurposeSource(')[1].split('function refreshPurposeSource(')[0]
    assert 'state.pin(' not in popup and 'state.select(' not in popup
    assert 'source_version!==ref.source_version' in popup
    assert 'more=button(' in popup and 'await read(' in popup
    assert 'excerpt.textContent=loaded' in popup
    assert 'window.open' not in popup and 'innerHTML' not in popup
    timeline=js.split('function timeCard(')[1].split('function renderTimeline(')[0]
    assert '기록된 값' not in timeline
    assert '추정 시각' in timeline and '복수 시각 후보' in timeline and '연속 구간' in timeline


def test_same_file_clock_fragments_group_only_with_complete_matching_identity():
    run("""
const a=require('node:assert/strict'),u=require('./ui/observer/view-utils.js');
const obj=id=>({type:'observation',key:'observation:'+id,version:id+'v',title:'/fixture/cron',
 evidence_id:'E',locator:{partition_offset:0,inode:12}}),objects={'observation:A':obj('A'),'observation:B':obj('B')},
 view={envelope:{case_id:'C',run_id:'R'},objects};
const clocks=id=>['ctime','mtime'].map(meaning=>({id:id+meaning,lane:'file',meaning,
 source_ref:{key:'observation:'+id,version:id+'v'},representative_claim_ref:{key:'claim:C',version:'cv'},
 raw_values:['2026-01-01T00:00:00.999Z'],normalized_ns:['1'],shape:'point',basis:'filesystem',comparable:true}));
let grouped=u.timeGroups([...clocks('A'),...clocks('B')],view);
a.equal(grouped.length,1);a.equal(grouped[0].source_refs.length,2);a.equal(grouped[0].members.length,4);
for(const change of [{evidence_id:'OTHER'},{title:'/another/path'},{locator:{partition_offset:1,inode:12}},
 {locator:{partition_offset:0,inode:13}},{evidence_id:null},{version:'old'}]){
 a.equal(u.timeGroups([...clocks('A'),...clocks('B')],{...view,objects:{...objects,'observation:B':{...objects['observation:B'],...change}}}).length,2);
}
a.equal(u.timeGroups([...clocks('A'),{...clocks('B')[0],estimated:true},clocks('B')[1]],view).length,2);
a.equal(u.clockText('2026-01-01T00:00:00.999999999Z'),'2026-01-01 09:00:00 KST');
a.equal(u.clockText('2026-01-01T12:00:00.001+09:00'),'2026-01-01 12:00:00 KST');
a.equal(u.clockText('year unknown'),'year unknown');
a.equal(clocks('A')[0].raw_values[0],'2026-01-01T00:00:00.999Z');
const cells=u.fileClockCells(grouped[0]);a.equal(cells.length,2);a.equal(cells[0].assertions.length,2);
const otherClaim=clocks('B').map(t=>({...t,representative_claim_ref:{key:'claim:OTHER',version:'cv2'},relevance_reason:'different explanation'}));
const physical=u.timeGroups([...clocks('A'),...otherClaim],view);
a.equal(physical.length,1);a.equal(physical[0].members.length,4);
a.equal(physical[0].members[2].representative_claim_ref.key,'claim:OTHER');
a.equal(physical[0].members[2].relevance_reason,'different explanation');
const native=clocks('B').map(t=>({...t,raw_values:['2026-01-01T00:00:00.999999999Z'],normalized_ns:['999']}));
const mixed=u.timeGroups([...clocks('A'),...native],{...view,objects:{...objects,'observation:B':{...obj('B'),locator:{partition_offset:0,inode:'12'}}}});
a.equal(mixed.length,1);const variants=u.fileClockCells(mixed[0]);
a.equal(variants.length,2);a.equal(variants[0].source_variants.length,2);a.equal(variants[0].raw_values.length,2);
a.equal(variants[0].assertions.length,2);a.equal(mixed[0].members[2].normalized_ns[0],'999');
for(const inode of ['012','12x','9007199254740993'])a.equal(u.timeGroups([...clocks('A'),...clocks('B')],
 {...view,objects:{...objects,'observation:B':{...obj('B'),locator:{partition_offset:0,inode}}}}).length,2);
""")


def test_kst_display_rolls_dates_without_inventing_unknown_times():
    run("""
const a=require('node:assert/strict'),u=require('./ui/observer/view-utils.js');
a.equal(u.clockText('2026-12-31T18:12:34.999Z'),'2027-01-01 03:12:34 KST');
a.equal(u.clockText('2026-01-01T23:12:34-05:00'),'2026-01-02 13:12:34 KST');
a.equal(u.clockText('2026-01-01T00:00:00+00:00'),'2026-01-01 09:00:00 KST');
for(const raw of ['2026-01-01T00:00:00','Jun 28 12:00:00','year unknown',
 '2026-02-30T00:00:00Z','2026-01-01T24:00:00Z','2026-01-01T12:01:60Z'])a.equal(u.clockText(raw),raw);
""")


def test_recent_failure_is_a_separate_card_not_a_speech_bubble():
    html=(ROOT/'ui/observer/index.html').read_text()
    js=(ROOT/'ui/observer/observer.js').read_text()
    css=(ROOT/'ui/observer/gray-theme.css').read_text()
    assert '<aside id="guide-feedback"' in html
    assert html.index('id="guide-feedback"') > html.index('class="thought-scene"')
    assert 'id="guide-feedback"' not in html.split('class="work-bubbles"')[1].split('class="thought-scene"')[0]
    assert "feedback.id='guide-feedback';work.append(feedback)" not in js
    assert '.work-feedback{max-width:800px' in css
    assert 'border-radius:4px' in css
    assert "ObserverViewUtils.clockText(String(value))" in js
    assert "toLocaleString('ko-KR'" not in js


def test_identical_work_example_labels_do_not_hide_record_counts():
    run("""
const a=require('node:assert/strict'),m=require('./ui/observer/moa.js');
const examples=[{label:'same command',observation_id:'A'},{label:'same command',observation_id:'B'}];
const [s]=m.reviewSubjects({context:{subjects:[{id:'D',examples,presented_records:8,omitted_examples:6}]}});
a.equal(s.label,'same command');a.equal(s.examples.length,2);a.equal(s.presented,8);a.equal(s.omitted,6);
""")
