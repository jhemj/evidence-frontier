import copy
from types import SimpleNamespace
import pytest
from test_dossiers import setup
from workbench.semantic_contract import assertion_errors, review_key, report_gate


def observation(i='o', **fields):
    return {'id':i,'evidence_id':'e','type':'linux_command','source_location':'image:/log:'+i,
            'timestamp':'2026-09-23T12:00:00.123456789+09:00',
            'fields':{'path':'/log','artifact_path':'RUN-test/objects/log','source_sha256':'a'*64,**fields}}


def test_literal_binding_checks_type_scope_and_pointer():
    o=observation(user='alice',count=1,flag=True,items=[{'x/y':'literal'}])
    f={'observation_ids':['o'],'fact_assertions':[{'observation_id':'o','pointer':'/fields/user','value':'alice'}]}
    assert assertion_errors(f,{'o':o},{'o'})==[]
    for ptr,value in [('/fields/user','bob'),('/fields/count',True),('/fields/items/00/x~1y','literal'),('/fields/items','x')]:
        f['fact_assertions'][0].update(pointer=ptr,value=value)
        assert assertion_errors(f,{'o':o},{'o'})
    f['fact_assertions'][0].update(pointer='/fields/items/0/x~1y',value='literal')
    assert not assertion_errors(f,{'o':o},{'o'})
    assert assertion_errors(f,{'o':o},set())


def test_report_gate_refuses_omission_and_changed_required_facts():
    f={'dossier_id':'d','title':'fact','reason':'source','observation_ids':['o'],'stages':[]}
    doc={'dossiers':[{'id':'d','status':'reviewed','finding':f}]}
    with pytest.raises(ValueError,match='누락'):report_gate(doc,[])
    with pytest.raises(ValueError,match='다릅니다'):report_gate(doc,[{**f,'reason':'changed'}])
    assert report_gate(doc,[f])['validated']
    doc['dossiers'][0]['finding']={**f,'timeline_role':'반증됨'}
    assert report_gate(doc,[])['dispositions'][0]['disposition']=='excluded'


def test_exact_log_dictionary_roundtrip_and_cross_source_separation():
    from workbench.structured_context import group,expand
    payload='exact message with a rare value XYZ '+('retain ' * 180)
    pack={'observations':[observation(str(i),excerpt=f'Sep 23 12:00:0{i} host program[{i}]: {payload}\n',byte_offset=i*1600) for i in range(5)]}
    pack['observations'][-1]['fields']['source_sha256']='b'*64
    before=copy.deepcopy(pack)
    group(pack)
    assert len(pack['structured_log_groups'])==1
    assert pack['structured_log_groups'][0]['observation_ids']==['0','1','2','3']
    assert 'excerpt' in pack['observations'][-1]['fields']
    assert all(o['fields']['byte_offset']==int(o['id'])*1600 for o in pack['observations'])
    expand(pack)
    assert pack==before


def test_log_group_does_not_mask_payload_variables_or_bad_utf8():
    from workbench.structured_context import group
    pack={'observations':[observation(str(i),excerpt=f'Sep 23 12:00:00 host app: user={i} '+('x'*500)) for i in range(4)]}
    group(pack);assert not pack.get('structured_log_groups')
    for o in pack['observations']:o['fields']['excerpt']='Sep 23 12:00:00 host app: '+('\ufffd'*500)
    group(pack);assert not pack.get('structured_log_groups')


def test_review_reuse_scope_changes_only_on_relevant_content_or_runtime():
    e={'id':'e','signature':'sig'};o=observation();runtime={'model':'qwen'}
    key=review_key([o],e,runtime)
    assert key==review_key([copy.deepcopy(o)],e,runtime)
    assert key!=review_key([observation(user='changed')],e,runtime)
    assert key!=review_key([o],e,{'model':'changed'})
    assert key!=review_key([o],{'id':'other','signature':'sig'},runtime)


def test_disk_budget_combines_same_filesystem_and_fails_closed(tmp_path,monkeypatch):
    from workbench.disk_budget import require_space
    monkeypatch.setattr('workbench.disk_budget.shutil.disk_usage',lambda p:SimpleNamespace(free=100))
    assert require_space([(tmp_path/'a',30),(tmp_path/'b',40)],reserve=20)[0]['planned_bytes']==70
    with pytest.raises(ValueError,match='공간 부족'):require_space([(tmp_path/'a',50),(tmp_path/'b',40)],reserve=20)
    def fail(p):raise OSError('probe failed')
    monkeypatch.setattr('workbench.disk_budget.shutil.disk_usage',fail)
    with pytest.raises(ValueError,match='확인 실패'):require_space([(tmp_path,1)],reserve=0)


@pytest.mark.parametrize('raw,expected',[
    ('1970-01-01T00:00:00.000000001Z',1),('1969-12-31T23:59:59.999999999Z',-1),
    ('1970-01-01T09:00:00.000000001+09:00',1),('2026-02-30T00:00:00Z',None),
    ('2026-09-23T00:00:00',None),('1970-01-01T00:00:00.0000000001Z',None)])
def test_exact_time_no_float_or_timezone_guess(raw,expected):
    from workbench.evidence_semantics import exact_utc_ns,time_record
    assert exact_utc_ns(raw)==expected
    assert time_record(raw)['epoch_nanoseconds']==(str(expected) if expected is not None else None)


def test_time_kinds_do_not_promote_metadata_to_action():
    from workbench.evidence_semantics import observation_time
    o=observation();o['type']='windows_registry'
    assert observation_time(o)['time_kind']=='file_metadata'
    o['type']='linux_environment';assert observation_time(o)['time_kind']=='collected'
    o['type']='linux_command';assert observation_time(o)['time_kind']=='occurred'


def test_time_association_keeps_nanoseconds_and_rejects_mixed_scopes():
    from workbench.evidence_semantics import compare_times
    a=observation('a',user='same');b=observation('b',user='same')
    b['timestamp']='2026-09-23T12:00:00.123456790+09:00'
    result=compare_times(a,b)
    assert result['delta_nanoseconds']=='1' and result['delta_seconds_exact']=='0.000000001'
    assert result['independent_sources'] is False
    b['fields']['path']='/another-log'
    assert compare_times(a,b)['independent_sources'] is None
    b['fields']['user']='other';assert not compare_times(a,b)['comparable']
    b['fields']['user']='same';b['type']='windows_registry'
    assert compare_times(a,b)['reason']=='time_kind_mismatch_or_unknown'


def test_untyped_import_metadata_does_not_break_case_projection():
    from workbench.discovery import inventory
    from workbench.evidence_semantics import observation_time,compare_times
    a=observation('a',indicators=None,referenced_paths={},time_basis=None,time_kind=[])
    assert inventory([a],{'id':'e','signature':'s'})==[]
    assert observation_time(a)['time_kind']=='unknown'
    assert not compare_times(a,observation('b'))['comparable']


def test_discovery_inventory_invalidates_new_origin_or_source_not_bookkeeping():
    from workbench.discovery import inventory
    e={'id':'e','signature':'sig'};o=observation(indicators=[{'value':'192.0.2.55'}])
    a=inventory([o],e)
    assert a==inventory([o],{**e,'status':'unrelated'})
    assert a[0]['key']!=inventory([o],{**e,'signature':'changed'})[0]['key']
    assert a[0]['key']!=inventory([o,observation('new',indicators=[{'value':'192.0.2.55'}])],e)[0]['key']
    assert a[0]['query']=='192.0.2.55'


def test_discovery_budget_preserves_full_pending_inventory(tmp_path,monkeypatch):
    from workbench.discovery import admit,current,project
    c,cid,e,t,o=setup(tmp_path);t=c.store.update(t['id'],review_policy='autonomous-v1')
    for i in range(8):
        c.store.add('observation',cid,evidence_id=e['id'],type='linux_command',timestamp=None,source_location=str(i),
                    fields={'indicators':[{'value':'192.0.2.'+str(i)}]})
    for _ in range(6):assert admit(c,cid,e,t)
    assert not admit(c,cid,e,t)
    from workbench.check_ledger import project as checks
    doc={'evidence':[e],'observations':c.active_observations(cid),'dossiers':c.store.list('dossier',cid),
         'check_ledger':checks(c.store.list('investigation_job',cid),c.store.list('dossier_batch',cid))}
    state=project(doc,current(c.store,cid,t))
    assert len(state['discovery_inventory'])==9 and state['deferred_discovery']==3
    assert state['open_leads']==6
    with pytest.raises(ValueError):c.store.update(current(c.store,cid,t)[0]['id'],key='changed')


def test_discovery_runs_worker_then_assesses_and_reports_without_reexecution(tmp_path,monkeypatch):
    from workbench.dossiers import finish,digest
    c,cid,e,t,o=setup(tmp_path);t=c.store.update(t['id'],review_policy='autonomous-v1')
    requests=[];packs=[]
    def model(self,question,pack,role):
        packs.append(pack)
        fs=[{'dossier_id':d['id'],'title':'원문 단서','judgment':'미확인','reason':'행위는 미상',
             'observation_ids':d['observation_ids'][:1],'stages':[],'remaining_checks':[]} for d in pack['required_dossiers']]
        assessments=[{'check_id':ch['id'],'dossier_id':ct['dossier_id'],'contract_id':ct['contract_id'],
                      'outcome':'inconclusive','reason':'같은 출처 및 범위 제한','observation_ids':ch['observation_ids'][:1]}
                     for ch in pack.get('executed_checks',[]) for ct in ch['contracts']]
        checks=[] if pack.get('executed_checks') else [
            {'tool':'search','query':'cron','path':'/etc/cron.d/a','hypothesis_id':d['id'],
             'reason':'확인 가능한 설정·호출 기록을 대조','success_condition':'보존 원문에서 대상 경로의 호출 흔적 확인',
             'refutation_condition':'대상 범위에서 반대 기록 확인','inconclusive_condition':'보존 범위가 불완전하거나 일치 기록 없음'}
            for d in pack['required_dossiers'][:1]]
        return {'summary':'원문 검토','findings':fs,'next_checks':checks,'check_assessments':assessments},{'model':'test'}
    def worker(method,path,**kwargs):
        if method=='POST':requests.append(kwargs['json'])
        result={'status':'covered_zero','complete':True,'observations':[]}
        return {'status':'succeeded','result':result,'result_sha256':digest(result)}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    monkeypatch.setattr('workbench.dossiers.worker_request',worker)
    for _ in range(30):
        result=finish(c,cid,e,t)
        if result:break
    assert result and len(requests)==1
    assert any(p.get('executed_checks') for p in packs)
    assert any(r.get('receipt_type') in ('dossier_model','dossier_page_model')
               for r in c.store.list('receipt',cid))
    assert finish(c,cid,e,t)==result and len(requests)==1
    from workbench.reporting import report_document
    doc=report_document(c,cid)
    # Question-first completion may leave inventory outside the admitted check
    # batch; it must remain visible as deferred rather than being reported as
    # complete merely because the admitted check was assessed.
    assert doc['investigation_frontier']['deferred_discovery']==1
    assert doc['check_ledger']['unassessed_contracts']==0


def test_source_only_review_is_reused_on_retry_but_changed_model_is_not(tmp_path,monkeypatch):
    from workbench.dossiers import finish,seed
    c,cid,e,t,o=setup(tmp_path)
    def model(self,question,pack,role):
        return {'summary':'검토','findings':[{'dossier_id':d['id'],'title':'단서','reason':'원문 기록',
            'judgment':'미확인','observation_ids':d['observation_ids'][:1],'remaining_checks':[]} for d in pack['required_dossiers']]},{'model':'test'}
    monkeypatch.setattr('workbench.dossiers.Provider.generate',model)
    for _ in range(10):
        if finish(c,cid,e,t):break
    t=c.store.update(t['id'],retry_generation=1);seed(c,cid,e,t)
    reused=[d for d in c.store.list('dossier',cid) if d.get('generation')==1 and d.get('reused_from')]
    assert len(reused)==1
    assert reused[0]['finding']['dossier_id']==reused[0]['id']
    c.store.add('config','',provider={'model':'different'})
    t=c.store.update(t['id'],retry_generation=2);seed(c,cid,e,t)
    assert not any(d.get('reused_from') for d in c.store.list('dossier',cid) if d.get('generation')==2)


def test_revisit_is_not_starved_behind_large_baseline_queue(tmp_path,monkeypatch):
    from workbench.dossiers import seed,finish
    c,cid,e,t,o=setup(tmp_path);t=c.store.update(t['id'],review_policy='autonomous-v1')
    for i in range(50):
        c.store.add('observation',cid,evidence_id=e['id'],type='linux_authentication',timestamp=None,
            source_location=str(i),fields={'path':'/log','user':'u'+str(i)})
    seed(c,cid,e,t)
    batches=c.store.list('dossier_batch',cid);assert len(batches)>3
    c.store.update(batches[0]['id'],status='done')
    revisit=next(b for b in batches[1:] if b['status'] not in ('done','await_checks'))
    dossier=c.store.get(revisit['dossier_ids'][0])
    job=c.store.add('investigation_job',cid,task_id=t['id'],evidence_id=e['id'],generation=0,
        fingerprint='revisit-job',request={'tool':'search','query':'revisit','path':'/log',
        'hypothesis_id':dossier['id'],'reason':'agent-admitted revisit','success_condition':'record found'},
        dossier_ids=[dossier['id']],contracts=[],purpose='dossier_falsification',source_run='RUN-'+'a'*32,
        status='admitted',review_family=dossier.get('review_family','baseline'),test_intent_ids=[])
    c.store.update(revisit['id'],status='await_checks',job_ids=[job['id']])
    class Stop(Exception):pass
    submitted=[]
    def worker(method,path,**kwargs):
        if method=='POST': submitted.append(kwargs['json'])
        raise Stop()
    monkeypatch.setattr('workbench.dossiers.worker_request',worker)
    # The next tick starts the new source-bound search, not another baseline call.
    with pytest.raises(Stop):finish(c,cid,e,t)
    assert submitted and submitted[0]['investigation']['request']['query']=='revisit'


def test_semantic_scoring_penalizes_false_claims_and_unknown_evidence():
    from workbench.evaluation import score_investigation
    gold={'cases':[{'case_id':'c','required_observation_ids':['one','two'],'allowed_observation_ids':['one','two'],
                   'conclusion':'unknown','next_test':'requery','unsupported_claims':['execution_proven']}]}
    candidate={'answers':[{'case_id':'c','observation_ids':['one','two'],'conclusion':'unknown','next_test':'requery'}]}
    assert score_investigation(candidate,gold)['score']==4
    candidate['answers'][0]['asserted_claims']=['execution_proven']
    assert score_investigation(candidate,gold)['score']==2
    candidate['answers'][0]['observation_ids'].append('made-up')
    assert score_investigation(candidate,gold)['score']==0
    with pytest.raises(ValueError):score_investigation(candidate,{})


def test_six_quality_cases_have_a_separate_key_and_discriminating_tests():
    import json
    from pathlib import Path
    from workbench.evaluation import score_investigation
    root=Path(__file__).resolve().parents[1]
    public=json.loads((root/'examples/investigation_quality_input.json').read_text())
    gold=json.loads((root/'tests/fixtures/investigation_quality_gold.json').read_text())
    assert len(public['cases'])==len(gold['cases'])==6
    assert all('required_observation_ids' not in c for c in public['cases'])
    for g,c in zip(gold['cases'],public['cases']):
        assert g['case_id']==c['case_id'] and g['next_test'] in c['test_choices']
    answers=[{'case_id':g['case_id'],'observation_ids':g['required_observation_ids'],
              'conclusion':g['conclusion'],'next_test':g['next_test']} for g in gold['cases']]
    assert score_investigation({'answers':answers},gold)['score']==24
