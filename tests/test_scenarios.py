from copy import deepcopy
import pytest
from test_hypothesis_ledger import context
from workbench.scenarios import project, accepted


def assessment(fit='moderate', question='접속은 승인된 작업인가?', priority='normal'):
    return {'comparison_question':question,'evidence_fit':fit,'ranking_reason':'접속 원문과 승인 기록의 일치 여부를 비교합니다.',
        'alternative_explanation':'승인된 유지보수 작업일 수 있습니다.','next_check':'작업 승인 기록과 접속 주체를 대조합니다.',
        'investigation_priority':priority,'priority_reason':'승인 기록의 비교로 불확실성을 빠르게 줄일 수 있습니다.'}


def snapshot(s,c):
    return {k:s.list(k,c['id']) for k in ('evidence','task','observation','hypothesis')}


def test_comparison_scoped_ties_no_count_or_priority_ranking_and_unlimited(context):
    s,c,e,t,obs,submit,a=context
    for i in range(13):
        submit({**a,'title':f'검증 질문 {i}', 'scenario_assessment':assessment(priority='high' if i==0 else 'normal'),
                'supporting_evidence_ids':[o['id'] for o in obs] if i==0 else [obs[0]['id']]},plan=f'p{i}')
    rows=project(snapshot(s,c))['cards']
    assert len(rows)==13 and all(r['rank']==1 and r['tied'] for r in rows)
    h=submit({**a,'title':'지속성 확보의 별도 질문','scenario_assessment':assessment('limited','지속성이 확보되었는가?')},plan='separate')
    separate=next(r for r in project(snapshot(s,c))['cards'] if r['id']==h['id'])
    assert separate['rank']==1 and not separate['tied']


def test_rank_change_is_durable_and_replay_is_noop(context):
    s,c,e,t,obs,submit,a=context
    h=submit({**a,'scenario_assessment':assessment('moderate')})
    peer=submit({**a,'title':'경쟁 설명','scenario_assessment':assessment('limited')},plan='p2')
    update={**a,'action':'update','hypothesis_card_id':peer['hypothesis_card_id'],
            'scenario_assessment':assessment('strong'),'change_reason':'새 원문에서 승인 주체가 일치합니다.'}
    promoted=submit(update,plan='p3')
    assert promoted['scenario_rank_history'][-1]['previous_rank']==2
    assert promoted['scenario_rank_history'][-1]['rank']==1
    assert s.get(h['id'])['scenario_rank_history'][-1]['rank']==2
    assert submit(update,plan='p3')==promoted
    weaker=submit({**update,'scenario_assessment':assessment('limited')},plan='p4')
    assert weaker['lifecycle']=='weakened'


def test_held_refuted_missing_assessment_and_stale_source_are_not_ranked(context):
    s,c,e,t,obs,submit,a=context
    h=submit({**a,'scenario_assessment':assessment('strong')})
    doc=snapshot(s,c)
    changed=deepcopy(doc);changed['observation'][0]['fields']['changed']=True
    assert project(changed)['cards'][0]['rank'] is None
    assert project(changed)['cards'][0]['lifecycle']=='inconclusive'
    doc['task'][0]['retry_generation']=1
    assert project(doc)['cards']==[]
    doc=snapshot(s,c);doc['evidence'][0]['connected']=False
    assert project(doc)['cards']==[]
    submit({**a,'action':'hold','hypothesis_card_id':h['hypothesis_card_id'],'scenario_assessment':assessment()},plan='held')
    assert project(snapshot(s,c))['cards'][0]['rank'] is None
    submit({**a,'action':'refute','hypothesis_card_id':h['hypothesis_card_id'],
        'supporting_evidence_ids':[],'refuting_evidence_ids':[obs[1]['id']],
        'scenario_assessment':assessment('limited')},plan='refuted')
    card=project(snapshot(s,c))['cards'][0]
    assert card['rank'] is None and card['lifecycle']=='refuted'
    submit({**a,'title':'아직 비교하지 않은 가설'},plan='legacy')
    assert all(r['rank'] is None for r in project(snapshot(s,c))['cards'])


def test_related_new_source_invalidates_fit_not_unrelated_source(context):
    s,c,e,t,obs,submit,a=context
    obs[0]=s.add('observation',c['id'],evidence_id=e['id'],type='linux_command',fields={'path':'/log','partition_offset':0})
    submit({**a,'supporting_evidence_ids':[obs[0]['id']],'scenario_assessment':assessment()})
    s.add('observation',c['id'],evidence_id=e['id'],fields={'path':'/noise'})
    assert project(snapshot(s,c))['cards'][0]['rank']==1
    s.add('observation',c['id'],evidence_id=e['id'],fields={'path':'/log','partition_offset':0})
    assert project(snapshot(s,c))['cards'][0]['rank'] is None


def test_rank_requires_alternative_check_and_positive_sources():
    for bad in ({**assessment(),'next_check':''},{**assessment(),'alternative_explanation':''},
                {**assessment(),'probability':99}):
        with pytest.raises(ValueError):accepted(bad,['o'],[])
    with pytest.raises(ValueError):accepted(assessment('strong'),[],['counter'])
    with pytest.raises(ValueError):accepted(assessment('strong'),['o'],[],'absence')


def test_scheduler_priority_is_separate_from_fit_with_fairness(context):
    from workbench.question_engine import refresh
    s,c,e,t,obs,submit,a=context
    strong=submit({**a,'scenario_assessment':assessment('strong',priority='low')})
    weak=submit({**a,'title':'영향은 크지만 지지가 약한 설명','scenario_assessment':assessment('limited',priority='high')},plan='p2')
    class Controller:
        store=s
        def active_observations(self,cid):return s.list('observation',cid)
    rows=[q for q in refresh(Controller(),c['id'],e,t)['questions'] if q['source_kind']=='hypothesis']
    assert rows[0]['source_ids']==[weak['id']]
    assert project(snapshot(s,c))['cards'][0]['id']==strong['id']
    s.add('decision_revision',c['id'],question_id=rows[0]['id'])
    rows=[q for q in refresh(Controller(),c['id'],e,t)['questions'] if q['source_kind']=='hypothesis']
    # One completed visit does not force round-robin away from a material
    # high-priority question. Bounded aging still prevents permanent starvation.
    assert rows[0]['source_ids']==[weak['id']]
    for _ in range(2):s.add('decision_revision',c['id'],question_id=rows[0]['id'])
    rows=[q for q in refresh(Controller(),c['id'],e,t)['questions'] if q['source_kind']=='hypothesis']
    assert rows[0]['source_ids']==[strong['id']]
