"""Display-only regression: animation and colors never adjudicate the incident."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest
from test_incident_status import document
from workbench.case_synthesis import source_revision
from workbench.observer_view import project, validate
from scripts.observer_snapshot import capture

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')


def rows(verdict='confirmed'):
    d=document(verdict)
    result=[{'kind':'case','id':'CASE-demo','case_id':'CASE-demo','name':'synthetic UI fixture',
             'status':'running','created_at':'2026-01-01T00:00:00Z'}]
    for kind in ('evidence','task','observation','case_synthesis'):
        for item in d[kind]:
            result.append({**item,'kind':kind,'case_id':'CASE-demo','created_at':'2026-01-01T00:00:01Z'})
    result[-1]['source_revision']=source_revision([r for r in result if r['kind']=='observation'])
    return result


def view(records):
    return validate(project(records,case_id='CASE-demo',run_id='fixture',data_mode='example',
        captured_at='2026-01-01T01:00:00Z',ledger_position={'observation_scope':'all canonical observations'}))


def node(script,*args):
    assert NODE, 'Node is required, not skipped'
    r=subprocess.run([NODE,'-e',script,*map(str,args)],capture_output=True,text=True,timeout=15)
    assert r.returncode==0,r.stderr


def test_explicit_scoped_verdict_is_bound_to_same_snapshot():
    v=view(rows());s=v['summary']['intrusion'];ref=s['leading_ref']
    assert s['verdict']=='confirmed'
    obj=v['objects'][ref['key']]
    assert obj['version']==ref['version'] and obj['scope']=='권한 없는 실행 여부'
    assert obj['refs']==[{'key':'observation:o','version':v['objects']['observation:o']['version']}]
    node("""
const a=require('node:assert/strict'),m=require(process.argv[1]),v=JSON.parse(process.argv[2]);
a.equal(m.judgment(v).level,'confirmed');
a.equal(m.judgment(v,{available:false}).level,'stale');
a.equal(m.judgment(v,{restricted:()=>true}).level,'stale');
v.objects[v.summary.intrusion.leading_ref.key].version='old';
a.equal(m.judgment(v).level,'stale');
""",ROOT/'ui/observer/moa.js',json.dumps(v))


@pytest.mark.parametrize('change',['source_change','new_related','disconnect','retry','superseded',
                                  'open_objection','followup','rejected','static_only','unknown_freshness'])
def test_stale_or_narrow_evidence_cannot_turn_moa_red(change):
    r=rows();e,t,o,s=r[1:]
    if change=='source_change':o['fields']['path']='/changed'
    elif change=='new_related':r.append({**o,'id':'new'})
    elif change=='disconnect':e['connected']=False
    elif change=='retry':t['retry_generation']=1
    elif change=='superseded':t['superseded']=True
    elif change=='open_objection':s['finding']['open_objections']=[{'id':'unresolved'}]
    elif change=='followup':s['followup_dossier_id']='next'
    elif change=='rejected':s['status']='model_failed'
    elif change=='unknown_freshness':s['_incident_source_current']=None
    else:o['type']='linux_configuration';s['source_revision']=source_revision([o])
    assert view(r)['summary']['intrusion']['leading_ref'] is None


def test_latest_refutation_replaces_confirmation_without_global_clean_verdict():
    r=rows();replacement=rows('refuted')[-1];replacement['id']='new'
    r.append(replacement)
    s=view(r)['summary']['intrusion']
    assert s['verdict']=='undetermined' and s['assessed_scopes']==1
    r[-1]['status']='model_failed'
    assert view(r)['summary']['intrusion']['leading_ref'] is None


def test_canonical_dependency_capture_is_read_only_and_sees_uncited_related_record(tmp_path):
    p=tmp_path/'fixture.sqlite';r=rows()
    with sqlite3.connect(p) as db:
        db.execute('CREATE TABLE records (id TEXT,kind TEXT,case_id TEXT,created_at TEXT,body TEXT)')
        db.executemany('INSERT INTO records VALUES (?,?,?,?,?)',[(x['id'],x['kind'],x['case_id'],x['created_at'],json.dumps(x)) for x in r])
    before=hashlib.sha256(p.read_bytes()).hexdigest()
    a=capture(p,'CASE-demo','fixture');assert a['summary']['intrusion']['verdict']=='confirmed'
    assert hashlib.sha256(p.read_bytes()).hexdigest()==before
    o={**r[3],'id':'uncited-related'}
    with sqlite3.connect(p) as db:
        db.execute('INSERT INTO records VALUES (?,?,?,?,?)',(o['id'],o['kind'],o['case_id'],o['created_at'],json.dumps(o)))
    assert capture(p,'CASE-demo','fixture')['summary']['intrusion']['leading_ref'] is None


def test_phase_briefing_is_meaningful_before_any_hypothesis_and_never_invents_work():
    node("""
const a=require('node:assert/strict'),m=require(process.argv[1]),v={case:{status:'running'}};
for(const title of ['integrity','inventory','normalize','timeline','crosscheck','linux_scan','windows_scan','linux_investigate','ai_judgment','investigation_report']){
  const x=m.activity(v,[{title,state:'running',kind:'task'}]);
  a.ok(x.label&&x.why&&x.meaning,title);a.notEqual(x.base,'idle');
}
a.equal(m.activity(v,[{title:'linux_scan',state:'running',kind:'task'}]).base,'gathering');
a.equal(m.activity(v,[{state:'input_registered',kind:'model'}]).base,'waiting');
a.equal(m.activity(v,[]).base,'idle');
a.equal(m.activity(v,[],{available:false}).base,'offline');
a.equal(m.activity({case:{status:'paused'}},[{state:'running',kind:'tool'}]).base,'ended');
const n=m.judgment({objects:{many_claims:{judgment:'확인'}},summary:{adopted_claims:10000}});
a.equal(n.level,'undetermined');a.equal(n.scope,'');a.ok(n.label.includes('판단 전'));
""",ROOT/'ui/observer/moa.js')


def test_local_svg_motion_safety_and_no_remote_asset_loading():
    html=(ROOT/'ui/observer/index.html').read_text();css=(ROOT/'ui/observer/moa.css').read_text()
    assert '<svg viewBox=' in html and 'Live2D 모델 미연결' not in html
    assert '<circle class="moa-body"' in html and 'moa-antenna' not in html
    assert 'https://' not in html and 'http://' not in html
    assert 'prefers-reduced-motion:reduce' in css
    assert '[data-motion=false]' in css and '[data-base=offline]' in css
    assert 'animation:none!important' in css
    assert '[data-level=confirmed] .moa-mouth{display:none}' in css
    assert '파란색은 정상 판정이 아니에요.' not in html
    node("""
const a=require('node:assert/strict'),{RendererHost}=require(process.argv[1]);
let status;const h=new RendererHost(x=>status=x);h.preferences({motion:true});
for(const base of ['searching','thinking','organizing','waiting','concerned','offline','ended','idle']){
  h.update({base,secret:'raw source'});a.equal(h.input.base,base);
  a.deepEqual(Object.keys(h.input).sort(),['base','motionAllowed','severity','transient']);
}
h.preferences({visible:false});a.equal(h.input.motionAllowed,false);
h.preferences({visible:true,reduced:true});a.equal(h.input.motionAllowed,false);
""",ROOT/'ui/observer/renderer.js')


def test_gray_workspace_does_not_recolor_semantic_verdicts():
    html=(ROOT/'ui/observer/index.html').read_text()
    theme=(ROOT/'ui/observer/gray-theme.css').read_text()
    assert html.index('/gray-theme.css')>html.index('/moa.css')
    assert '--wash:#f3f4f6' in theme and '--ink:#292d33' in theme
    assert '--moa-' not in theme and '[data-level=' not in theme
    assert '.badge.warn' in theme and '.badge.error' in theme


def test_pose_examples_are_display_only_and_happiness_cannot_mask_warning():
    node("""
const a=require('node:assert/strict'),m=require(process.argv[1]),{RendererHost}=require(process.argv[2]);
a.ok(Object.keys(m.previews).length>=12);
const h=new RendererHost(()=>{});h.preferences({motion:true});
for(const p of Object.values(m.previews)){
  h.update({...p,severity:'info'});a.equal(h.input.base,p.base);
  a.deepEqual(Object.keys(h.input).sort(),['base','motionAllowed','severity','transient']);
}
h.update({base:'idle',transient:'happy',severity:'info'});a.equal(h.input.transient,'happy');
h.update({base:'idle',transient:'happy',severity:'error'});a.equal(h.input.transient,'none');
h.preferences({hidden:true});a.equal(h.input.motionAllowed,false);
""",ROOT/'ui/observer/moa.js',ROOT/'ui/observer/renderer.js')
    js=(ROOT/'ui/observer/observer.js').read_text()
    preview=js.split("$('moa-preview-open').onclick=")[1].split("$('activity-filter')")[0]
    assert 'fetch(' not in preview and 'synchronize(' not in preview and 'state.' not in preview
    assert "cloneNode(true)" in preview


def test_live_work_elapsed_time_and_target_do_not_invent_execution():
    node("""
const a=require('node:assert/strict'),m=require(process.argv[1]);
const v={case:{status:'running'}},at='2026-01-01T00:00:00Z',now=Date.parse(at)+32000;
const job={id:'J',kind:'tool',state:'running',title:'search',target:'example',timer_at:at,timer_origin:'execution'};
a.equal(m.elapsedActivity(v,[job],{now}).text,'32초째 ‘example’를 검색하고 있어요.');
const dispatch={...job,state:'waiting',timer_origin:'dispatch'};
const waiting=m.elapsedActivity(v,[dispatch],{now});
a.ok(waiting.text.startsWith('요청 후 32초'));a.ok(waiting.text.includes('결과를 확인'));
a.ok(!waiting.text.includes('검색하고 있어요'));
const prepared={id:'M',kind:'model',state:'input_registered',target:'단서 A',timer_at:at,timer_origin:'input_registered'};
a.ok(m.elapsedActivity(v,[prepared],{now}).text.startsWith('입력 준비 후 32초'));
const stage={id:'T',kind:'task',state:'running',title:'linux_scan',at};
a.ok(!m.elapsedActivity(v,[stage],{now}).text.includes('32초'));
a.ok(m.elapsedActivity(v,[stage],{now,observedSince:now-3000}).text.startsWith('확인 후 3초'));
a.equal(m.activity(v,[stage,prepared]).base,'waiting');
a.equal(m.elapsedActivity(v,[job],{now,available:false}).id,null);
a.equal(m.elapsedActivity({case:{status:'paused'}},[job],{now}).id,null);
a.ok(m.elapsedActivity(v,[{...job,timer_at:'bad'}],{now}).timer_basis.includes('미제공'));
""",ROOT/'ui/observer/moa.js')
    html=(ROOT/'ui/observer/index.html').read_text()
    assert '모아' not in html and '포식이' in html
    assert '<title>forsic · 포식이 조사실</title>' in html
    js=(ROOT/'ui/observer/observer.js').read_text()
    tick=js.split('function updateLiveWork(){')[1].split("$('refresh').onclick")[0]
    assert 'fetch(' not in tick and 'synchronize(' not in tick and 'state.accept(' not in tick


def test_activity_projection_uses_search_query_and_never_times_unsent_or_finished_jobs():
    r=rows();task=r[2]
    job={'kind':'investigation_job','id':'J','case_id':'CASE-demo','task_id':task['id'],
         'status':'admitted','created_at':'2026-01-01T00:00:01Z',
         'request':{'tool':'search','query':'literal needle','path':'/search/scope'}}
    r.append(job)
    a=next(a for a in view(r)['activity']['items'] if a['id']=='J')
    assert a['state']=='queued' and a['timer_at'] is None and a['target']=='literal needle'
    job.update(status='dispatched',worker_status='running',dispatched_at='2026-01-01T00:00:02Z')
    a=next(a for a in view(r)['activity']['items'] if a['id']=='J')
    assert a['state']=='running' and a['timer_origin']=='dispatch'
    job.update(status='failed')
    a=next(a for a in view(r)['activity']['items'] if a['id']=='J')
    assert a['state']=='failed' and a['timer_at'] is None
    job.update(status='ingested',result_status='partial')
    a=next(a for a in view(r)['activity']['items'] if a['id']=='J')
    assert a['state']=='partial' and a['timer_at'] is None
