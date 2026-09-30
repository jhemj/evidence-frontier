"""Zero-model U1/L1 projection and browser-contract regressions."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from workbench.observer_view import digest, project, validate
from workbench.observer_activity import lifecycle_activities

ROOT = Path(__file__).resolve().parents[1]


def base():
    def row(kind, identity, **values):
        return dict(kind=kind, id=identity, case_id='CASE-fixture',
                    created_at='2026-01-01T00:00:00Z', **values)
    rows = [row('case', 'CASE-fixture', status='running'), row('evidence', 'E', connected=True),
        row('task', 'T', evidence_id='E', retry_generation=0, status='running'),
        row('observation', 'O', evidence_id='E', timestamp='2026-01-01T01:00:00Z',
            fields={'command': 'inspect --result fixture', 'path': '/fixture/source', 'time_basis': 'explicit UTC'}),
        row('claim', 'C', task_id='T', evidence_id='E', generation=0, status='approved',
            title='Fixture result string', text='A result query is recorded; success is unknown.',
            observation_ids=['O'], fact_assertions=[{'observation_id':'O', 'pointer':'/fields/command',
                'operator':'equals', 'value':'inspect --result fixture'}]),
        row('hypothesis', 'H', task_id='T', evidence_id='E', generation=0,
            hypothesis_kind='dynamic', revision=1, title='Was fixture work successful?',
            supporting_evidence_ids=['O'], refuting_evidence_ids=[], _scenario_source_current=True,
            scenario_assessment={'comparison_question':'Did fixture work succeed?', 'evidence_fit':'limited',
                'ranking_reason':'Query text alone cannot establish successful execution.',
                'alternative_explanation':'The query could fail.', 'next_check':'Compare retained outcomes.',
                'investigation_priority':'normal', 'priority_reason':'Distinguish recorded query from result.'}),
        row('test_intent', 'X', question_id='Q', tool='search', status='reserved',
            scope={'task_id':'T', 'evidence_id':'E', 'generation':0, 'request':{'query':'inert fixture'}},
            admission={'eligible':True}, conditions={'success_condition':'A retained successful outcome.'})]
    return rows


def view(rows):
    return validate(project(rows, case_id='CASE-fixture', run_id='fixture', data_mode='example',
        captured_at='2026-01-01T02:00:00Z', ledger_position={}))


def connected():
    rows = base()
    initial = view(rows)
    c = initial['objects']['claim:C']
    scope = {'task_id':'T', 'evidence_id':'E', 'generation':0, 'observation_ids':['O'],
             'proposition':'A result query is recorded; success is unknown.'}
    ref = {'kind':'claim', 'id':'C', 'version':c['canonical_version']}
    rows[-1]['scope'].update(semantic_target_ref=ref, semantic_target_scope=scope)
    rows.append(dict(kind='explanation_relation', id='LINK', case_id='CASE-fixture',
        contract='explicit-explains-1', relation='explains', hypothesis_id='H', hypothesis_revision=1,
        task_id='T', evidence_id='E', generation=0, claim_ref=ref, target_scope=scope,
        rationale='The query is why the outcome remains a question.'))
    return rows


def test_explicit_semantic_refs_resolve_after_object_versions_without_cycles():
    v = view(connected())
    links = [r for r in v['relations'] if r['kind'] in ('explains', 'discriminates')]
    assert {r['kind'] for r in links} == {'explains', 'discriminates'}
    for r in links:
        assert r['from_ref']['version'] == v['objects'][r['from']]['version']
        assert r['to_ref']['version'] == v['objects'][r['to']]['version']
    plain = view(base())
    # Adding display relations does not mutate adopted canonical objects.
    for key in ('claim:C', 'hypothesis:H'):
        assert plain['objects'][key]['version'] == v['objects'][key]['version']


@pytest.mark.parametrize('alter', ['revision', 'version', 'scope', 'withdrawn', 'stale'])
def test_no_semantic_fallback_on_shared_source_after_correction(alter):
    rows = connected()
    if alter == 'revision': rows[5]['revision'] = 2
    if alter == 'version': rows[-1]['claim_ref'] = {**rows[-1]['claim_ref'], 'version':'f'*64}
    if alter == 'scope': rows[-1]['target_scope'] = {**rows[-1]['target_scope'], 'evidence_id':'foreign'}
    if alter == 'withdrawn': rows[4]['status'] = 'retracted'
    if alter == 'stale': rows[5]['_scenario_source_current'] = False
    assert not any(r['kind'] == 'explains' for r in view(rows)['relations'])


def test_timeline_uses_same_representative_for_title_reason_and_click():
    rows = base()
    rows[4]['incident_relevance'] = {'level':'background', 'reason':'Background reason.'}
    other = deepcopy(rows[4]); other.update(id='C2', timeline_role='핵심',
        incident_relevance={'level':'direct', 'reason':'A retained query is the clue, not success.'})
    rows.insert(5, other)
    v = view(rows); t = v['timeline'][0]
    assert t['representative_claim_ref']['key'] == 'claim:C2'
    assert t['claim_refs'][0]['key'] == 'claim:C'
    assert t['relevance_reason'] == other['incident_relevance']['reason']
    run_js(r"""
const assert=require('node:assert/strict'),b=require(process.argv[1]),v=JSON.parse(process.argv[2]),t=v.timeline[0];
const bundle=b.timelineBundle(t,v);
assert.equal(bundle.claim.key,'claim:C2');assert.equal(bundle.target,'claim:C2');
assert.equal(bundle.reason,t.relevance_reason);
const legacy=b.timelineBundle({...t,representative_claim_ref:null},v);
assert.equal(legacy.claim,null);assert.equal(legacy.target,t.source_ref.key);assert.equal(legacy.reason,null);
""", 'briefing.js', v)


def run_js(code, filename, value):
    node = os.environ.get('FRONTIER_TEST_NODE')
    assert node, 'Explicit Node runtime is required for browser contracts'
    p = subprocess.run([node, '-e', code, str(ROOT/'ui/observer'/filename), json.dumps(value)],
        capture_output=True, text=True, timeout=15)
    assert p.returncode == 0, p.stderr


def test_one_bundle_keeps_exact_fact_hypothesis_test_and_missing_links():
    v = view(connected())
    run_js(r"""
const a=require('node:assert/strict'),b=require(process.argv[1]),v=JSON.parse(process.argv[2]),h=v.objects['hypothesis:H'];
const x=b.composeBundle(h,v);a.equal(x.claim.key,'claim:C');a.equal(x.test.key,'test:X');
a.equal(x.snapshot,v.envelope.projection_revision);a.ok(x.fact.includes('inspect --result'));
a.equal(x.nextStatus,'확인 후보 · 아직 시작하지 않았어요');
const gap=b.composeBundle(h,{...v,relations:[]});a.equal(gap.claim,null);a.equal(gap.test,undefined);
a.ok(gap.linkageGap);a.ok(gap.nextGap);a.ok(!gap.fact);
const past=b.composeBundle(h,v,{historical:true});a.equal(past.claim,null);a.equal(past.test,null);
""", 'briefing.js', v)


def lifecycle(identity, inp, phases, *, retry=None, accepted=()):
    owner = {'kind':'dossier', 'id':'D', 'version':'b'*64}
    result = []
    for i, phase in enumerate(phases, 1):
        result.append(dict(kind='request_lifecycle', id=identity+str(i), event_id=identity+str(i),
            case_id='CASE-fixture', lifecycle_version='model-request-lifecycle-1', attempt_id=identity,
            seq=i, phase=phase, observed_at=f'2026-01-01T00:00:{i:02}Z',
            task_id='T', generation=0, evidence_id='E', logical_work_id='fixture-work',
            input_record_id='I', input_ref={'kind':'review_input','id':'I','version':digest(inp)},
            reservation_id='I', owner_refs=[owner], affected_refs=[owner],
            delivery_state='response_received' if phase in ('response_received','validating','accepted','partial_accepted','ended') else 'attempted',
            retry_of_attempt_id=retry, accepted_refs=list(accepted) if phase in ('accepted','partial_accepted') else [],
            rejected_refs=[], metadata={'outcome':'accepted'} if phase == 'ended' else {}))
    return result


def lifecycle_input():
    return dict(kind='review_input', id='I', task_id='T', case_id='CASE-fixture', generation=0,
                evidence_id='E', pack={'required_dossiers':[], 'observations':[]})


def project_activity(events, inp):
    return lifecycle_activities(events, [inp], {}, lambda r: r.get('generation') == 0, digest)


def test_request_stage_has_no_invented_generation_and_separate_dispatch_timer():
    inp = lifecycle_input()
    rows = lifecycle('A', inp, ['input_registered','resource_queued','dispatch_attempted','response_waiting'])
    a = project_activity(rows, inp)[0]
    assert a['state'] == 'waiting' and a['phase'] == 'response_waiting'
    assert a['timer_origin'] == 'dispatch' and a['timer_at'] == rows[2]['observed_at']
    assert 'generation' not in a['phase']
    run_js(r"""
const a=require('node:assert/strict'),m=require(process.argv[1]),x=JSON.parse(process.argv[2]);
const queued={id:'tool',kind:'tool',state:'waiting'},validating={...x,id:'V',state:'validating',phase:'validating'};
a.equal(m.currentItem([queued,x]).id,x.id);a.equal(m.currentItem([queued,validating]).id,'V');
const ended=m.activity({case:{status:'paused'}},[x]);a.equal(ended.base,'ended');
""", 'moa.js', a)


@pytest.mark.parametrize('kind', ['review_input', 'synthesis_input', 'falsifier_input'])
def test_read_only_capture_verifies_full_input_version_without_exporting_prompt(tmp_path, kind):
    from scripts.observer_snapshot import capture
    rows = base()
    inp = lifecycle_input()
    inp['kind'] = kind
    inp['pack']['secret_prompt'] = 'PRIVATE INPUT MUST NOT ENTER DISPLAY'
    rows.append(inp)
    events = lifecycle('A', inp, ['input_registered', 'dispatch_attempted', 'response_waiting'])
    for event in events:
        event['input_ref']['kind'] = kind
        event['input_ref']['version'] = digest(inp)
    rows.extend(events)
    path = tmp_path / 'source.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE records(id TEXT, kind TEXT, case_id TEXT, created_at TEXT, body TEXT)')
        for row in rows:
            connection.execute('INSERT INTO records VALUES(?,?,?,?,?)', (
                row['id'], row['kind'], row['case_id'], row.get('created_at'), json.dumps(row)))
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    projected = capture(path, 'CASE-fixture', 'fixture')
    activity = next(a for a in projected['activity']['items'] if a['id'] == 'A')
    assert activity['phase'] == 'response_waiting'
    assert activity['state'] == 'waiting'
    assert 'PRIVATE INPUT MUST NOT ENTER DISPLAY' not in json.dumps(projected)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize('alter', ['duplicate', 'gap', 'input_version', 'mixed_scope'])
def test_incomplete_or_mixed_request_telemetry_is_not_live_stage(alter):
    inp = lifecycle_input(); events = lifecycle('A', inp, ['input_registered','dispatch_attempted','response_waiting'])
    if alter == 'duplicate': events[1]['event_id'] = events[0]['event_id']
    if alter == 'gap': events.pop(1)
    if alter == 'input_version': inp['pack']['observations'] = [{'id':'changed'}]
    if alter == 'mixed_scope': events[1]['evidence_id'] = 'foreign'
    assert project_activity(events, inp) == []


def test_failed_attempt_resolves_only_explicit_same_owner_retry_adoption():
    inp = lifecycle_input()
    failed = lifecycle('A', inp, ['input_registered','dispatch_attempted','failed','ended'])
    retry = lifecycle('B', inp, ['input_registered','response_received','accepted','ended'], retry='A',
                      accepted=[{'kind':'dossier','id':'D','version':'c'*64}])
    rows = project_activity(failed+retry, inp)
    assert rows[0]['failure_impact'] == 'resolved' and rows[0]['resolved_by_attempt_id'] == 'B'
    unrelated = deepcopy(retry)
    for r in unrelated: r['logical_work_id'] = 'different-work'
    assert project_activity(failed+unrelated, inp)[0]['failure_impact'] == 'unresolved'
    wrong_owner = deepcopy(retry)
    wrong_owner[-2]['accepted_refs'][0]['id'] = 'OTHER'
    assert project_activity(failed+wrong_owner, inp)[0]['failure_impact'] == 'unresolved'


def test_incident_correction_enters_change_history_and_reconnect_has_no_discovery():
    run_js(r"""
const a=require('node:assert/strict'),{ViewState}=require(process.argv[1]);
const view=(seq,version)=>({envelope:{schema_version:'observer-view-1',case_id:'C',run_id:'R',data_mode:'example',sequence:seq,projection_revision:'p'+seq},
 case:{id:'C'},objects:{'incident:I':{key:'incident:I',type:'incident',version,refs:[],validity:'adopted'}},timeline:[],narrative:[]});
const s=new ViewState();s.accept(view(1,'v1'));a.equal(s.changes.length,0);
s.accept(view(2,'v2'));a.equal(s.changes[0].old.type,'incident');
s.accept(view(2,'v2'));a.equal(s.changes.length,1);
""", 'state.js', {})
