from copy import deepcopy
import json

import pytest

from test_dossiers import setup
from workbench.dossiers import finish
from workbench.review_context import fit, InputBudgetError, model_view_size
from workbench import review_stream


def fixture(tmp_path, count=100, excerpt=None):
    c,cid,e,t,original=setup(tmp_path)
    s=c.store
    sources=[s.add('observation',cid,evidence_id=e['id'],type='linux_configuration',
        timestamp=None,source_location=f'/source/{i}',fields={'path':f'/source/{i}',
        'inode':i,'partition_offset':0,'source_sha256':f'{i:064x}',
        'time_record':{'file_mtime':{'value':'2026-09-01T00:00:00Z','basis':'filesystem metadata'}},
        'excerpt':excerpt if excerpt is not None else f'fact-{i} ' + 'a'*1000}) for i in range(count)]
    ids=[o['id'] for o in sources]
    d=s.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],generation=0,
        status='pending',title='Evidence changes',baseline=False,observation_ids=ids,
        total_records=count,group_key='synthetic')
    batch=s.add('dossier_batch',cid,task_id=t['id'],evidence_id=e['id'],generation=0,
        status='pending',round=2,attempts=0,dossier_ids=[d['id']],job_ids=[],output=None,deferred_checks=[])
    return c,cid,e,t,d,batch,sources


def result(pack):
    ids=[o['id'] for o in pack['observations']]
    return {'summary':'Only the provided source view was assessed.',
        'findings':[{'dossier_id':d['id'],'title':'Source view finding','judgment':'미확인',
            'reason':'This is a source-bounded conclusion, not absence of activity.',
            'observation_ids':ids[:1], 'counterevidence_ids':ids[-1:] if ids else [],
            'stages':[], 'alternatives':['Other explanations remain.'],'remaining_checks':[]}
            for d in pack['required_dossiers']], 'next_checks':[], 'check_assessments':[]}


def test_real_finish_pages_restart_and_only_synthesis_publishes(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path)
    before=deepcopy(sources); calls=[]
    def model(config,question,pack,**kwargs):
        assert model_view_size(pack) <= 36000
        calls.append(deepcopy(pack))
        return result(pack),{'output':result(pack)}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    assert finish(c,cid,e,t) is None
    stream=c.store.list('review_stream',cid)[0]
    pages=c.store.list('review_page',cid)
    assert len(pages)>1
    assert c.store.get(d['id'])['status']=='pending'
    assert not c.store.get(d['id']).get('assessment_history')
    assert not [r for r in c.store.list('receipt',cid) if r['receipt_type']=='dossier_model']
    assert c.store.get(b['id'])['attempts']==0
    assert len(c.store.list('review_input',cid))==1
    # New controller object simulates resumption without relying on an in-memory cursor.
    from workbench.controller import Controller
    c=Controller(c.store,tmp_path)
    for _ in range(30):
        finish(c,cid,e,t)
        if c.store.get(b['id'])['status']=='done':break
    assert c.store.get(b['id'])['status']=='done'
    assert c.store.get(d['id'])['status']=='reviewed'
    assert c.store.get(stream['id'])['status']=='synthesized'
    leaves=[p for p in calls if p['review_stream']['phase']=='source_page']
    assert len(leaves)==len(pages)
    assert [o['id'] for p in leaves for o in p['observations']]==[o['id'] for o in sources]
    assert all(p['finalization_allowed'] is False for p in leaves)
    final=calls[-1]
    assert final['review_stream']['phase']=='synthesis'
    assert final['review_stream']['pages_reviewed']==len(pages)
    assert final['review_stream']['source_observations_presented']==len(sources)
    assert len(c.store.list('review_input',cid))==len(c.store.list('review_page',cid))+1
    assert len([r for r in c.store.list('receipt',cid) if r['receipt_type']=='dossier_model'])==1
    assert [c.store.get(o['id']) for o in sources]==before


def test_references_include_counterevidence_stage_fact_and_check_sources():
    output={'findings':[{'observation_ids':['positive'],'counterevidence_ids':['negative'],
        'stages':[{'observation_ids':['stage']}],
        'fact_assertions':[{'observation_id':'literal'}]}],
        'check_assessments':[{'observation_ids':['check']}]}
    assert review_stream.references(output)==['positive','negative','stage','literal','check']


def test_irreducible_sources_are_refocused_from_originals_then_synthesized(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path,4,excerpt='recorded fact\n'+'unselected context '*900)
    before=[c.store.get(source['id']) for source in sources]
    seen=[]
    def model(config,question,pack,**kwargs):
        seen.append(deepcopy(pack))
        out=result(pack)
        out['findings'][0]['counterevidence_ids']=[]
        out['findings'][0]['observation_ids']=[o['id'] for o in pack['observations']]
        if pack['review_stream']['phase']=='focus_page':
            out['excerpt_selections']=[{'observation_id':o['id'],'quote':'recorded fact',
                'reason':'Exact narrow proposition'} for o in pack['observations']]
        return out,{'output':out}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    for _ in range(25):
        finish(c,cid,e,t)
        if c.store.get(b['id'])['status']=='done':break
    assert c.store.get(b['id'])['status']=='done'
    focused=[p for p in seen if p['review_stream']['phase']=='focus_page']
    assert focused and all('unselected context' in str(p['observations']) for p in focused)
    assert seen[-1]['review_stream']['phase']=='synthesis'
    assert 'unselected context' not in str(seen[-1]['observations'])
    assert all(p['finalization_allowed'] is False for p in seen[:-1])
    assert len([r for r in c.store.list('receipt',cid) if r['receipt_type']=='dossier_model'])==1
    assert [c.store.get(source['id']) for source in sources]==before


def test_bad_exact_selection_is_rejected_before_page_or_objection_adoption(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path)
    def model(config,question,pack,**kwargs):
        out=result(pack)
        out['excerpt_selections']=[{'observation_id':pack['observations'][0]['id'],
            'quote':'invented words not found in source','reason':'Not a valid selection'}]
        return out,{'output':out}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    finish(c,cid,e,t)
    assert all(p['status']=='pending' for p in c.store.list('review_page',cid))
    assert not c.store.list('objection',cid)
    receipt=c.store.list('receipt',cid)[-1]
    assert any(e['code']=='excerpt_selection_invalid' for e in receipt['validation_errors'])


def test_comparison_can_pack_nonadjacent_pages_without_losing_children(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path,3)
    stream=c.store.add('review_stream',cid,task_id=t['id'],batch_id=b['id'],level=0)
    pages=[{'id':str(i),'included_ids':[str(i)],'covered_source_count':1} for i in range(3)]
    def pack(stream,children,final=False):
        if [p['id'] for p in children]!=['0','2']:raise InputBudgetError('synthetic envelope floor')
        return {'child_sources':['0','2']}
    monkeypatch.setattr(review_stream,'reduction_pack',pack)
    review_stream._schedule_comparisons(c.store,cid,stream,pages)
    parent=c.store.list('review_page',cid)[0]
    assert parent['child_ids']==['0','2']
    assert c.store.get(stream['id'])['frontier']==[parent['id'],'1']


def test_comparison_retains_prior_alternatives_and_immutable_narrative_pointer():
    observation={'id':'o','fields':{'excerpt':'unchanged source'}}
    prior={'summary':'old interpretation','findings':[{'dossier_id':'d','reason':'old reasoning',
        'alternatives':['legitimate use'],'remaining_checks':['verify authorization'],'counterevidence_ids':['o']}]}
    canonical={'observations':[observation],'previous_assessment':prior,'required_dossiers':[],
        'allowed_observation_ids_by_dossier':{},'executed_checks':[]}
    stream={'id':'s','canonical':deepcopy(canonical),'canonical_sha256':'hash','page_count':1,'maximum':36000}
    page={'id':'p','receipt_id':'r','included_ids':['o'],'pack':{'observations':[observation]},
        'output':{'findings':[{'observation_ids':['o']}],'check_assessments':[]}}
    pack=review_stream.reduction_pack(stream,[page])
    assert pack['observations']==[observation]
    assert pack['prior_assessment_context']['unresolved_context'][0]['alternatives']==['legitimate use']
    assert pack['prior_assessment_context']['retrieval']=='canonical_pack.previous_assessment'
    assert stream['canonical']==canonical


def test_rejected_page_does_not_advance_or_publish(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path)
    def model(config,question,pack,**kwargs):
        output=result(pack);output['findings'][0]['observation_ids']=['not-provided']
        return output,{'output':output}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    finish(c,cid,e,t)
    assert all(p['status']=='pending' for p in c.store.list('review_page',cid))
    assert c.store.get(b['id'])['attempts']==1
    assert c.store.get(d['id'])['status']=='pending'
    assert not c.store.get(d['id']).get('finding')
    assert c.store.list('review_diagnostic',cid)[0]['failure_category']=='citation_scope'


def test_transport_rejection_preserves_reference_binding_for_replay(tmp_path,monkeypatch):
    from workbench.provider import ModelOutputError
    c,cid,e,t,d,b,sources=fixture(tmp_path)
    metadata={'reference_projection':{'version':'model-references-1', 'bindings':{'R1':sources[0]['id']}}}
    def reject(*args,**kwargs):
        raise ModelOutputError('unknown handle','{"observation_ids":["R999"]}', 'output_reference', metadata)
    monkeypatch.setattr('workbench.dossiers.consult',reject)
    finish(c,cid,e,t)
    diagnostic=c.store.list('review_diagnostic',cid)[0]
    assert diagnostic['model_metadata']==metadata
    assert 'R999' in diagnostic['raw_output']
    assert c.store.get(d['id'])['status']=='pending'


def test_irreducible_final_scope_is_explicit_gap_not_partial_page_adoption(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path)
    monkeypatch.setattr('workbench.dossiers.consult',lambda config,question,pack,**kwargs:(result(pack),{'output':result(pack)}))
    finish(c,cid,e,t)
    def cannot_reduce(*args,**kwargs):raise InputBudgetError('source identity budget floor')
    monkeypatch.setattr(review_stream,'reduction_pack',cannot_reduce)
    for _ in range(30):
        finish(c,cid,e,t)
        if c.store.get(b['id'])['status']=='input_projection_blocked':break
    assert c.store.get(b['id'])['status']=='input_projection_blocked'
    assert c.store.get(b['id'])['attempts']==2
    assert not c.store.get(d['id']).get('finding')
    assert all(p['status']=='reviewed' for p in c.store.list('review_page',cid) if not p.get('focus_of'))
    assert all(p['status']=='pending' for p in c.store.list('review_page',cid) if p.get('focus_of'))
    assert all(r['receipt_type']!='dossier_model' for r in c.store.list('receipt',cid))


def test_large_notebook_uses_durable_comparisons_before_final_synthesis(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path,260)
    actual_reduction=review_stream.reduction_pack
    def limited_note_envelope(stream,pages,final=True):
        # Model a finite note envelope while retaining the real source fitting
        # and validation path for each bounded comparison.
        if len(pages)>2:raise InputBudgetError('bounded notebook capacity')
        return actual_reduction(stream,pages,final=final)
    monkeypatch.setattr(review_stream,'reduction_pack',limited_note_envelope)
    seen=[]
    def model(config,question,pack,**kwargs):
        seen.append(deepcopy(pack))
        output=result(pack)
        # A discovered objection is deliberately omitted by every later
        # intermediate model. The ledger, not this notebook, must preserve it.
        if len(seen)>1:output['findings'][0]['counterevidence_ids']=[]
        return output,{'output':output}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    for _ in range(50):
        finish(c,cid,e,t)
        if c.store.get(b['id'])['status']=='done':break
    assert c.store.get(b['id'])['status']=='done'
    nodes=c.store.list('review_page',cid)
    comparisons=[n for n in nodes if n.get('level',0)>0]
    assert comparisons and all(n['status']=='reviewed' and len(n['child_ids'])>=2 for n in comparisons)
    assert sum(n['covered_source_count'] for n in nodes if n['level']==0)==len(sources)
    stream=c.store.get(c.store.get(b['id'])['review_stream_id'])
    assert sum(c.store.get(n)['covered_source_count'] for n in stream['frontier'])==len(sources)
    assert any(p['review_stream']['phase']=='comparison_page' for p in seen)
    assert all(p['finalization_allowed'] is False for p in seen[:-1])
    assert seen[-1]['review_stream']['phase']=='synthesis'
    assert len([r for r in c.store.list('receipt',cid) if r['receipt_type']=='dossier_model'])==1
    original_counter=seen[0]['observations'][-1]['id']
    assert original_counter in {o['id'] for o in seen[-1]['observations']}
    assert seen[-1]['open_objections']
    final=c.store.get(d['id'])['finding']
    assert final['publication_status']=='qualified_open_objections'
    assert original_counter in final['open_objections'][0]['observation_ids']


def test_new_source_membership_invalidates_durable_canonical_before_next_call(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path)
    monkeypatch.setattr('workbench.dossiers.consult',lambda config,question,pack,**kw:(result(pack),{}))
    finish(c,cid,e,t)
    old_stream=c.store.get(b['id'])['review_stream_id']
    extra=c.store.add('observation',cid,evidence_id=e['id'],type='linux_configuration',timestamp=None,
        source_location='/new-source',fields={'path':'/new-source','excerpt':'New contrary scope.'})
    c.store.update(d['id'],observation_ids=d['observation_ids']+[extra['id']])
    finish(c,cid,e,t)
    new_stream=c.store.get(b['id'])['review_stream_id']
    assert new_stream!=old_stream
    assert c.store.get(old_stream)['status']=='superseded'
    assert extra['id'] in {o['id'] for o in c.store.get(new_stream)['canonical']['observations']}
    assert any(x.get('failure_category')=='stale_review_stream' for x in c.store.list('review_diagnostic',cid))
    assert not c.store.get(d['id']).get('finding')


def test_mid_call_dependency_change_rejects_page_adoption(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path)
    def model(config,question,pack,**kw):
        c.store.update(d['id'],observation_ids=d['observation_ids'][:-1])
        return result(pack),{}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    finish(c,cid,e,t)
    assert not any(p['status']=='reviewed' for p in c.store.list('review_page',cid))
    assert not c.store.list('objection',cid)
    assert c.store.list('review_diagnostic',cid)[-1]['rejection']=='stale_review_scope'


def test_pending_page_discloses_objections_outside_its_source_scope(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path)
    calls=[]
    def model(config,question,pack,**kw):
        calls.append(deepcopy(pack))
        return result(pack),{}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    finish(c,cid,e,t);finish(c,cid,e,t)
    assert calls[1]['unpresented_open_objection_count']>=1
    assert len(c.store.list('objection',cid))>=1


def test_source_page_does_not_prime_prior_narrative_as_current_evidence(tmp_path,monkeypatch):
    c,cid,e,t,d,b,sources=fixture(tmp_path)
    old={'summary':'PRIOR-NARRATIVE-NOT-SOURCE','findings':[{'dossier_id':d['id'],
        'title':'Previous interpretation','reason':'PRIOR-NARRATIVE-NOT-SOURCE',
        'alternatives':['legitimate activity'],'remaining_checks':['verify execution'],
        'counterevidence_ids':[]}]}
    c.store.update(b['id'],output=old)
    calls=[]
    def model(config,question,pack,**kw):
        calls.append(pack);return result(pack),{}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    finish(c,cid,e,t)
    page=calls[0]
    assert page['review_stream']['phase']=='source_page'
    assert 'previous_assessment' not in page
    assert 'PRIOR-NARRATIVE-NOT-SOURCE' not in str(page)
    assert page['prior_assessment_context']['unresolved_context'][0]['alternatives']==['legitimate activity']
    stream=c.store.list('review_stream',cid)[0]
    assert stream['canonical']['previous_assessment']==old


def test_mandatory_objection_sources_cannot_be_trimmed_to_force_synthesis():
    rows=[{'id':str(i),'fields':{'excerpt':'evidence '+str(i)+'abcdefghij'*500}} for i in range(10)]
    stream={'id':'stream','maximum':10000,'page_count':10,'canonical_sha256':'canonical',
        'canonical':{'observations':rows,'required_dossiers':[],'executed_checks':[],
            'allowed_observation_ids_by_dossier':{}},
        'open_objections':[{'id':'objection','observation_ids':[o['id'] for o in rows]}]}
    before=deepcopy(stream)
    with pytest.raises(InputBudgetError):review_stream.reduction_pack(stream,[])
    assert stream==before
