from copy import deepcopy
from test_dossiers import setup
from workbench.dossiers import relevant_jobs, split_assessment, finish
from workbench.review_contracts import contract


def test_related_jobs_preserve_shared_and_unknown_but_exclude_siblings(tmp_path):
    c,cid,e,t,o=setup(tmp_path);s=c.store
    def job(owners):
        return s.add('investigation_job',cid,task_id=t['id'],evidence_id=e['id'],request={'tool':'search','query':'synthetic'},
            status='ingested',contracts=[contract({'hypothesis_id':x,'success_condition':'literal match'}) for x in owners])
    own=job(['a']);other=job(['b']);shared=job(['a','b']);legacy=job([])
    batch={'dossier_ids':['a'],'job_ids':[x['id'] for x in (own,other,shared,legacy)]}
    before=deepcopy(batch)
    assert [j['id'] for j in relevant_jobs(s,batch)]==[x['id'] for x in (own,shared,legacy)]
    assert batch==before and len(s.list('investigation_job',cid))==4


def test_split_assessment_keeps_own_fact_history_and_check_assessments():
    output={'summary':'two units','findings':[{'dossier_id':'a','observation_ids':['oa']},{'dossier_id':'b','observation_ids':['ob']}],
        'check_assessments':[{'dossier_id':'a','contract_id':'ca'},{'dossier_id':'b','contract_id':'cb'}],
        'next_checks':[{'hypothesis_id':'a'},{'hypothesis_id':'b'}]}
    before=deepcopy(output);result=split_assessment(output,'a')
    assert result['findings']==output['findings'][:1]
    assert result['check_assessments']==output['check_assessments'][:1]
    assert result['next_checks']==output['next_checks'][:1] and output==before


def test_actual_batch_split_rebinds_related_results_and_keeps_prior_assessment(tmp_path):
    c,cid,e,t,o=setup(tmp_path);s=c.store;t=s.update(t['id'],review_policy='autonomous-v1')
    ds=[s.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],generation=0,status='pending',
              title='Synthetic',baseline=False,observation_ids=[o['id']]) for _ in range(2)]
    jobs=[s.add('investigation_job',cid,task_id=t['id'],evidence_id=e['id'],generation=0,status='ingested',
        request={'tool':'read_file','path':'/synthetic/'+str(i),'hypothesis_id':d['id']},
        contracts=[contract({'hypothesis_id':d['id']})],observation_ids=[o['id']]) for i,d in enumerate(ds)]
    parent=s.add('dossier_batch',cid,task_id=t['id'],evidence_id=e['id'],generation=0,
        status='pending',round=1,attempts=2,dossier_ids=[d['id'] for d in ds],job_ids=[j['id'] for j in jobs],
        output={'findings':[{'dossier_id':d['id'],'observation_ids':[o['id']]} for d in ds]},
        deferred_checks=[{'request':{'hypothesis_id':d['id']},'reason':'budget'} for d in ds])
    assert finish(c,cid,e,t) is None
    children=[b for b in s.list('dossier_batch',cid) if b.get('parent_batch_id')==parent['id']]
    assert len(children)==2
    for b in children:
        idx=[d['id'] for d in ds].index(b['dossier_ids'][0])
        assert b['job_ids']==[jobs[idx]['id']] and b['round']==1
        assert b['output']['findings'][0]['dossier_id']==ds[idx]['id']
        assert len(b['output']['findings'])==1 and len(b['deferred_checks'])==1
