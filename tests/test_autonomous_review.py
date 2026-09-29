import json
from collections import Counter
from datetime import timezone
import pytest
from test_dossiers import setup
from workbench.review_partition import partition
from workbench.check_ledger import project as check_ledger
from workbench.review_contracts import contract


def observation(i,typ='linux_command',**fields):
    return {'id':str(i),'evidence_id':'e','type':typ,'timestamp':'2026-09-01T01:00:00+09:00',
        'source_location':f'image:/log:{i}','fields':{'path':'/log','byte_offset':i*100,'partition_offset':0,**fields}}


def test_partition_accounts_dates_and_undated_records_without_loss():
    rows=[observation(i,user='a' if i%2 else 'b') for i in range(1170)]
    rows[0]['timestamp']=None
    rows[1]['fields']['partition_offset']=4096
    units=partition('인증·SSH·권한',rows,('baseline',None,'인증·SSH·권한'))
    assert Counter(o['id'] for _,group,_ in units for o in group)==Counter(o['id'] for o in rows)
    assert max(len(group) for _,group,_ in units)<=12
    assert units==partition('인증·SSH·권한',list(reversed(rows)),('baseline',None,'인증·SSH·권한'))
    assert len({key for _,_,key in units})==len(units)


def test_split_baselines_respect_hard_admission_cap():
    from workbench.review_queue import schedule
    rows=[dict(id=str(i),baseline=True,review_family=('access','persistence','execution')[i%3],
        review_priority=6,group_key=str(i)) for i in range(2000)]
    admitted=schedule(rows,limit=96)
    assert len(admitted)==96
    assert {r['review_family'] for r in admitted}=={'access','persistence','execution'}


def test_seed_never_samples_away_a_baseline_record(tmp_path):
    from workbench.dossiers import seed
    c,cid,e,t,o=setup(tmp_path)
    for i in range(45):
        c.store.add('observation',cid,evidence_id=e['id'],type='linux_authentication',timestamp=None,
            source_location=f'/auth:{i}',fields={'path':'/auth','user':'operator','excerpt':str(i)})
    seed(c,cid,e,t)
    units=[d for d in c.store.list('dossier',cid) if d['review_family']=='access']
    assert sum(d['total_records'] for d in units)==45
    assert all(set(d['all_observation_ids']).issubset(d['observation_ids']) for d in units)


def test_failed_batch_is_automatically_isolated(tmp_path,monkeypatch):
    from workbench.dossiers import finish
    c,cid,e,t,o=setup(tmp_path);t=c.store.update(t['id'],review_policy='autonomous-v1')
    for typ in ('linux_authentication','linux_persistence'):
        c.store.add('observation',cid,evidence_id=e['id'],type=typ,timestamp=None,source_location='/log',fields={'path':'/log'})
    def fail(*args,**kwargs):raise ValueError('schema error')
    monkeypatch.setattr('workbench.dossiers.Provider.generate',fail)
    for _ in range(3):assert finish(c,cid,e,t) is None
    batches=c.store.list('dossier_batch',cid)
    assert len([b for b in batches if b['status']=='split'])==1
    assert all(b['validation_feedback']['errors'][0]['detail']=='schema error' for b in batches if b.get('parent_batch_id'))
    assert len([b for b in batches if b.get('parent_batch_id') and len(b['dossier_ids'])==1])==3
    assert not [d for d in c.store.list('dossier',cid) if d['status']=='model_failed']


def test_check_ledger_deduplicates_scopes_but_keeps_unassessed_contracts():
    call={'tool':'read_file','path':'/log','hypothesis_id':'d','success_condition':'record','refutation_condition':'contrary record'}
    c=contract(call);owner={'evidence_id':'e','task_id':'t','generation':0}
    job={**owner,'id':'j','request':call,'status':'ingested','contracts':[c]}
    batch={**owner,'deferred_checks':[{'request':call,'reason':'budget'}]*131,
        'output':{'check_assessments':[{'check_id':'j',**c,'evaluation_status':'unassessed','outcome':'inconclusive','observation_ids':[]}]}}
    result=check_ledger([job],[batch])
    assert result['unique_scopes']==1 and result['not_executed']==0 and result['unassessed_contracts']==1
    batch['output']['check_assessments'][0]['evaluation_status']='assessed'
    assert check_ledger([job],[batch])['unassessed_contracts']==0
    assert check_ledger([job,{**job,'id':'j2','evidence_id':'e2'}],[batch])['unique_scopes']==2


def test_session_links_cannot_cross_evidence_or_partition_or_claim_attribution():
    from workbench.session_links import project
    rows=[observation(i,user='admin') for i in range(4)]
    rows[1]['evidence_id']='other';rows[2]['fields']['partition_offset']=8192
    linked=project(rows)
    assert len(linked)==3 and sum(len(x['records']) for x in linked)==4
    assert all(x['attribution']=='unconfirmed' and x['link_basis']=='account_day_candidate' for x in linked)


def test_sudo_parser_retains_actor_target_and_command():
    from workbench.linux_analysis import text_events
    line=b'2026-09-01T01:00:00+09:00 host sudo: analyst : TTY=pts/0 ; PWD=/tmp ; USER=root ; COMMAND=/usr/bin/id\n'
    event=list(text_events('/var/log/auth.log',line,0,timezone.utc))[0]
    assert event['fields']['user']=='analyst' and event['fields']['target_user']=='root'
    assert event['fields']['command']=='/usr/bin/id'
    assert 'session_id' not in event['fields']


def test_final_synthesis_checkpoints_and_citation_rejection(tmp_path,monkeypatch):
    from workbench.case_synthesis import tick,current
    c,cid,e,t,o=setup(tmp_path)
    h=c.store.add('hypothesis',cid,evidence_id=e['id'],contract='linux-v1',number=1,text='가설 질문',
        expected_source_types=['linux_detection'],competing_explanations=['정상 관리'],unavailable_materials=['승인 기록'])
    calls=[]
    def model(self,question,pack,role):
        calls.append(pack)
        refs=['NOT-PRESENT'] if len(calls)==1 else [o['id']]
        return {'findings':[{'dossier_id':h['id'],'title':'정황','judgment':'미확인','reason':'자료 부족','observation_ids':refs,'stages':[]}]},{'model':'test'}
    monkeypatch.setattr('workbench.case_synthesis.Provider.generate',model)
    assert not tick(c,cid,e,t,[])
    assert not current(c,cid,t)
    assert not tick(c,cid,e,t,[])
    assert tick(c,cid,e,t,[])
    assert len(calls)==2 and len(current(c,cid,t))==1
    with pytest.raises(ValueError):c.store.update(current(c,cid,t)[0]['id'],status='changed')


def test_final_synthesis_exhaustion_is_explicit_unknown(tmp_path,monkeypatch):
    from workbench.case_synthesis import tick,current
    c,cid,e,t,o=setup(tmp_path)
    c.store.add('hypothesis',cid,evidence_id=e['id'],contract='linux-v1',number=1,text='가설',expected_source_types=['linux_detection'])
    def fail(*args,**kwargs):raise ValueError('schema')
    monkeypatch.setattr('workbench.case_synthesis.Provider.generate',fail)
    for _ in range(3):assert not tick(c,cid,e,t,[])
    assert tick(c,cid,e,t,[])
    record=current(c,cid,t)[0]
    assert record['status']=='model_failed' and record['finding']['judgment']=='미확인'
    assert not record['finding']['observation_ids']


def test_report_preserves_final_citation_beyond_preview_cap(tmp_path):
    from workbench.reporting import preview_document,render
    c,cid,e,t,o=setup(tmp_path)
    for i in range(205):
        last=c.store.add('observation',cid,evidence_id=e['id'],type='linux_binary',timestamp=None,source_location='/bin/test',fields={})
    c.store.add('judgment',cid,task_id=t['id'],evidence_id=e['id'],generation=0,summary='test',findings=[
        {'title':'판단','judgment':'미확인','reason':'검토','observation_ids':[last['id']],'stages':[],'alternatives':[],'remaining_checks':[]}])
    doc=preview_document(c,cid);html=render(doc)
    assert last['id'] in {o['id'] for o in doc['observations']}
    assert f'href="#{last["id"]}"' in html and f'id="{last["id"]}"' in html


def test_synthesis_invalidates_report_revision_for_existing_database(tmp_path):
    from workbench.store import Store
    store=Store(tmp_path/'case.db');before=store.report_revision('c')
    store.add('case_synthesis','c',finding={})
    assert store.report_revision('c')==before+1


def test_synthesis_oversized_input_ends_as_explicit_gap_without_call(tmp_path,monkeypatch):
    from workbench.case_synthesis import tick,current
    from workbench import review_stream
    c,cid,e,t,o=setup(tmp_path)
    c.store.add('hypothesis',cid,evidence_id=e['id'],contract='linux-v1',number=1,text='가설',expected_source_types=['linux_detection'])
    def oversized(*a,**kw):raise ValueError('input budget exceeded')
    def paging_also_oversized(*a,**kw):raise review_stream.ProjectionTooLarge('page floor')
    monkeypatch.setattr('workbench.case_synthesis.fit',oversized)
    monkeypatch.setattr('workbench.review_stream.start',paging_also_oversized)
    monkeypatch.setattr('workbench.case_synthesis.Provider.generate',lambda *a,**kw:pytest.fail('must not call model'))
    assert not tick(c,cid,e,t,[]) and tick(c,cid,e,t,[])
    assert current(c,cid,t)[0]['status']=='input_budget'
    assert not c.store.list('synthesis_input',cid)


def test_synthesis_rejects_a_disconnected_scope_before_adoption(tmp_path,monkeypatch):
    from workbench.case_synthesis import tick,current
    c,cid,e,t,o=setup(tmp_path)
    h=c.store.add('hypothesis',cid,evidence_id=e['id'],contract='linux-v1',number=1,text='가설',expected_source_types=['linux_detection'])
    def model(*args,**kwargs):
        c.store.update(e['id'],connected=False)
        return {'findings':[{'dossier_id':h['id'],'title':'기록','judgment':'미확인','reason':'검토','observation_ids':[o['id']]}]},{}
    monkeypatch.setattr('workbench.case_synthesis.Provider.generate',model)
    assert not tick(c,cid,e,t,[]) and not current(c,cid,t)
    assert c.store.list('receipt',cid)[-1]['failure_category']=='stale_scope'


def test_coverage_domains_and_caveats_do_not_invent_material_requests():
    from workbench.completion import materials
    rows=materials({'check_ledger':{'checks':[]},'hypotheses':[
        {'id':f'h{number}','number':number,'text':'조사 범위',
         'unavailable_materials':['명령 기록은 전송 성공 증거가 아님']}
        for number in range(1,11)]})
    assert rows==[]


def test_material_requests_keep_actual_blocked_question_contracts():
    from workbench.completion import materials
    rows=materials({'check_ledger':{'checks':[]},'test_intents':[
        {'id':'blocked','question_id':'question-a','admission':{
            'eligible':False,'reason':'capability_intent_mismatch','design':{
                'immediate_observable':'network_outcome','expected_update':'연결 성공 여부 판별'}}},
        {'id':'available','admission':{'eligible':True}}]})
    assert len(rows)==1
    assert rows[0]['reference']=='blocked' and rows[0]['question_id']=='question-a'
    assert rows[0]['material']=='network_outcome'
    assert rows[0]['blocked_conclusion']=='연결 성공 여부 판별'
    assert '기존 자료·도구' in rows[0]['action']


def test_material_requests_keep_unassessed_results_without_reacquisition():
    from workbench.completion import materials
    rows=materials({'check_ledger':{'checks':[
        {'scope_key':'done','job_ids':['job-a'],'contracts':[{'evaluation_status':'assessed'}]},
        {'scope_key':'pending','job_ids':['job-b'],'contracts':[{'evaluation_status':'pending'}],
         'request':{'tool':'read_file','path':'/var/log/example','reason':'승인 여부 검토'}}]}})
    assert len(rows)==1 and rows[0]['category']=='retained'
    assert rows[0]['reference']=='pending' and rows[0]['material']=='/var/log/example'
    assert '재평가' in rows[0]['action']


def test_report_keeps_history_separate_from_current_denominator(tmp_path):
    from workbench.dossiers import seed
    from workbench.reporting import report_document
    c,cid,e,t,o=setup(tmp_path);seed(c,cid,e,t)
    old=c.store.list('dossier',cid)
    t=c.store.update(t['id'],retry_generation=1);seed(c,cid,e,t)
    doc=report_document(c,cid)
    assert all(d['generation']==1 for d in doc['dossiers'])
    assert {d['id'] for d in doc['dossier_history']}=={d['id'] for d in old}
    assert doc['completion']['review_units']==len(doc['dossiers'])


def test_image_tool_planning_never_receives_another_images_source_paths(tmp_path):
    from workbench.investigation import evidence_pack
    c,cid,e,t,o=setup(tmp_path)
    other=c.store.add('evidence',cid,path='other.E01',signature='different',connected=True)
    foreign=c.store.add('observation',cid,evidence_id=other['id'],type='linux_detection',timestamp=None,
        source_location='/etc/foreign',fields={'path':'/etc/foreign'})
    packet=evidence_pack(c,cid,preferred=[foreign['id']],evidence_id=e['id'])
    assert o['id'] in {x['id'] for x in packet['observations']}
    assert foreign['id'] not in {x['id'] for x in packet['observations']}


def test_synthesis_rejects_unsupported_refutation(tmp_path,monkeypatch):
    from workbench.case_synthesis import tick,current
    c,cid,e,t,o=setup(tmp_path)
    h=c.store.add('hypothesis',cid,evidence_id=e['id'],contract='linux-v1',number=1,text='가설',expected_source_types=['linux_detection'])
    def model(*args,**kwargs):
        return {'findings':[{'dossier_id':h['id'],'title':'정황','judgment':'미확인','reason':'미확인','observation_ids':[o['id']]}],
            'supporting_evidence_ids':[],'refuting_evidence_ids':['foreign']},{}
    monkeypatch.setattr('workbench.case_synthesis.Provider.generate',model)
    assert not tick(c,cid,e,t,[]) and not current(c,cid,t)
    assert c.store.list('receipt',cid)[-1]['failure_category']=='citation_scope'


def test_ai_presented_denominator_includes_planning_and_synthesis(tmp_path):
    from workbench.reporting import report_document
    c,cid,e,t,o=setup(tmp_path)
    c.store.add('investigation_plan',cid,task_id=t['id'],valid_ids=[o['id']])
    c.store.add('synthesis_input',cid,task_id=t['id'],generation=0,pack={'observations':[o]})
    assert report_document(c,cid)['completion']['ai_presented_observations']==1
