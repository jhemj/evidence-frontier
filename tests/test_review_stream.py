from copy import deepcopy
import json

import pytest

from test_dossiers import setup
from workbench.dossiers import finish
from workbench.review_context import fit, InputBudgetError
from workbench import review_stream


def fixture(tmp_path, count=100):
    c,cid,e,t,original=setup(tmp_path)
    s=c.store
    sources=[s.add('observation',cid,evidence_id=e['id'],type='linux_configuration',
        timestamp=None,source_location=f'/source/{i}',fields={'path':f'/source/{i}',
        'inode':i,'partition_offset':0,'source_sha256':f'{i:064x}',
        'time_record':{'file_mtime':{'value':'2026-09-01T00:00:00Z','basis':'filesystem metadata'}},
        'excerpt':f'fact-{i} ' + 'a'*1000}) for i in range(count)]
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
        assert len(json.dumps(pack,ensure_ascii=False,separators=(',',':'))) <= 36000
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
    assert len(c.store.list('review_input',cid))==len(pages)+1
    assert len([r for r in c.store.list('receipt',cid) if r['receipt_type']=='dossier_model'])==1
    assert [c.store.get(o['id']) for o in sources]==before


def test_references_include_counterevidence_stage_fact_and_check_sources():
    output={'findings':[{'observation_ids':['positive'],'counterevidence_ids':['negative'],
        'stages':[{'observation_ids':['stage']}],
        'fact_assertions':[{'observation_id':'literal'}]}],
        'check_assessments':[{'observation_ids':['check']}]}
    assert review_stream.references(output)==['positive','negative','stage','literal','check']


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
    assert c.store.get(b['id'])['attempts']==0
    assert not c.store.get(d['id']).get('finding')
    assert all(p['status']=='reviewed' for p in c.store.list('review_page',cid))
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
        return result(pack),{'output':result(pack)}
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
