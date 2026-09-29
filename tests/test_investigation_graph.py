import json
import pytest
from workbench.store import Store
from workbench.controller import Controller
from workbench.investigation import seed
from workbench.investigation_graph import tick, digest, completed_result_memory


def test_completed_result_memory_prioritizes_unpresented_tools_and_preserves_ids():
    """A burst of repeated searches must not hide a different completed result."""
    jobs = []
    for index in range(10):
        jobs.append({'id': f'search-{index}', 'status': 'ingested',
                     'request': {'tool': 'search'},
                     'observation_ids': [f'OBS-search-{index}']})
    jobs.insert(5, {'id': 'static-file-1', 'status': 'ingested',
                    'request': {'tool': 'static_file'},
                    'observation_ids': ['OBS-static-file-1']})
    memory = completed_result_memory(jobs, {'OBS-search-0'})
    assert 'OBS-static-file-1' in memory['observation_ids']
    assert memory['jobs'][0]['tool'] == 'search'
    assert any(row['tool'] == 'static_file' for row in memory['jobs'])
    assert memory['jobs_total'] == 11
    assert all(row['observation_ids'] for row in memory['jobs'])
    assert memory['jobs'][0]['request_scope']['tool'] == 'search'
    assert 'result_status' in memory['jobs'][0]


def test_plan_promotes_completed_result_and_rebuilds_scoped_audit(tmp_path, monkeypatch):
    """Promotion is verified at the final pre-consult pack, not only in hints."""
    from workbench.investigation_graph import Investigation

    c = Controller(Store(tmp_path / 'case.db'), tmp_path)
    cid = c.create('planner promotion', '', 'standard')['id']
    c.store.update(cid, status='running')
    e1 = c.store.add('evidence', cid, path='first.E01', signature='first', connected=True)
    e2 = c.store.add('evidence', cid, path='second.E01', signature='second', connected=True)
    task = c.store.add('task', cid, action='linux_investigate', cell_id='CELL', evidence_id=e1['id'])
    c.store.add('receipt', cid, task_id=task['id'], evidence_id=e1['id'], result={'run_id': 'RUN-' + 'a' * 32})
    c.store.add('config', '', provider={'model': 'fixture'})
    from workbench.investigation import seed
    seed(c, cid, e1['id'])
    completed = c.store.add('observation', cid, evidence_id=e1['id'], type='linux_command',
                            timestamp=None, source_location='fixture',
                            fields={'path': '/tmp/static-result', 'excerpt': 'completed'})
    ordinary = c.store.add('observation', cid, evidence_id=e1['id'], type='linux_command',
                           timestamp=None, source_location='fixture',
                           fields={'path': '/tmp/ordinary', 'excerpt': 'ordinary'})
    foreign = c.store.add('observation', cid, evidence_id=e2['id'], type='linux_command',
                          timestamp=None, source_location='fixture',
                          fields={'path': '/tmp/foreign', 'excerpt': 'foreign'})
    c.store.add('investigation_job', cid, task_id=task['id'], status='ingested',
                request={'tool': 'static_file', 'path': '/tmp/static-result'},
                observation_ids=[completed['id']], result_scope={'status': 'succeeded'},
                result_status='succeeded')
    inv = Investigation(c, cid, e1, task)
    state = inv.preflight({})
    c.store.update(c.store.list('investigation_plan', cid)[0]['id'], assessed=True)
    captured = {}
    monkeypatch.setattr('workbench.investigation_graph.evidence_pack',
                        lambda *args, **kwargs: {
                            'observations': [ordinary], 'total_observations': 2,
                            'included_observations': 1, 'selection_audit': {
                                'selected': [{'id': ordinary['id'], 'reason': 'baseline_rank'}]},
                            'hypotheses': [], 'dynamic_hypotheses': []})
    monkeypatch.setattr('workbench.review_context.fit', lambda pack: None)
    def fake_model(question, pack, run, purpose):
        captured['pack'] = pack
        return {'summary': 'fixture', 'claims': [], 'hypotheses': [],
                'remaining_questions': [], 'tool_calls': []}
    inv.model = fake_model
    inv.plan(state)
    pack = captured['pack']
    selected = {o['id'] for o in pack['observations']}
    assert completed['id'] in selected
    assert foreign['id'] not in selected
    assert pack['selection_audit']['included'] == len(selected)
    assert pack['selection_audit']['selected']
    assert pack['completed_result_memory']['jobs'][0]['request_scope']['path'] == '/tmp/static-result'

@pytest.mark.parametrize('fail_first,keep_exploring',[(False,False),(True,False),(False,True),(False,'repeat')])
def test_checkpoint_reopen_reuses_plans_and_results(tmp_path, monkeypatch, fail_first, keep_exploring):
    monkeypatch.setenv('DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.setenv('INVESTIGATION_MODEL_CALLS','6')
    c=Controller(Store(tmp_path/'case.db'),tmp_path)
    case=c.create('Graph fixture','','standard'); cid=case['id'];c.store.update(cid,status='running')
    ev=c.store.add('evidence',cid,path='fixture.E01',signature='fixture',connected=True)
    task=c.store.add('task',cid,action='linux_investigate',cell_id='CELL',evidence_id=ev['id'])
    c.store.add('receipt',cid,evidence_id=ev['id'],result={'run_id':'RUN-'+'a'*32})
    c.store.add('config','',provider={'model':'local','base_url':'http://localhost:11434','protocol':'ollama'})
    seed(c,cid,ev['id']); model_calls=[]; submissions={}
    def model(self,question,pack,role):
        model_calls.append(question)
        if fail_first and len(model_calls)==1:raise ValueError('Invalid JSON fixture response')
        if fail_first and len(model_calls)==2:
            assert 'Invalid JSON fixture response' in pack['output_validation_feedback']['error']
            assert 'not evidence' in pack['output_validation_feedback']['instruction']
        output={'summary':'확인한 범위에서의 조사','claims':[],'hypotheses':[],'remaining_questions':['자료 미제공'],'tool_calls':[]}
        if keep_exploring:output['tool_calls']=[{'tool':'search','query':'repeated-query' if keep_exploring=='repeat' else f'query-{len(model_calls)}','reason':'additional fixture search'}]
        return output,{'output':output,'usage':{'eval_count':10}}
    def worker(method,path,**kwargs):
        if method=='POST':
            body=kwargs['json'];submissions.setdefault(body['job_key'],body)
        result={'status':'partial','complete':False,'observations':[]}
        return {'status':'succeeded','result':result,'result_sha256':digest(result)}
    monkeypatch.setattr('workbench.investigation_graph.Provider.generate',model)
    monkeypatch.setattr('workbench.investigation_graph.worker_request',worker)
    result=None
    for _ in range(200):
        # Every tick closes and reopens the actual persistent SQLite checkpointer.
        result=tick(c,cid,ev,task)
        if result:break
    assert result and result['status']=='partial' and not result['complete']
    assert result['domains_requested']>=3 and result['domains_assessed']==0
    assert any(c.get('output',{}).get('remaining_questions') for c in c.store.list('investigation_plan',cid))
    expected_calls=len(model_calls)
    assert expected_calls >= 1
    assert len(submissions) >= 0
    if keep_exploring is True:
        assert '마지막 결과 통합' in model_calls[-1]
        assert not any(body.get('investigation',{}).get('request',{}).get('query')=='query-6' for body in submissions.values())
        assert any('미실행 추가 제안' in text for text in c.store.list('investigation_plan',cid)[-1]['output']['remaining_questions'])
    before=len(c.store.list('receipt',cid))
    assert tick(c,cid,ev,task)==result
    assert len(c.store.list('receipt',cid))==before
    assert c.store.list('investigation_run',cid)[0]['model_calls']==expected_calls

def test_pause_does_not_start_a_model_plan(tmp_path,monkeypatch):
    monkeypatch.setenv('DATA_ROOT',str(tmp_path/'data'))
    c=Controller(Store(tmp_path/'case.db'),tmp_path);case=c.create('Pause','','standard');cid=case['id']
    e=c.store.add('evidence',cid,signature='x',path='x.E01',connected=True)
    t=c.store.add('task',cid,evidence_id=e['id'],action='linux_investigate',cell_id='c')
    c.store.update(cid,status='pause_requested')
    assert tick(c,cid,e,t) is None
    assert c.store.get(cid)['status']=='paused'
    assert not c.store.list('model_reservation',cid)


@pytest.mark.parametrize('target_os',['linux','windows'])
@pytest.mark.parametrize('failed_calls',[(2,),(2,4)])
def test_truncated_plan_partitions_without_skipping_domains(tmp_path,monkeypatch,target_os,failed_calls):
    from workbench.provider import ModelOutputError
    monkeypatch.setenv('DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.setenv('INVESTIGATION_MODEL_CALLS','12')
    c=Controller(Store(tmp_path/'case.db'),tmp_path);s=c.store
    cid=c.create('Output recovery','','standard')['id'];s.update(cid,status='running',target_os=target_os)
    e=s.add('evidence',cid,path='fixture.E01',signature='fixture',connected=True)
    t=s.add('task',cid,action=f'{target_os}_investigate',cell_id='CELL',evidence_id=e['id'])
    s.add('receipt',cid,evidence_id=e['id'],result={'run_id':'RUN-'+'a'*32})
    s.add('config','',provider={'model':'local','base_url':'http://localhost:11434','protocol':'ollama'})
    seed(c,cid,e['id']);seen=[]
    def model(self,question,pack,role):
        focus=[h['number'] for h in pack['hypotheses']]
        seen.append(focus)
        if len(seen) in failed_calls:
            raise ModelOutputError('Output truncated','{"summary":"never accepted"','output_budget',
                {'usage':{'eval_count':4000},'generation_settings':{'num_predict':4000},
                 'prompt_characters':10000,'elapsed_seconds':2.5})
        if len(seen)>2:
            assert len(focus)==1
            assert pack['response_budget_guidance']['focus']==focus
            assert '전체 가설 수 제한이 아닙니다' in question
            if len(seen)-1 in failed_calls:
                assert pack['output_validation_feedback']['category']=='output_budget'
            else:
                assert 'output_validation_feedback' not in pack
        output={'summary':'Scoped source review','claims':[],'hypotheses':[],
                'remaining_questions':['Unreviewed sources remain'],
                'tool_calls':[{'tool':'search','query':f'new-scope-{len(seen)}','reason':'Discriminating follow-up'}] if len(seen)<6 else []}
        return output,{'output':output,'usage':{'eval_count':50}}
    def worker(*args,**kwargs):
        result={'status':'partial','complete':False,'observations':[]}
        return {'status':'succeeded','result':result,'result_sha256':digest(result)}
    monkeypatch.setattr('workbench.investigation_graph.Provider.generate',model)
    monkeypatch.setattr('workbench.investigation_graph.worker_request',worker)
    result=None
    for _ in range(200):
        # Each tick reopens the persistent graph, including the adaptive width.
        result=tick(c,cid,e,t)
        if result:break
    assert result and result['status']=='partial' and result['domains_requested']>=3
    assert seen and all(focus for focus in seen)
    plans=s.list('investigation_plan',cid)
    assert any(p.get('focus') for p in plans)
    assert any(p.get('output',{}).get('remaining_questions') for p in plans)
    assert all(p['output']['summary']!='never accepted' for p in plans)
    run=s.list('investigation_run',cid)[0]
    assert run['plan_domain_batch_size']==1 and run['domain_cursor']>=1
    assert run['consecutive_plan_failures']==0 and run['model_calls']==6
    errors=[r for r in s.list('receipt',cid) if r.get('receipt_type')=='model_error']
    assert len(errors)==len(failed_calls)
    assert all(r['recovery']['consecutive_failures']==1 for r in errors)
    assert all(r['usage']=={'eval_count':4000} for r in errors)
    assert all(r['generation_settings']['num_predict']==4000 for r in errors)
    assert all(r['prompt_characters']==10000 and r['elapsed_seconds']==2.5 for r in errors)


@pytest.mark.parametrize('category',['output_budget','output_schema','transport_or_validation'])
def test_repeated_planner_failure_is_explicit_not_a_completed_investigation(tmp_path,monkeypatch,category):
    from workbench.provider import ModelOutputError
    monkeypatch.setenv('DATA_ROOT',str(tmp_path/'data'))
    monkeypatch.setenv('INVESTIGATION_MODEL_CALLS','12')
    c=Controller(Store(tmp_path/'case.db'),tmp_path);s=c.store
    cid=c.create('Failure budget','','standard')['id'];s.update(cid,status='running')
    e=s.add('evidence',cid,path='fixture.E01',signature='fixture',connected=True)
    t=s.add('task',cid,action='linux_investigate',cell_id='CELL',evidence_id=e['id'])
    s.add('receipt',cid,evidence_id=e['id'],result={'run_id':'RUN-'+'a'*32})
    s.add('config','',provider={'model':'local','base_url':'http://localhost:11434','protocol':'ollama'})
    seed(c,cid,e['id']);seen=[]
    def model(self,question,pack,role):
        seen.append([h['number'] for h in pack['hypotheses']])
        raise ModelOutputError('fixture failure','{"summary":',category)
    def worker(*args,**kwargs):
        result={'status':'partial','complete':False,'observations':[]}
        return {'status':'succeeded','result':result,'result_sha256':digest(result)}
    monkeypatch.setattr('workbench.investigation_graph.Provider.generate',model)
    monkeypatch.setattr('workbench.investigation_graph.worker_request',worker)
    with pytest.raises(ValueError,match='연속 실패'):
        for _ in range(100):tick(c,cid,e,t)
    run=s.list('investigation_run',cid)[0]
    assert run['stop_reason']=='planner_failure_limit'
    assert run['model_calls']==3 and run['consecutive_plan_failures']==3 and run['domain_cursor']==1
    assert not s.list('investigation_result',cid)
    assert len(s.list('investigation_plan',cid))==1 # Only the deterministic preflight.
    assert seen==([[1,2,3],[1],[1]] if category=='output_budget' else [[1,2,3]]*3)
    with pytest.raises(ValueError,match='연속 실패'):tick(c,cid,e,t)
    assert s.get(run['id'])['model_calls']==3 # Reopening cannot refund failure reservations.


def test_domain_record_id_and_natural_creation_are_distinct_routes(tmp_path):
    from workbench.investigation_graph import Investigation
    c=Controller(Store(tmp_path/'case.db'),tmp_path);s=c.store
    cid=c.create('synthetic routing','','standard')['id']
    e=s.add('evidence',cid,connected=True)
    t=s.add('task',cid,evidence_id=e['id'],retry_generation=2)
    seed(c,cid,e['id']);domain=s.list('hypothesis',cid)[0]
    o=s.add('observation',cid,evidence_id=e['id'],type='linux_command',fields={})
    base={'action':'update','number':domain['number'],'hypothesis_id':domain['id'],'title':'영역 답변',
          'judgment':'미확인','reasoning':'추가 확인 필요','supporting_evidence_ids':[o['id']],
          'refuting_evidence_ids':[],'remaining_checks':[]}
    p=s.add('investigation_plan',cid,task_id=t['id'],generation=2,assessed=False,valid_ids=[o['id']],
        output={'claims':[],'hypotheses':[base,{**base,'action':'create','number':None,'hypothesis_id':'','title':'새 원문 기반 질문'}]})
    Investigation(c,cid,e,t).assess({'plan_id':p['id']})
    assert s.get(domain['id'])['reasoning']=='추가 확인 필요'
    dynamic=[h for h in s.list('hypothesis',cid) if h.get('hypothesis_kind')=='dynamic']
    assert len(dynamic)==1 and dynamic[0]['generation']==2 and dynamic[0]['number']==11
    Investigation(c,cid,e,t).assess({'plan_id':p['id']})
    assert s.get(dynamic[0]['id'])['revision']==1


def test_falsifier_unknown_citation_is_recorded_and_left_unreviewed(tmp_path, monkeypatch):
    from workbench.investigation_graph import Investigation

    c=Controller(Store(tmp_path/'case.db'),tmp_path)
    case=c.create('Citation contract','','standard'); cid=case['id']; c.store.update(cid,status='running')
    e=c.store.add('evidence',cid,path='fixture.E01',signature='fixture',connected=True)
    t=c.store.add('task',cid,action='linux_investigate',cell_id='CELL',evidence_id=e['id'])
    c.store.add('receipt',cid,evidence_id=e['id'],result={'run_id':'RUN-'+'a'*32})
    c.store.add('config','',provider={'model':'local','base_url':'http://localhost:11434','protocol':'ollama'})
    o=c.store.add('observation',cid,evidence_id=e['id'],type='linux_command',timestamp=None,source_location='fixture:/var/log/example',fields={'path':'/var/log/example'})
    run=c.store.add('investigation_run',cid,task_id=t['id'],evidence_id=e['id'],version='investigation-graph-2',
                    signature=e['signature'],model_calls=1,max_model_calls=6,tool_calls=0,max_tool_calls=36,
                    challenge_calls=0,max_challenge_calls=10,review_calls=0,max_review_calls=5,domain_cursor=4,
                    source_run='RUN-'+'a'*32,stop_reason=None)
    claim=c.store.add('claim',cid,task_id=t['id'],evidence_id=e['id'],text='기록된 명령',observation_ids=[o['id']],
                      challenge_status='pending',falsification=None)
    inv=Investigation(c,cid,e,t)
    monkeypatch.setattr(inv,'admit',lambda state,calls,purpose,**kwargs: None)
    monkeypatch.setattr('workbench.investigation_graph.evidence_pack',lambda *args,**kwargs:
                        {'observations':[o], 'selection_is_partial':False})
    monkeypatch.setattr('workbench.runtime_contract.guard',lambda *args,**kwargs: None)
    captured={}
    def fake_consult(config,question,pack,role,provider_factory):
        captured['question']=question
        return ({'alternatives':['정상 관리 가능성'],
                 'contradicting_observation_ids':['OBS-not-allowed'],
                 'missing_checks':['승인 기록']}, {'role':'falsifier'})
    monkeypatch.setattr('workbench.investigation_graph.consult',fake_consult)

    assert inv.challenge({'run_id':run['id']}) == {}
    updated=c.store.get(claim['id'])
    assert updated['falsification'] is None
    assert updated['challenge_status']=='specified'
    errors=[r for r in c.store.list('receipt',cid) if r.get('receipt_type')=='model_error']
    assert errors and errors[-1]['failure_category']=='citation_contract'
    assert errors[-1]['rejected_observation_ids']==['OBS-not-allowed']
    assert o['id'] in captured['question']


@pytest.mark.parametrize('id_supplied',[True,False])
def test_domain_hold_updates_domain_and_create_remains_dynamic(tmp_path,id_supplied):
    from workbench.investigation_graph import Investigation
    c=Controller(Store(tmp_path/'case.db'),tmp_path);s=c.store
    case=c.create('domain routing','','standard');cid=case['id'];e=s.add('evidence',cid,connected=True)
    t=s.add('task',cid,evidence_id=e['id'],retry_generation=2)
    seed(c,cid,e['id']);domain=s.list('hypothesis',cid)[0]
    o=s.add('observation',cid,evidence_id=e['id'],type='linux_command',fields={})
    common={'title':'영역 보류','judgment':'확정','reasoning':'원문 추가 대조 필요',
            'supporting_evidence_ids':[],'refuting_evidence_ids':[],'remaining_checks':['승인 기록']}
    hold={**common,'action':'hold','hypothesis_id':domain['id'] if id_supplied else '', 'number':domain['number']}
    p=s.add('investigation_plan',cid,task_id=t['id'],generation=2,assessed=False,valid_ids=[o['id']],
            output={'claims':[],'hypotheses':[hold]})
    Investigation(c,cid,e,t).assess({'plan_id':p['id']})
    updated=s.get(domain['id'])
    assert updated['judgment']=='미확인'
    assert updated['judgment_history'][-1]['action']=='hold'
    assert not [h for h in s.list('hypothesis',cid) if h.get('hypothesis_kind')=='dynamic']

    create={**common,'action':'create','hypothesis_id':domain['id'],'number':domain['number'],
            'supporting_evidence_ids':[o['id']],
            'title':'새로운 원문 질문','card_summary':'별도 동적 가설입니다.'}
    p2=s.add('investigation_plan',cid,task_id=t['id'],generation=2,assessed=False,valid_ids=[o['id']],
             output={'claims':[],'hypotheses':[create]})
    Investigation(c,cid,e,t).assess({'plan_id':p2['id']})
    dynamic=[h for h in s.list('hypothesis',cid) if h.get('hypothesis_kind')=='dynamic']
    assert len(dynamic)==1 and dynamic[0]['id']!=domain['id']


@pytest.mark.parametrize('action',['hold','update','create'])
def test_unknown_domain_id_is_not_repaired_using_number(tmp_path,action):
    from workbench.investigation_graph import Investigation
    c=Controller(Store(tmp_path/'case.db'),tmp_path);s=c.store
    cid=c.create('unknown domain','','standard')['id'];e=s.add('evidence',cid,connected=True)
    t=s.add('task',cid,evidence_id=e['id']);seed(c,cid,e['id']);domain=s.list('hypothesis',cid)[0]
    o=s.add('observation',cid,evidence_id=e['id'],type='linux_command',fields={})
    proposal={'action':action,'hypothesis_id':'unknown-id','number':domain['number'],
              'title':'검사 질문','judgment':'미확인','reasoning':'자료 대조',
              'supporting_evidence_ids':[o['id']],'refuting_evidence_ids':[], 'remaining_checks':[]}
    p=s.add('investigation_plan',cid,task_id=t['id'],assessed=False,valid_ids=[o['id']],
            output={'claims':[],'hypotheses':[proposal]})
    Investigation(c,cid,e,t).assess({'plan_id':p['id']})
    assert s.get(domain['id'])==domain
    assert not [h for h in s.list('hypothesis',cid) if h.get('hypothesis_kind')=='dynamic']
    assert s.list('receipt',cid)[-1]['receipt_type']=='hypothesis_update_rejected'
