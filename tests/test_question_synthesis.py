"""Synthetic regression coverage for checkpointed question synthesis."""
from copy import deepcopy

from test_dossiers import setup
from workbench import case_synthesis


def context(tmp_path, count=20):
    controller, cid, evidence, task, original = setup(tmp_path)
    store = controller.store
    observations = [original]
    for i in range(count - 1):
        observations.append(store.add('observation', cid, evidence_id=evidence['id'],
            type='linux_command', timestamp=None, source_location=f'fixture:{i}',
            fields={'path': f'/var/log/product-{i}', 'excerpt': 'productX invoked ' + 'abcdefghij' * 250,
                    'object_id': 'obj-1'}))
    hypothesis = store.add('hypothesis', cid, evidence_id=evidence['id'], contract='linux-v1',
        number=1, hypothesis_kind='coverage_domain', text='productX execution', status='open',
        judgment='미확인', expected_source_types=['linux_command'], observation_ids=[],
        supporting_evidence_ids=[], refuting_evidence_ids=[], competing_explanations=['maintenance'],
        unavailable_materials=['execution exit record'])
    dossier = store.add('dossier', cid, task_id=task['id'], evidence_id=evidence['id'], generation=0,
        status='reviewed', title='productX', baseline=False, observation_ids=[o['id'] for o in observations],
        total_records=len(observations), finding={'observation_ids':[observations[0]['id']],
        'judgment':'미확인','title':'prior','reason':'bounded'}, group_key='question')
    return controller, cid, evidence, task, hypothesis, dossier, observations


def model_for_page(pack, next_checks=None):
    dossier = pack['required_dossiers'][0]
    ids = [o['id'] for o in pack['observations']]
    finding = {'dossier_id': dossier['id'], 'title': 'productX source view', 'judgment': '미확인',
        'reason': 'Only the supplied bounded source view was assessed.', 'observation_ids': ids[:1],
        'counterevidence_ids': [], 'stages': [], 'alternatives': ['maintenance'], 'remaining_checks': []}
    return {'summary':'bounded synthesis', 'findings':[finding], 'supporting_evidence_ids':ids[:1],
        'refuting_evidence_ids':[], 'next_checks':next_checks or [], 'check_assessments':[]}, {'usage':{}}


def test_more_than_sixteen_sources_pages_then_single_final_record(tmp_path, monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path, 20)
    calls=[]
    monkeypatch.setattr(case_synthesis, 'consult', lambda config,question,pack,**kw:
        (calls.append(deepcopy(pack)) or model_for_page(pack)))
    for _ in range(60):
        if case_synthesis.tick(c,cid,e,t,[d]): break
    records=c.store.list('case_synthesis',cid)
    assert records and records[-1]['hypothesis_id']==h['id']
    assert len([x for x in calls if x.get('review_stream',{}).get('phase')=='source_page']) > 1
    assert calls[-1].get('review_stream',{}).get('phase')=='synthesis'
    assert c.store.get(d['id'])['finding']['observation_ids']


def test_reentry_with_same_source_revision_does_not_duplicate_synthesis(tmp_path, monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path, 3)
    calls=[]
    monkeypatch.setattr(case_synthesis, 'consult', lambda config,question,pack,**kw:
        (calls.append(pack) or model_for_page(pack)))
    assert case_synthesis.tick(c,cid,e,t,[d]) is False
    first=len(c.store.list('case_synthesis',cid)); call_count=len(calls)
    assert case_synthesis.tick(c,cid,e,t,[d]) is True
    assert len(c.store.list('case_synthesis',cid))==first
    assert len(calls)==call_count


def test_synthesis_page_rejects_unbound_excerpt_before_adopting_notes(tmp_path,monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path,20)
    def model(config,question,pack,**kwargs):
        output,metadata=model_for_page(pack)
        output['excerpt_selections']=[{'observation_id':pack['observations'][0]['id'],
            'quote':'fabricated excerpt which is not in this source','reason':'invalid'}]
        return output,metadata
    monkeypatch.setattr(case_synthesis,'consult',model)
    case_synthesis.tick(c,cid,e,t,[d])
    assert not c.store.list('case_synthesis',cid)
    assert not c.store.list('objection',cid)
    assert all(p['status']=='pending' for p in c.store.list('review_page',cid))
    receipt=c.store.list('receipt',cid)[-1]
    assert any(e['code']=='excerpt_selection_invalid' for e in receipt['validation_errors'])


def test_direct_final_synthesis_also_rejects_unbound_optional_selection(tmp_path,monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path,3)
    def model(config,question,pack,**kwargs):
        output,metadata=model_for_page(pack)
        output['excerpt_selections']=[{'observation_id':pack['observations'][0]['id'],
            'quote':'fabricated source quote','reason':'invalid'}]
        return output,metadata
    monkeypatch.setattr(case_synthesis,'consult',model)
    case_synthesis.tick(c,cid,e,t,[d])
    assert not c.store.list('case_synthesis',cid)
    assert any(e['code']=='excerpt_selection_invalid' for e in c.store.list('receipt',cid)[-1]['validation_errors'])


def test_stale_source_added_mid_model_is_not_accepted(tmp_path, monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path, 3)
    original=case_synthesis.consult
    def model(config,question,pack,**kw):
        changed=deepcopy(c.store.get(observations[0]['id']))
        fields={**changed['fields'],'time_record':{'event':'late-source-change'}}
        c.store.add('observation',cid,evidence_id=e['id'],type=changed['type'],timestamp=None,
            source_location='fixture:new-clock',fields=fields)
        return model_for_page(pack)
    monkeypatch.setattr(case_synthesis,'consult',model)
    case_synthesis.tick(c,cid,e,t,[d])
    assert not c.store.list('case_synthesis',cid)
    assert any(r.get('failure_category')=='stale_scope' for r in c.store.list('receipt',cid))


def test_foreign_open_objection_is_not_silently_attached(tmp_path, monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path, 3)
    foreign=c.store.add('objection',cid,task_id=t['id'],generation=0,dossier_id='foreign',
        fingerprint='foreign',statement='foreign scope',observation_ids=[observations[0]['id']],
        span_ids=[],status='open',assessment_receipt_id='r')
    seen=[]
    monkeypatch.setattr(case_synthesis,'consult',lambda config,question,pack,**kw:
        (seen.append(pack) or model_for_page(pack)))
    case_synthesis.tick(c,cid,e,t,[d])
    assert all(o['id'] != foreign['id'] for o in seen[0].get('open_objections',[]))
    assert c.store.get(foreign['id'])['status']=='open'


def test_next_check_creates_one_followup_dossier_and_reentry_is_bounded(tmp_path, monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path, 3)
    calls=[]
    def model(config,question,pack,**kw):
        calls.append(pack)
        check={'tool':'read_file','path':'/var/log/product-0','query':'',
            'hypothesis_id':h['id'],'reason':'Distinguish invocation from execution',
            'success_condition':'A positive execution record is present',
            'refutation_condition':'A positive failed execution record is present'}
        return model_for_page(pack, [check])
    monkeypatch.setattr(case_synthesis,'consult',model)
    case_synthesis.tick(c,cid,e,t,[d])
    followups=[x for x in c.store.list('dossier',cid) if x.get('origin_hypothesis_id')==h['id']]
    assert len(followups)==1
    assert len(c.store.list('investigation_job',cid))==1
    # Re-entering before a worker result must not create another logical unit.
    case_synthesis.tick(c,cid,e,t,[d]+followups)
    assert len([x for x in c.store.list('dossier',cid) if x.get('origin_hypothesis_id')==h['id']])==1


def test_followup_ingest_reenters_synthesis_once(tmp_path, monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path, 3)
    calls=[]
    def model(config,question,pack,**kw):
        calls.append(pack)
        if len(calls)==1:
            check={'tool':'read_file','path':'/var/log/product-0','query':'',
                'hypothesis_id':h['id'],'reason':'Distinguish invocation from execution',
                'success_condition':'A positive execution record is present',
                'refutation_condition':'A positive failed execution record is present'}
        else: check=None
        return model_for_page(pack, [check] if check else [])
    monkeypatch.setattr(case_synthesis,'consult',model)
    case_synthesis.tick(c,cid,e,t,[d])
    followup=next(x for x in c.store.list('dossier',cid) if x.get('origin_hypothesis_id')==h['id'])
    job=c.store.list('investigation_job',cid)[0]
    extra=c.store.add('observation',cid,evidence_id=e['id'],type='linux_tool_result',timestamp=None,
        source_location='fixture:followup',fields={'path':'/var/log/product-0','excerpt':'positive execution record'})
    c.store.update(job['id'],status='ingested',result_status='covered',observation_ids=[extra['id']])
    c.store.update(followup['id'],status='reviewed',observation_ids=list(dict.fromkeys(followup['observation_ids']+[extra['id']])),
        finding={'observation_ids':[extra['id']],'judgment':'미확인','title':'followup','reason':'bounded'})
    before=len(c.store.list('case_synthesis',cid))
    for _ in range(3):
        case_synthesis.tick(c,cid,e,t,[d,followup])
    assert len(c.store.list('case_synthesis',cid))==before+1
    assert len(calls)==2


def test_source_page_rejects_same_object_added_while_model_is_waiting(tmp_path, monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path, 20)
    calls=[]
    def model(config,question,pack,**kw):
        calls.append(pack)
        # A new physical observation for the same retained path/object arrives
        # while the page model is in flight. Production must not accept this
        # page against the old source revision.
        c.store.add('observation',cid,evidence_id=e['id'],type='linux_command',timestamp=None,
            source_location='fixture:new-same-object',fields={'path':'/var/log/product-0',
            'excerpt':'late source bytes','object_id':'obj-1'})
        return model_for_page(pack)
    monkeypatch.setattr(case_synthesis,'consult',model)
    case_synthesis.tick(c,cid,e,t,[d])
    assert not c.store.list('case_synthesis',cid)
    assert any(r.get('failure_category')=='stale_scope' for r in c.store.list('receipt',cid))
    assert all(p['status']=='pending' for p in c.store.list('review_page',cid))


def test_final_counterevidence_opens_qualified_objection(tmp_path, monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path, 3)
    def model(config,question,pack,**kw):
        ids=[o['id'] for o in pack['observations']]
        finding={'dossier_id':pack['required_dossiers'][0]['id'],'title':'bounded finding',
            'judgment':'미확인','reason':'A contrary source remains unresolved.',
            'observation_ids':ids[:1],'counterevidence_ids':ids[-1:], 'stages':[],
            'alternatives':['maintenance'],'remaining_checks':[]}
        return {'summary':'bounded','findings':[finding],'supporting_evidence_ids':ids[:1],
            'refuting_evidence_ids':[],'next_checks':[],'check_assessments':[]}, {'usage':{}}
    monkeypatch.setattr(case_synthesis,'consult',model)
    case_synthesis.tick(c,cid,e,t,[d])
    record=c.store.list('case_synthesis',cid)[0]
    assert record['status']=='objections_open'
    assert record['finding']['publication_status']=='qualified_open_objections'
    assert record['finding']['open_objections']


def test_final_cannot_resolve_objection_owned_by_other_dossier(tmp_path, monkeypatch):
    c,cid,e,t,h,d,observations=context(tmp_path, 3)
    foreign=c.store.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],generation=0,
        status='reviewed',title='other',baseline=False,observation_ids=[observations[0]['id']],
        total_records=1,finding={'observation_ids':[observations[0]['id']], 'judgment':'미확인'})
    objection=c.store.add('objection',cid,task_id=t['id'],generation=0,dossier_id=foreign['id'],
        fingerprint='foreign-final',statement='other scope contrary',observation_ids=[observations[0]['id']],
        span_ids=[],status='open',assessment_receipt_id='r')
    def model(config,question,pack,**kw):
        ids=[o['id'] for o in pack['observations']]
        return {'summary':'bounded','findings':[{'dossier_id':pack['required_dossiers'][0]['id'],'title':'f',
            'judgment':'미확인','reason':'bounded','observation_ids':ids[:1],'stages':[],
            'alternatives':['other'],'remaining_checks':[]}], 'supporting_evidence_ids':ids[:1],
            'refuting_evidence_ids':[], 'next_checks':[], 'check_assessments':[],
            'objection_assessments':[{'objection_id':objection['id'],'outcome':'resolved',
                'reason':'unrelated source','basis':'positive_evidence','observation_ids':ids[:1]}]}, {'usage':{}}
    monkeypatch.setattr(case_synthesis,'consult',model)
    case_synthesis.tick(c,cid,e,t,[d,foreign])
    assert not c.store.list('case_synthesis',cid)
    assert c.store.get(objection['id'])['status']=='open'
    assert any(r.get('failure_category') in ('citation_scope','stale_scope')
               for r in c.store.list('receipt',cid))
