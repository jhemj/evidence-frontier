import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess

import pytest
from fastapi.testclient import TestClient

from workbench.observer_view import project, validate, digest, time_assertions
from scripts.observer_snapshot import capture
from scripts.serve_observer import create_app
from workbench.observer_session import LiveObserver

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get('FRONTIER_TEST_NODE') or shutil.which('node')


def records():
    def row(kind, id, **kwargs):
        return {'kind': kind, 'id': id, 'case_id': 'CASE-demo', 'created_at': '2026-01-01T00:00:00Z', **kwargs}
    return [row('case', 'CASE-demo', name='계약 시험 · 실제 사건 아님', status='running'),
        row('evidence', 'E1'), row('task', 'T1', evidence_id='E1', status='running'),
        row('observation', 'O1', evidence_id='E1', type='linux_command', timestamp='2026-01-01T01:00:00Z',
            fields={'path': '/fixture/log', 'excerpt': '<img src="https://invalid.example/leak" onerror="alert(1)">',
                    'time_basis': 'explicit UTC record', 'source_sha256': 'abc', 'byte_offset': 0, 'byte_length': 20}),
        row('claim', 'C1', task_id='T1', evidence_id='E1', status='approved', claim_type='observation',
            text='상태 조회 문자열이 기록됨', observation_ids=['O1']),
        row('case_question', 'Q1', task_id='T1', evidence_id='E1', question='기록은 무엇을 보여주는가?',
            source_ids=['C1'], observation_ids=['O1'], state='open'),
        row('test_intent', 'TEST1', question_id='Q1', tool='read_file', status='reserved',
            scope={'task_id': 'T1', 'evidence_id': 'E1', 'request': {'path': '/fixture/log'},
                   'test_design': {'immediate_observable': 'source_content'}},
            admission={'eligible': True}, assessment_status='unassessed')]


def make(rows=None, sequence=1, mode='example'):
    return project(rows if rows is not None else records(), case_id='CASE-demo', run_id='test-run', data_mode=mode,
                   captured_at='2026-01-01T03:00:00Z', ledger_position={'test': True}, sequence=sequence, source_revision=9)


def test_snapshot_refs_and_content_versions():
    a=validate(make())
    rows=records();rows[3]['fields']['excerpt']='정정된 원문'
    b=validate(make(rows,2))
    assert a['objects']['observation:O1']['version'] != b['objects']['observation:O1']['version']
    assert a['objects']['claim:C1']['version'] != b['objects']['claim:C1']['version']
    assert a['objects']['question:Q1']['version'] != b['objects']['question:Q1']['version']
    assert a['envelope']['projection_revision'] != b['envelope']['projection_revision']
    assert b['narrative'][0]['refs'][0]['version']==b['objects']['claim:C1']['version']


@pytest.mark.parametrize('status',['resource_limit','complete','quiescent'])
def test_execution_end_comes_from_current_epoch_not_analysis_inference(status):
    rows=records();rows[0].update(status=status,epoch_id='EP-current')
    rows.extend([{'kind':'epoch','id':'EP-current','case_id':'CASE-demo','status':status,
                  'ended_at':'2026-01-02T00:00:00Z'},
                 {'kind':'epoch','id':'EP-old','case_id':'CASE-demo','ended_at':'2025-01-01T00:00:00Z','end_reason':'old'}])
    view=validate(make(rows));end=view['case']['execution_end']
    assert end['status']==status and end['ended_at']=='2026-01-02T00:00:00Z'
    assert end['reason'] is None # Do not manufacture the unrecorded limit type.
    assert view['summary']['unassessed_tests']==1


def test_cross_case_and_generation_fail_closed():
    rows=records();rows.append({**rows[3], 'id':'BAD','case_id':'other'})
    with pytest.raises(ValueError,match='Cross-case'):make(rows)
    rows=records();rows[4]['generation']=7
    assert 'claim:C1' not in make(rows)['objects']
    rows=records();rows[1]['connected']=False
    assert not make(rows)['objects']


def test_missing_source_is_not_adopted_and_chat_does_not_change_judgment():
    rows=records();rows[4]['observation_ids']=['unknown']
    rows.append({'kind':'message','id':'M','case_id':'CASE-demo','role':'user','content':'이것은 정상입니다'})
    v=make(rows)
    assert v['objects']['claim:C1']['validity']=='unresolved_references'
    assert v['summary']['adopted_claims']==0
    assert not v['narrative'][0]['refs']


@pytest.mark.parametrize('eligible,job,status,expected',[(True,None,'reserved','candidate'),
    (False,None,'blocked','blocked'),(True,{'status':'submitted'},'reserved','queued'),
    (True,{'status':'submitted','worker_status':'running'},'reserved','running'),
    (True,{'status':'ingested','result_status':'covered'},'complete','succeeded'),
    (True,{'status':'ingested','result_status':'partial'},'partial','partial'),
    (True,{'status':'ingested','result_status':'failed'},'complete','failed')])
def test_execution_and_discrimination_are_separate(eligible,job,status,expected):
    rows=records();rows[-1]['admission']['eligible']=eligible;rows[-1]['status']=status
    if job:rows.append({'id':'JOB1','kind':'investigation_job','case_id':'CASE-demo','task_id':'T1',
                       'evidence_id':'E1','test_intent_ids':['TEST1'],'created_at':'2026-01-01',**job})
    v=make(rows);t=v['objects']['test:TEST1']
    assert t['execution']==expected and t['discrimination']=='unassessed'
    assert v['summary']['unassessed_tests']==1


def test_same_physical_result_is_not_independent_evidence_or_new_execution():
    rows=records();rows.append({'kind':'test_result_use','id':'U1','case_id':'CASE-demo',
        'test_intent_id':'TEST1','job_id':'JOB1','original_observation_ids':['O1'],
        'new_execution':False,'independent_evidence':False})
    v=make(rows)
    r=next(r for r in v['relations'] if r['kind']=='reuses_physical_result')
    assert not r['new_execution'] and not r['independent_evidence']
    assert v['objects']['observation:O1']['independence']=='미평가'


@pytest.mark.parametrize('raw,basis,known',[
    ('2026-01-02T01:02:03','시간대 미상',False),('Jan 02 01:02:03','연도 미상',False),
    ('2026-01-02T01:02:03Z','',False),('2026-01-02T01:02:03Z','시간대 미상',False),
    ('2026-01-02T01:02:03Z','explicit UTC',True),
    ('2026-01-02T01:02:03+09:00','연도 추정(파일 mtime); 이미지 시간대',True)])
def test_time_comparability(raw,basis,known):
    row=records()[3];row['timestamp']=raw;row['fields']['time_basis']=basis
    t=time_assertions(row)[0]
    assert t['comparable'] is known
    assert (bool(t['normalized_ns'])) is known
    assert t['meaning']=='기록 시각'


def test_multiple_candidates_not_continuous_interval_and_file_time_not_action():
    row=records()[3];row['fields']['time_record']={'candidates':['2025-01-01T00:00:00Z','2026-01-01T00:00:00Z']}
    t=time_assertions(row)[0];assert t['shape']=='candidates' and len(t['normalized_ns'])==2
    row['fields']['file_context']={'ctime_ns':1700000000123456789}
    t=time_assertions(row)[1];assert t['lane']=='file' and t['shape']=='point' and 'ctime' in t['meaning']
    assert '1700000000123456789' in t['normalized_ns']
    assert '생성·실행 시각이 아니' in t['limitation']


def test_unknown_and_collection_time_are_retained_off_axis():
    row=records()[3];row['timestamp']=None
    assert time_assertions(row)[0]['shape']=='unknown'
    row['timestamp']='2026-01-01T00:00:00Z';row['fields']['time_kind']='collected'
    assert not time_assertions(row)[0]['comparable']


def test_report_manifest_scope_and_missing_report():
    assert make()['reports']==[]
    rows=records();rows.append({'kind':'report','id':'R','case_id':'CASE-demo','report_id':'REPORT-demo',
        'snapshot':{'scope_revision':8},'distributed_files':{'executive.html':'abc'}})
    r=make(rows)['reports'][0]
    assert r['freshness']=='older_scope' and r['correction_impact']=='possible'
    assert r['claim_versions'] is None


def test_high_visibility_stage_is_not_free_model_statement():
    rows=records();source=rows[3]
    source['fields']['command']='query status'
    rows.append({'kind':'dossier','id':'D','case_id':'CASE-demo','task_id':'T1','evidence_id':'E1',
        'status':'reviewed','finding':{'title':'Product attack confirmed','card_summary':'Actor definitely succeeded',
            'observation_ids':['O1'],'timeline_role':'핵심','reason':'Compare execution with normal maintenance.',
            'fact_assertions':[{'observation_id':'O1','pointer':'/fields/command','operator':'equals','value':'query status'}],
            'stages':[{'stage':'execution','statement':'Product attack confirmed','observation_ids':['O1']}],
            'incident_relevance':{'level':'direct','reason':'This recorded command is relevant, success unverified.'}}})
    view=validate(make(rows))
    stage=view['objects']['claim:D:stage:0']
    assert 'Product attack confirmed' not in stage['title']
    assert stage['interpretation']=='Product attack confirmed'
    assert 'query status' in view['narrative'][0]['text']
    assert view['timeline'][0]['core'] and 'query status' in view['timeline'][0]['title']


def test_report_claim_versions_use_same_canonical_bindings_and_detect_correction():
    from workbench.judgment_snapshot import claim_version
    rows=records();claim=rows[4]
    saved={'claim:C1':claim_version(claim,claim,{'O1':rows[3]})}
    rows.append({'kind':'report','id':'R','case_id':'CASE-demo','report_id':'REPORT-demo',
        'snapshot':{'scope_revision':9,'claim_versions':saved}})
    view=make(rows)
    assert view['objects']['claim:C1']['canonical_version']==saved['claim:C1']
    assert view['reports'][0]['correction_impact']=='none_for_included_claims'
    rows[4]['text']='Corrected narrow description'
    changed=make(rows,sequence=2)['reports'][0]
    assert changed['correction_impact']=='confirmed' and changed['changed_claim_keys']==['claim:C1']


def test_business_questions_group_contexts_without_merging_answers():
    rows=records();rows[5]['business_question_id']='BUSINESS-root'
    rows.append({**rows[5],'id':'Q2','state':'held','status':'held'})
    view=validate(make(rows));q=view['objects']['question:BUSINESS-root']
    assert len(q['contexts'])==2 and q['state']=='contextual'
    assert q['test_keys']==['test:TEST1']
    assert view['summary']['questions']==1


def test_current_hypothesis_requires_full_dependency_verification_and_scoped_sources(tmp_path):
    from workbench.scenarios import dependency
    from scripts.observer_snapshot import scenario_source_current
    rows=records();o=rows[3]
    h={'kind':'hypothesis','id':'H','case_id':'CASE-demo','created_at':'2026-01-01T02:00:00Z',
       'hypothesis_kind':'dynamic','task_id':'T1','evidence_id':'E1','generation':0,
       'title':'Record has an authorized explanation','card_summary':'An explanation, not a verified actor.',
       'supporting_evidence_ids':['O1'],'refuting_evidence_ids':[],
       'scenario_assessment':{'comparison_question':'Was the activity authorized?', 'evidence_fit':'moderate',
            'ranking_reason':'A source-bound interpretation.','alternative_explanation':'An unauthorized activity remains possible.',
            'next_check':'Compare actual approval records.','investigation_priority':'normal','priority_reason':'Distinguish approval.'},
       'scenario_source_revision':dependency([o],{'O1'})}
    view=make(rows+[h]);assert not view['objects']['hypothesis:H']['assessment_current']
    dbpath=tmp_path/'sources.db'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE records(id TEXT,kind TEXT,case_id TEXT,created_at TEXT,body TEXT)')
        for r in rows+[h]:db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(r['id'],r['kind'],r['case_id'],r['created_at'],json.dumps(r)))
    before=hashlib.sha256(dbpath.read_bytes()).hexdigest()
    actual=capture(dbpath,'CASE-demo','run')
    assert actual['objects']['hypothesis:H']['assessment_current']
    assert hashlib.sha256(dbpath.read_bytes()).hexdigest()==before
    # An uncited observation of the same object invalidates the old comparison.
    new={**o,'id':'O2','fields':{**o['fields'],'excerpt':'New contrary context'}}
    with sqlite3.connect(dbpath) as db:
        db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(new['id'],new['kind'],new['case_id'],new['created_at'],json.dumps(new)))
    stale=capture(dbpath,'CASE-demo','run')['objects']['hypothesis:H']
    assert not stale['assessment_current'] and stale['freshness']=='dependency_changed'
    assert stale['evidence_fit'] is None and stale['validity']=='historical_assessment'
    assert stale['historical_reason']==h['scenario_assessment']['ranking_reason']
    assert stale['historical_next_discriminator']==h['scenario_assessment']['next_check']
    assert stale['ranking_reason'] is None and stale['next_discriminator'] is None


def test_briefing_does_not_create_plans_strength_or_work_from_manual_selection():
    if not NODE:pytest.skip('Node runtime unavailable')
    script=r'''
const assert=require('node:assert/strict'),b=require(process.argv[1]);
const hypothesis=(id,current=true)=>({type:'hypothesis',id,key:'hypothesis:'+id,version:'v-'+id,
  title:id,assessment_current:current,changed_at:'2026-01-01',source_keys:['observation:O1']});
const test=id=>({type:'test',id,key:'test:'+id,question_id:'Q'+id,version:'v'+id,execution:'running',job_ids:['J'+id],
  conditions:{success_condition:'An explicitly recorded supporting condition.',refutation_condition:'A contrary condition.',
    inconclusive_condition:'Incomplete coverage cannot decide.'},request:{reason:'A supplied test purpose.'},
  design:{expected_update:'A supplied expected change.',required_observation_ids:['O1']},assessment_status:'unassessed',result_scope:{complete:false}});
const question=id=>({type:'question',id:'Q'+id,key:'question:Q'+id,hypothesis_keys:['hypothesis:'+id],test_keys:['test:'+id]});
const a=hypothesis('A'),c=hypothesis('B'),x=test('A'),y=test('B'),qa=question('A'),qb=question('B');
const view={case:{},objects:Object.fromEntries([a,c,x,y,qa,qb].map(o=>[o.key,o]))};
const activity={items:[{id:'JA',state:'running',title:'A',purpose:'Actual job purpose.'}]};
const saved=JSON.stringify([view,activity]);
let ctx=b.context({view,current:view,activity,available:true,selectedKey:null});
assert.equal(ctx.active.key,a.key);assert.equal(ctx.primary.key,a.key);assert.equal(ctx.test.key,x.key);
ctx=b.context({view,current:view,activity,available:true,selectedKey:c.key});
assert.equal(ctx.active.key,a.key);assert.equal(ctx.primary.key,c.key);assert.equal(ctx.test.key,y.key);
assert.equal(ctx.real.id,'JA'); // Looking at B never changes the engine's active A.
const plan=b.testExplanation(ctx.test);
assert.equal(plan.purpose,y.request.reason);assert.equal(plan.outcomes[0].text,y.conditions.success_condition);
assert.equal(plan.outcomes[1].text,y.conditions.refutation_condition);assert.equal(plan.outcomes[2].text,y.conditions.inconclusive_condition);
assert.equal(plan.assessed,false);assert.equal(plan.incomplete,true);
assert.equal(JSON.stringify([view,activity]),saved);
ctx=b.context({view,current:{...view,case:{execution_end:{status:'resource_limit'}}},activity,available:true});
assert.equal(ctx.active,undefined);assert.equal(ctx.real,null);assert.match(b.testExplanation(x,{ended:true}).status,/지금은 진행하지/);
ctx=b.context({view,current:{...view,case:{status:'resource_limit'}},activity,available:true});
assert.equal(ctx.ended,true);assert.equal(ctx.real,null); // Missing end time is not live execution.
ctx=b.context({view,current:view,activity,available:true,pinned:true});assert.equal(ctx.active,false);
ctx=b.context({view,current:view,activity,available:false});assert.equal(ctx.real,null);assert.equal(ctx.hypotheses.length,0);
a.assessment_current=false;ctx=b.context({view,current:view,activity,available:true});assert.equal(ctx.active,undefined);
const missing=hypothesis('unlinked');assert.equal(b.linkedTests(missing,view).length,0);
const unrelated={...x,key:'test:OTHER',design:{required_observation_ids:['other-file']}};
const wideQuestion={...qa,test_keys:[x.key,unrelated.key]};
const wideView={...view,objects:{...view.objects,[qa.key]:wideQuestion,[unrelated.key]:unrelated}};
assert.deepEqual(b.linkedTests(a,wideView).map(t=>t.key),[x.key]);
assert.equal(b.testExplanation(null).outcomes.length,0);
assert.equal(b.testExplanation({...x,conditions:{}}).outcomes.length,0);
const claim={type:'claim',key:'claim:C',validity:'adopted',source_keys:['observation:O1'],
  title:'Malware definitely executed by a person.',interpretation:'An invented product identity.',
  display_binding:{status:'bound',fact:{pointer:'/fields/command',value:'<img src="https://invalid.example">'}}};
assert.equal(b.factText(claim),'기록에 명령 문자열 ‘<img src="https://invalid.example">’가 남아 있어요.');
assert.equal(b.factText(claim).includes('definitely'),false);
assert.equal(b.claimFor(a,{objects:{[claim.key]:claim}}),claim);
assert.equal(b.claimFor(missing,{objects:{[claim.key]:{...claim,source_keys:['observation:OTHER']}}}),null);
'''
    result=subprocess.run([NODE,'-e',script,str(ROOT/'ui/observer/briefing.js')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_main_scene_keeps_detailed_questions_and_work_in_explicit_dialogs():
    import re
    html=(ROOT/'ui/observer/index.html').read_text()
    main=html.split('</main>')[0]
    assert 'id="hypothesis-bubbles"' in main and 'id="guide-work"' in main
    assert 'id="questions"' not in main and 'id="activity"' not in main
    assert 'id="inspection-dialog"' in html and 'id="work-dialog"' in html
    assert '/briefing.js' in html
    identifiers=re.findall(r'\bid="([^"]+)"',html)
    assert len(identifiers)==len(set(identifiers))
    js=(ROOT/'ui/observer/observer.js').read_text()
    assert '원문으로 확인한 좁은 사실' not in js and '좁은 주장' not in js
    assert '이렇게 생각하나요?' in js
    for control in ('explanation-older','explanation-newer','explanation-current','explanation-position'):
        assert 'id="'+control+'"' in main
    assert 'explanations.slice(0,3)' not in js
    assert 'thought hypothesis ' in js


def history_hypothesis():
    scope={'task_id':'T1','evidence_id':'E1','generation':0}
    revisions=[{'revision':i,'at':f'2026-01-01T0{i}:00:00Z',**scope,
        'title':f'Explanation {i}', 'card_summary':f'Recorded interpretation {i}',
        'reasoning':f'Public supporting explanation {i}', 'observation_ids':['O1'],
        'judgment':'미확인','lifecycle':'investigating',
        'raw_output':'DO NOT EXPORT MODEL OUTPUT', 'origin_key':'DO NOT EXPORT INTERNAL KEY',
        'scenario_assessment':{'comparison_question':'Was it approved?',
            'alternative_explanation':'Another interpretation is possible.',
            'next_check':'A proposed check, not a scheduled job.'}} for i in (1,2)]
    return {'kind':'hypothesis','id':'H','case_id':'CASE-demo',**scope,
        'created_at':'2026-01-01T01:00:00Z','updated_at':'2026-01-01T02:00:00Z',
        'hypothesis_kind':'dynamic','title':'Explanation 2','revision':2,'revision_history':revisions,
        'observation_ids':['O1'],'supporting_evidence_ids':['O1'],'refuting_evidence_ids':[]}


def test_explanation_history_is_explicit_versioned_read_only_and_not_a_past_source_certificate(tmp_path):
    rows=records()+[history_hypothesis()];dbpath=tmp_path/'history.db'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE records(id TEXT,kind TEXT,case_id TEXT,created_at TEXT,body TEXT)')
        for r in rows:db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(r['id'],r['kind'],r['case_id'],r['created_at'],json.dumps(r)))
    view=capture(dbpath,'CASE-demo','test-run');rev=view['envelope']['projection_revision']
    h=view['objects']['hypothesis:H'];manifest=h['explanation_history']
    assert manifest['count']==2 and [e['revision'] for e in manifest['entries']]==[1,2]
    assert 'Public supporting explanation 1' not in json.dumps(view)
    before=hashlib.sha256(dbpath.read_bytes()).hexdigest()
    with TestClient(server(tmp_path,[view],source_database=dbpath)) as c:
        path='/api/explanations/'+rev+'/H/1'
        response=c.get(path);assert response.status_code==200
        data=response.json();entry=data['entry']
        assert entry['reason']=='Public supporting explanation 1'
        assert entry['historical'] and entry['source_freshness']=='not_certified_at_historical_revision'
        assert data['owner_ref']=={'key':h['key'],'version':h['version']}
        assert data['source_refs']==[{'key':'observation:O1','version':view['objects']['observation:O1']['version']}]
        assert data['source_reference_scope']=='requested_snapshot_not_historical_source_versions'
        assert 'DO NOT EXPORT' not in response.text
        for suffix in ('/foreign/1','/H/0','/H/3'):
            assert c.get('/api/explanations/'+rev+suffix).status_code==404
        assert c.post(path).status_code==405
        assert c.get(path,headers={'Origin':'https://invalid.example'}).status_code==403
        assert hashlib.sha256(dbpath.read_bytes()).hexdigest()==before
        modified=copy.deepcopy(rows[-1]);modified['revision_history'][0]['reasoning']='Corrected explanation'
        with sqlite3.connect(dbpath) as db:db.execute('UPDATE records SET body=? WHERE id=?',(json.dumps(modified),'H'))
        assert c.get(path).status_code==409 # Never substitute an incompatible newer history.
    with TestClient(server(tmp_path,[make(rows)] ,source_database=dbpath)) as c:
        assert c.get('/api/explanations/'+make(rows)['envelope']['projection_revision']+'/H/1').status_code==404


def test_explanation_history_rejects_duplicate_or_invalid_revisions():
    from workbench.observer_history import explanation_manifest
    h=history_hypothesis();h['revision_history'].append(copy.deepcopy(h['revision_history'][0]))
    with pytest.raises(ValueError,match='Invalid explanation revision'):explanation_manifest(h)
    h=history_hypothesis();h['revision_history'][0]['revision']=True
    with pytest.raises(ValueError,match='Invalid explanation revision'):explanation_manifest(h)
    h=history_hypothesis();h['revision_history'][0]['evidence_id']='foreign'
    with pytest.raises(ValueError,match='Cross-scope'):explanation_manifest(h)
    h=history_hypothesis();h['revision_history'][0].update(task_id='previous-task',generation=7)
    from workbench.observer_history import explanation_entries
    assert explanation_manifest(h)['count']==2
    previous=explanation_entries(h)[0]
    assert previous['task_id']=='previous-task' and previous['generation']==7
    assert explanation_manifest({'id':'empty'})['count']==0


def test_one_explanation_at_a_time_and_history_selection_survives_new_judgment():
    if not NODE:pytest.skip('Node runtime unavailable')
    script=r'''
const assert=require('node:assert/strict'),{ExplanationHistory}=require(process.argv[1]);
const v={envelope:{case_id:'C',run_id:'R',data_mode:'replay',source_binding:'binding',projection_revision:'S'},objects:{}};
const h={key:'hypothesis:H',id:'H',version:'owner2',changed_at:'2026-01-01T00:30:00Z',ledger_revision:2,
  explanation_history:{version:'history2',count:2,entries:[{revision:1,at:'2026-01-01T01:00:00Z',version:'entry1'},
    {revision:2,at:'2026-01-01T02:00:00Z',version:'entry2'}]}};
const other={key:'hypothesis:B',id:'B',version:'B1',changed_at:'2026-01-01T00:00:00Z'};
v.objects[h.key]=h;v.objects[other.key]=other;
const saved=JSON.stringify(v),history=new ExplanationHistory();
history.update(v,[h,other],h);assert.equal(history.selected.key,h.key);assert.equal(history.past,false);
assert.equal(history.selected.at,'2026-01-01T02:00:00Z'); // Revision time, not initial object creation.
assert.equal(history.items.length,3);const previous=history.move(1);
assert.equal(history.selected.revision,1);assert.equal(history.selected.archived,true);assert.equal(history.past,true);
const payload={envelope:v.envelope,owner_ref:{key:h.key,version:h.version},history_version:'history2',
  entry:{hypothesis_id:'H',revision:1,version:'entry1',historical:true,reason:'Public saved reason'},source_refs:[]};
assert.throws(()=>history.accept(previous,{...payload,owner_ref:{key:'wrong',version:h.version}}));
assert.throws(()=>history.accept(previous,{...payload,envelope:{...v.envelope,run_id:'other'}}));
assert.throws(()=>history.accept(previous,{...payload,source_refs:[{key:'missing',version:'x'}]}));
history.accept(previous,payload);assert.equal(history.loaded().entry.reason,'Public saved reason');
history.update(v,[h,other],h);assert.equal(history.selected.token,previous.token);assert.equal(history.items.length,3);
const newer={...h,version:'owner3',ledger_revision:3,changed_at:'2026-01-01T03:00:00Z',
  explanation_history:{version:'history3',count:3,entries:[...h.explanation_history.entries,
    {revision:3,at:'2026-01-01T03:00:00Z',version:'entry3'}]}};
history.update({...v,objects:{...v.objects,[h.key]:newer}},[newer,other],newer);
assert.equal(history.selected.token,previous.token);assert.equal(history.selected.revision,1);
assert.equal(history.loaded().entry.reason,'Public saved reason'); // No forced jump or replay celebration.
assert.equal(history.move(-1).revision,2);assert.equal(history.move(-1).revision,3);assert.equal(history.past,false);
history.move(1);history.update(v,[other],other);assert.equal(history.selected.removed,true);
assert.equal(history.past,true);history.latest();assert.equal(history.selected.key,other.key);
assert.throws(()=>history.update({...v,envelope:{...v.envelope,case_id:'foreign'}},[other],other));
assert.equal(JSON.stringify(v),saved); // View navigation never mutates ledger/schedules.
const empty=new ExplanationHistory();empty.update(v,[],null);assert.equal(empty.move(1),null);
const single=new ExplanationHistory();single.update(v,[other],other);single.move(1);assert.equal(single.past,false);
'''
    result=subprocess.run([NODE,'-e',script,str(ROOT/'ui/observer/briefing.js')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_full_source_is_versioned_read_only_paginated_and_never_an_arbitrary_file(tmp_path):
    rows=records();rows[3]['fields']['excerpt']='<script>untrusted()</script>'+'x'*13000
    dbpath=tmp_path/'source.db'
    with sqlite3.connect(dbpath) as db:
        db.execute('CREATE TABLE records(id TEXT,kind TEXT,case_id TEXT,created_at TEXT,body TEXT)')
        for r in rows:db.execute('INSERT INTO records VALUES(?,?,?,?,?)',(r['id'],r['kind'],r['case_id'],r['created_at'],json.dumps(r)))
    view=capture(dbpath,'CASE-demo','test-run');revision=view['envelope']['projection_revision']
    before=hashlib.sha256(dbpath.read_bytes()).hexdigest()
    with TestClient(server(tmp_path,[view],source_database=dbpath)) as c:
        path='/api/sources/'+revision+'/O1'
        a=c.get(path+'?limit=12000').json();b=c.get(path+'?offset=12000&limit=12000').json()
        assert a['text']+b['text']==rows[3]['fields']['excerpt'] and b['next_offset'] is None
        assert c.get('/api/sources/'+revision+'/foreign').status_code==404
        assert c.get(path+'?offset=-1').status_code==400
        assert c.get(path+'?limit=65537').status_code==400
        assert hashlib.sha256(dbpath.read_bytes()).hexdigest()==before
        with sqlite3.connect(dbpath) as db:
            row={**rows[3],'fields':{'excerpt':'corrected'}}
            db.execute('UPDATE records SET body=? WHERE id=?',(json.dumps(row),'O1'))
        assert c.get(path).status_code==409


def test_capture_is_read_only_consistent_and_excludes_prompts(tmp_path):
    database=tmp_path/'source.sqlite3'
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE records(id TEXT, kind TEXT, case_id TEXT, created_at TEXT, body TEXT)')
        rows=records();rows.append({'id':'INPUT','kind':'review_input','case_id':'CASE-demo','task_id':'T1',
                                   'created_at':'2026-01-01','pack':{'secret_prompt':'DO NOT EXPORT'}})
        rows[3]['fields']['excerpt']='x'*13000
        for r in rows:c.execute('INSERT INTO records VALUES(?,?,?,?,?)',(r['id'],r['kind'],r['case_id'],r.get('created_at'),json.dumps(r)))
    before=hashlib.sha256(database.read_bytes()).hexdigest()
    view=capture(database,'CASE-demo','run')
    assert view['envelope']['data_mode']=='replay'
    assert 'DO NOT EXPORT' not in json.dumps(view)
    o=view['objects']['observation:O1'];assert o['excerpt_partial'] and len(o['excerpt'])==12000
    assert hashlib.sha256(database.read_bytes()).hexdigest()==before
    validate(view)


def server(tmp_path, views=None, **kwargs):
    files=[]
    for i,v in enumerate(views or [make()]):
        p=tmp_path/f'view{i}.json';p.write_text(json.dumps(v));files.append(p)
    return create_app(files,**kwargs)


def test_server_has_no_write_or_model_routes_and_no_external_resources(tmp_path):
    with TestClient(server(tmp_path)) as c:
        s=c.get('/api/session');assert s.status_code==200
        assert s.json()['automatic_model_calls']==0
        rev=s.json()['latest']['projection_revision']
        assert c.get('/api/snapshots/'+rev).status_code==200
        for path in ['/api/cases/C/start','/api/chat','/api/model','/api/session']:
            assert c.post(path,json={'prompt':'x'}).status_code==405
        assert c.get('/api/session',headers={'Origin':'https://invalid.example'}).status_code==403
        assert c.get('/api/session',headers={'Host':'invalid.example'}).status_code==400
        assert c.get('/source.sqlite3').status_code==404
        assert c.get('/api/reports/R/executive.html').status_code==404
        html=c.get('/');assert html.status_code==200
        assert "default-src 'none'" in html.headers['content-security-policy']
        assert "form-action 'none'" in html.headers['content-security-policy']
    js=(ROOT/'ui/observer/observer.js').read_text()
    assert 'innerHTML' not in js and 'insertAdjacentHTML' not in js and 'eval(' not in js
    assert 'http://' not in js and 'https://' not in js


def test_mixed_runs_and_corrupt_snapshots_are_not_served(tmp_path):
    a=make();b=make(sequence=2);b['envelope']['run_id']='other'
    with pytest.raises(ValueError,match='Mixed'):server(tmp_path,[a,b])
    a['objects']['claim:C1']['title']='tampered'
    with pytest.raises(ValueError,match='hash'):server(tmp_path,[a])


def test_verified_report_download_is_attachment_never_preview_or_example_export(tmp_path):
    root=tmp_path/'reports';dest=root/'REPORT-demo';dest.mkdir(parents=True)
    data=b'<script>alert(1)</script>';(dest/'analyst.html').write_bytes(data)
    rows=records();rows.append({'kind':'report','id':'R','case_id':'CASE-demo','report_id':'REPORT-demo',
        'snapshot':{'scope_revision':8},'distributed_files':{'analyst.html':hashlib.sha256(data).hexdigest()}})
    with TestClient(server(tmp_path,[make(rows,mode='replay')],report_root=root)) as c:
        r=c.get('/api/reports/R/analyst.html');assert r.content==data and r.status_code==200
        assert 'attachment' in r.headers['content-disposition']
        (dest/'analyst.html').write_bytes(b'changed')
        assert c.get('/api/reports/R/analyst.html').status_code==409
        assert c.get('/api/reports/R/report.json').status_code==404
    with TestClient(server(tmp_path,[make(rows)],report_root=root)) as c:
        assert c.get('/api/reports/R/analyst.html').status_code==404


def test_client_atomic_correction_reconnect_order_selection_and_pinning():
    if not NODE:pytest.skip('Node runtime unavailable')
    a=make();rows=records();rows[4]['text']='정정: 기록상 문자열만 확인';b=make(rows,2);c=make(rows,4)
    script=r'''
const assert=require('node:assert/strict'), {ViewState}=require(process.argv[1]);
const [a,b,c]=JSON.parse(process.argv[2]);
const s=new ViewState();assert.equal(s.accept(a),true);assert.equal(s.changes.length,0);
s.select('claim:C1');s.pin(true);assert.equal(s.accept(a),false);
assert.equal(s.accept(c),false);assert.equal(s.current.envelope.sequence,1);assert.equal(s.sync,'resync_required');
assert.equal(s.accept(b),true);assert.equal(s.selection.key,'claim:C1');assert.equal(s.pinnedChanged(),true);
assert.equal(s.displayed.objects['claim:C1'].title,a.objects['claim:C1'].title);
s.pin(false);assert.equal(s.narratives()[0].text,b.objects['claim:C1'].statement);
assert.equal(s.accept(a),false);assert.equal(s.current.envelope.sequence,2);
assert.equal(s.accept(c,{resync:true}),true);assert.equal(s.current.envelope.sequence,4);
const e={id:'event1',case_id:'CASE-demo',run_id:'test-run',data_mode:'example',sequence:5,refs:[{key:'claim:C1',version:c.objects['claim:C1'].version}]};
assert.throws(()=>s.invalidate({...e,data_mode:'replay'}),/범위 불일치/);
assert.throws(()=>s.invalidate({...e,refs:[null]}),/범위 불일치/);
assert.equal(s.invalidate(e),true);assert.equal(s.invalidate(e),false);assert.equal(s.narratives().length,0);
assert.equal(s.accept(c,{resync:true}),false);assert.equal(s.sync,'resync_required');
assert.throws(()=>s.accept({...b,envelope:{...b.envelope,run_id:'mixed'}}),/혼입/);
const bad=JSON.parse(JSON.stringify(c));bad.objects['claim:C1'].refs[0].version='bad';
assert.throws(()=>s.accept(bad),/참조 버전/);
'''
    result=subprocess.run([NODE,'-e',script,str(ROOT/'ui/observer/state.js'),json.dumps([a,b,c])],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def live_rows():
    rows = records()
    rows[0]['runtime_binding'] = {'fingerprint': 'fixture-binding'}
    return rows


def live_source(tmp_path, loader=None, clock=None):
    return LiveObserver(loader or (lambda seq: make(live_rows(), seq, mode='live')),
                        case_id='CASE-demo', run_id='test-run', source_binding='fixture-binding',
                        cache_dir=tmp_path/'observer-cache', **({'clock': clock} if clock else {}))


def test_live_identity_cannot_be_invented_or_mixed(tmp_path):
    with pytest.raises(ValueError, match='binding'):
        make(mode='live')
    s = live_source(tmp_path)
    assert s.sample()
    old = s.session()['latest']
    s.capture = lambda seq: {**make(live_rows(), seq, mode='live'), 'envelope': {
        **make(live_rows(), seq, mode='live')['envelope'], 'source_binding': 'another'}}
    assert not s.sample()
    status = s.session()
    assert status['source_status']=='stale' and status['latest']==old
    assert s.error=='ValueError'


def test_live_activity_has_its_own_clock_without_replacing_judgment(tmp_path):
    rows = live_rows()
    counter = [0]
    def loader(seq):
        counter[0] += 1
        return project(rows, case_id='CASE-demo', run_id='test-run', data_mode='live',
                       captured_at=f'2026-01-01T03:00:{counter[0]:02d}Z', ledger_position={}, sequence=seq)
    s = live_source(tmp_path, loader)
    assert s.sample()
    a = s.session()
    assert s.sample()
    b = s.session()
    assert b['latest']==a['latest']
    assert b['activity']['checked_at']!=a['activity']['checked_at']
    rows[4]['text']='정정된 좁은 주장'
    assert s.sample()
    assert s.session()['latest']['sequence']==a['latest']['sequence']+1
    assert len(s.all_views())==2
    restart = live_source(tmp_path)
    assert restart.sample()
    assert restart.session()['latest']['sequence']>s.session()['latest']['sequence']


def test_live_stale_source_and_bounded_cache(tmp_path):
    clock=[10]
    rows=live_rows()
    s=live_source(tmp_path, lambda seq: make(rows,seq,mode='live'), clock=lambda:clock[0])
    for i in range(10):
        rows[4]['text']=str(i)
        assert s.sample()
    assert len(s.all_views())==6
    assert s.session()['source_status']=='observed'
    clock[0]+=66
    assert s.session()['source_status']=='stale'
    with pytest.raises(ValueError,match='different source'):
        LiveObserver(s.capture,case_id='wrong',run_id='test-run',source_binding='fixture-binding',cache_dir=s.cache)


def test_input_record_is_not_proof_of_request_dispatch_or_adoption():
    rows=records()
    rows += [{'kind': 'review_input','id':'I','case_id':'CASE-demo','task_id':'T1'},
             {'kind': 'model_reservation','id':'M','case_id':'CASE-demo','task_id':'T1','status':'received'}]
    activity={a['id']:a for a in make(rows)['activity']['items']}
    assert activity['I']['state']=='input_registered'
    assert activity['M']['state']=='received'
    assert activity['M']['result_adopted'] is None


def test_live_server_reads_do_not_sample_per_request_or_offer_controls(tmp_path):
    s=live_source(tmp_path)
    s.sample()
    calls=[]
    s.start=lambda: calls.append('start')
    s.close=lambda: calls.append('close')
    with TestClient(create_app(live_source=s)) as client:
        for _ in range(4):
            status=client.get('/api/session').json()
            assert status['mode']=='read_only_live'
            assert client.get('/api/snapshots/'+status['latest']['projection_revision']).status_code==200
            assert client.post('/api/chat').status_code==405
        assert len(s.costs)==1
        assert client.get('/api/snapshots/expired').status_code==404
    assert calls==['start','close']


def test_live_pending_snapshot_restricts_narrative_and_binds_activity():
    if not NODE:pytest.skip('Node runtime unavailable')
    a=make(live_rows(),mode='live'); rows=live_rows();rows[4]['text']='changed';b=make(rows,2,mode='live')
    script=r'''
const assert=require('node:assert/strict'),{ViewState}=require(process.argv[1]);
const [a,b]=JSON.parse(process.argv[2]),s=new ViewState();s.accept(a);s.select('claim:C1');s.pin(true);
s.beginUpdate(b.envelope);assert.equal(s.narratives().length,0);assert.equal(s.displayed,a);
s.accept(b,{resync:true});assert.equal(s.pinnedChanged(),true);assert.equal(s.selection.key,'claim:C1');
s.acceptActivity({...b.activity,...b.envelope,checked_at:'2026-01-02'});
assert.throws(()=>s.acceptActivity({...b.activity,...b.envelope,checked_at:'2026-01-01'}),/이전 작업/);
assert.throws(()=>s.acceptActivity({...b.activity,...b.envelope,run_id:'wrong'}),/범위 불일치/);
assert.throws(()=>s.beginUpdate({...b.envelope,source_binding:'wrong'}),/혼입/);
'''
    result=subprocess.run([NODE,'-e',script,str(ROOT/'ui/observer/state.js'),json.dumps([a,b])],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr


def test_optional_renderer_receives_no_evidence_stops_hidden_and_falls_back():
    if not NODE:pytest.skip('Node runtime unavailable')
    script=r'''
const assert=require('node:assert/strict'),{RendererHost}=require(process.argv[1]);
let last,paused,destroyed=false,status;
const host=new RendererHost(x=>status=x);
host.attach({update:x=>last=x,pause:x=>paused=x,destroy:()=>destroyed=true});
host.update({base:'working',transient:'celebrate_damage',severity:'warning',secret:'raw evidence'});
assert.deepEqual(Object.keys(last).sort(),['base','motionAllowed','severity','transient']);
assert.equal(last.motionAllowed,false);assert.equal(last.transient,'none');
host.preferences({motion:true});assert.equal(last.motionAllowed,true);
host.preferences({reduced:true});assert.equal(paused,true);
host.preferences({reduced:false,visible:false});assert.equal(paused,true);
host.preferences({visible:true});assert.equal(last.motionAllowed,true);
host.attach({update:()=>{throw Error('renderer failed')},pause:()=>{},destroy:()=>{}});
assert.equal(destroyed,true);assert.equal(status.available,false);assert.equal(status.failed,true);
'''
    result=subprocess.run([NODE,'-e',script,str(ROOT/'ui/observer/renderer.js')],capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
