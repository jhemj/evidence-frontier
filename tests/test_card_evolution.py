from copy import deepcopy
from workbench.card_evolution import assessment_revision
from workbench.triage import project


def test_accepted_card_changes_keep_stable_identity_and_source_delta():
    original={'title':'실행 가능성 검토','card_summary':'명령 문자열이 있습니다. 실행 여부는 확인 중입니다.',
              'observation_ids':['o'],'reason':'원문 검토','judgment':'미확인'}
    d={'id':'d','task_id':'t','generation':0,'status':'pending','observation_ids':['o']}
    history=assessment_revision(d,original,'r1');d['assessment_history']=history
    before=deepcopy(history)
    revised={**original,'title':'설명서의 명령 예시로 확인','card_summary':'문서의 실행 예시입니다. 호스트 실행 기록은 아닙니다.',
             'timeline_role':'반증됨','counterevidence_ids':['o'],'change_reason':'같은 원문의 문서 문맥을 반영했습니다.'}
    d['assessment_history']=assessment_revision(d,revised,'r2',published=True)
    d.update(finding=revised,status='reviewed')
    assert d['assessment_history'][0]==before[0]
    assert d['assessment_history'][-1]['change_type']=='reinterpretation'
    assert d['assessment_history'][-1]['added_observation_ids']==[]
    snapshot={'case':{'id':'c'},'task':[{'id':'t'}],'observation':[{'id':'o','type':'file_metadata','fields':{}}],'dossier':[d]}
    card=project(snapshot)['cards'][0]
    assert card['id']=='d' and card['title']==revised['title'] and card['lifecycle']=='refuted'
    assert card['revision']==2 and card['event_time']['time_kind']=='unknown'
    assert assessment_revision(d,revised,'r2',published=True)==d['assessment_history']


def test_new_hypothesis_revision_updates_same_timeline_card_without_new_observation():
    s={'case':{'id':'c'},'evidence':[{'id':'e'}],'task':[{'id':'t','evidence_id':'e'}],
       'observation':[{'id':'o','evidence_id':'e','type':'linux_command','timestamp':'2026-01-01T01:00:00+00:00','fields':{}}],
       'hypothesis':[{'id':'h','hypothesis_kind':'dynamic','hypothesis_card_id':'stable','task_id':'t','evidence_id':'e',
            'title':'정황 검토','status':'reviewed_with_gaps','observation_ids':['o'],'lifecycle':'investigating','revision':1}]}
    old=project(s)['cards'][0]
    s['hypothesis'][0].update(title='같은 기록의 해석이 강화됨',revision=2,lifecycle='strengthened',
        revision_history=[{'revision':1},{'revision':2,'change_type':'reinterpretation','change_reason':'원문 재대조'}])
    new=project(s)['cards'][0]
    assert old['id']==new['id']=='stable'
    assert old['title']!=new['title'] and new['lifecycle']=='strengthened'
    assert len(s['observation'])==1 and new['change_type']=='reinterpretation'
    assert new['event_time']==old['event_time']
    s['evidence'][0]['connected']=False
    assert project(s)['cards']==[]
