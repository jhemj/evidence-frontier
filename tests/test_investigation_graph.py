import json
import pytest
from workbench.store import Store
from workbench.controller import Controller
from workbench.investigation import seed
from workbench.investigation_graph import tick, digest

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
    assert result['domains_requested']==10 and result['domains_assessed']==0
    expected_calls=4 if keep_exploring=='repeat' else 6 if keep_exploring else 4+int(fail_first)
    assert len(model_calls)==expected_calls
    assert len(submissions)==(2 if keep_exploring=='repeat' else 6 if keep_exploring else 1)
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
