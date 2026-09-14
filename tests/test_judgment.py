import pytest
from workbench.controller import Controller
from workbench.store import Store
from workbench.judgment import finish, current, bound_absence
from workbench.reporting import report_document, render


def fixture(tmp_path):
    c=Controller(Store(tmp_path/'case.sqlite3'),tmp_path)
    case=c.create('Automatic results','','standard');cid=case['id']
    ev=c.store.add('evidence',cid,path='fixture.E01',name='fixture.E01',connected=True)
    task=c.store.add('task',cid,evidence_id=ev['id'],action='ai_judgment')
    ob=c.store.add('observation',cid,evidence_id=ev['id'],type='linux_command',timestamp=None,
        source_location='fixture:/var/log/commands:line:1',fields={'command':'scp file host:path','stage':'명령 기록'})
    c.store.add('config','',provider={'model':'local','base_url':'http://localhost:11434','protocol':'ollama'})
    return c,cid,ev,task,ob


def test_execution_absence_is_not_confirmed_from_missing_logs():
    result=bound_absence({'title':'스크립트 실행 부재','judgment':'확인','reason':'실행되지 않았음','observation_ids':[], 'remaining_checks':[]})
    assert result['judgment']=='미확인'
    assert '기록의 부재만으로' in result['reason']
    quoted={'title':'파일 주석 존재 확인','judgment':'확인',
        'reason':'주석이 존재하지만 실제로 실행되지 않았음을 증명하는 것이 아님.'}
    assert bound_absence(quoted)==quoted


def test_automatic_three_levels_need_no_analyst_action(tmp_path,monkeypatch):
    c,cid,ev,task,ob=fixture(tmp_path);calls=[]
    def model(self,question,pack,role):
        calls.append(role)
        output={'summary':'확인한 기록과 남은 불확실성을 구분했습니다.','findings':[
            {'title':'전송 명령이 기록됨','judgment':'확인','reason':'명령 원문 존재','observation_ids':[ob['id']],'alternatives':[],'remaining_checks':[]},
            {'title':'파일 전송 시도가 유력함','judgment':'유력','reason':'전송 도구와 목적지 인자가 함께 기록됨','observation_ids':[ob['id']],'alternatives':['입력만 했을 가능성'],'remaining_checks':['실행 로그']},
            {'title':'전송 성공 여부','judgment':'미확인','reason':'결과 기록 없음','observation_ids':[],'alternatives':[],'remaining_checks':['상대 서버 기록']} ]}
        return output,{'output':output,'usage':{'eval_count':100}}
    monkeypatch.setattr('workbench.judgment.Provider.generate',model)
    result=finish(c,cid,ev,task)
    assert result['judgment_counts']=={'확인':1,'유력':1,'미확인':1}
    assert finish(c,cid,ev,task)==result and calls==['judgment']
    assert not c.store.list('claim',cid)  # No approve/reject workflow required.
    doc=report_document(c,cid);html=render(doc)
    assert len(doc['judgments'])==1
    assert '파일 전송 시도가 유력함' in html and '실행 로그' in html
    assert '분석가 승인' not in html
    c.store.update(ev['id'],connected=False)
    assert not current(c,cid) and not report_document(c,cid)['judgments']


@pytest.mark.parametrize('invalid_ids',[[],['OBSERVATION-not-supplied']])
def test_unsupported_confirmed_judgment_retries_with_bounded_budget(tmp_path,monkeypatch,invalid_ids):
    c,cid,ev,task,ob=fixture(tmp_path)
    def model(*args,**kwargs):
        output={'summary':'bad','findings':[{'title':'Claim','judgment':'확인','reason':'bad reference','observation_ids':invalid_ids,'alternatives':[],'remaining_checks':[]}]}
        return output,{'output':output}
    monkeypatch.setattr('workbench.judgment.Provider.generate',model)
    assert finish(c,cid,ev,task) is None
    assert finish(c,cid,ev,task) is None
    assert finish(c,cid,ev,task)['status']=='failed'
    assert len(c.store.list('judgment_attempt',cid))==2
    assert not current(c,cid)
