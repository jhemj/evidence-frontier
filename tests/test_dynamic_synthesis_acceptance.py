import pytest
from workbench.store import Store
from workbench.controller import Controller
from workbench.hypothesis_ledger import apply
from workbench.case_synthesis import tick
from workbench.triage import project


@pytest.mark.parametrize('stale', [False,True])
def test_dynamic_final_synthesis_is_atomic_and_reaches_card_projection(tmp_path,monkeypatch,stale):
    c=Controller(Store(tmp_path/'case.db'),tmp_path);s=c.store
    case=c.create('synthetic hypothesis acceptance','','standard');cid=case['id'];s.update(cid,status='running')
    e=s.add('evidence',cid,path='synthetic',signature='synthetic',connected=True)
    t=s.add('task',cid,evidence_id=e['id'],action='linux_investigate',retry_generation=0,status='partial')
    o=s.add('observation',cid,evidence_id=e['id'],type='linux_configuration',timestamp=None,source_location='synthetic:1',fields={'path':'/synthetic','excerpt':'a configured invocation'})
    p=s.add('investigation_plan',cid,task_id=t['id'],generation=0)
    h=apply(s,cid,e['id'],{'action':'create','title':'호출이 예약되어 있는가?','judgment':'유력','reasoning':'설정 원문 대조',
        'supporting_evidence_ids':[o['id']]},[o['id']],task_id=t['id'],source_plan_id=p['id'])
    assert h
    jt=s.add('task',cid,evidence_id=e['id'],action='ai_judgment',retry_generation=0,status='running')
    s.add('config','',provider={'model':'local','base_url':'http://localhost:11434','protocol':'ollama'})
    def consult(*args,**kwargs):
        if stale:s.update(jt['id'],retry_generation=1)
        f={'dossier_id':h['id'],'title':'예약 호출 설정 확인, 실제 실행은 미확인','card_summary':'원문에서 예약 호출 설정을 확인했습니다. 실제 실행 결과는 확인하지 못했습니다.',
           'judgment':'확인','reason':'설정 원문이 직접 뒷받침함','observation_ids':[o['id']],
           'stages':[{'stage':'configuration','judgment':'확인','statement':'예약 호출 설정','observation_ids':[o['id']]}],
           'timeline_role':'핵심','alternatives':[],'remaining_checks':['실행 기록 확보']}
        return {'summary':'좁은 설정 사실만 확인','findings':[f],'supporting_evidence_ids':[o['id']],'refuting_evidence_ids':[],'next_checks':[]},{}
    monkeypatch.setattr('workbench.case_synthesis.consult',consult)
    assert tick(c,cid,e,jt,[]) is False
    current=s.get(h['id'])
    if stale:
        assert current['revision']==1 and not s.list('case_synthesis',cid)
    else:
        assert current['revision']==2 and current['lifecycle']=='supported'
        assert len(s.list('case_synthesis',cid))==1
        assert tick(c,cid,e,jt,[]) is True  # no duplicate final revision
        document={k:s.list(k,cid) for k in ('evidence','task','observation','hypothesis','case_synthesis')}
        card=project(document)['cards'][0]
        assert card['id']==h['hypothesis_card_id'] and card['lifecycle']=='supported'
        assert card['title']==current['title'] and len(card['change_history'])==2
