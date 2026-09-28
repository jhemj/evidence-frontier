import pytest
from workbench.hypothesis_ledger import apply
from workbench.store import Store


@pytest.fixture
def context(tmp_path):
    s=Store(tmp_path/'case.db')
    c=s.add('case','',name='hypothesis test')
    e=s.add('evidence',c['id'],connected=True)
    t=s.add('task',c['id'],evidence_id=e['id'],retry_generation=0)
    obs=[s.add('observation',c['id'],evidence_id=e['id'],type='linux_command',fields={}) for _ in range(2)]
    def submit(a,plan='p',index=0,final=False,gen=0):
        existing=next((p for p in s.list('investigation_plan',c['id']) if p.get('name')==plan),None)
        p=existing or s.add('investigation_plan',c['id'],name=plan,task_id=t['id'],generation=gen)
        return apply(s,c['id'],e['id'],a,[o['id'] for o in obs],task_id=t['id'],generation=gen,
                     source_plan_id=p['id'],proposal_index=index,final=final)
    a={'action':'create','title':'예약 호출과 연결 기록의 관계','card_summary':'예약 호출 기록이 있습니다. 연결 성공 여부를 대조합니다.',
       'judgment':'유력','reasoning':'호출 기록을 근거로 검증','supporting_evidence_ids':[obs[0]['id']],'refuting_evidence_ids':[]}
    return s,c,e,t,obs,submit,a


def test_no_global_ten_cap_and_replay_idempotency(context):
    s,c,e,t,obs,submit,a=context
    rows=[submit(dict(a,title=f'서로 다른 검증 질문 {i}'),plan=f'p{i}') for i in range(13)]
    assert len(s.list('hypothesis',c['id']))==13
    assert rows[-1]['number']==13
    replay=submit(dict(a,title='replayed response'),plan='p0')
    assert replay['id']==rows[0]['id'] and replay['revision']==1
    assert replay['title']!='replayed response'


def test_same_card_new_sources_then_refutation_preserves_history(context):
    s,c,e,t,obs,submit,a=context
    h=submit(a);old=h['revision_history'][0]
    b=dict(a,action='reinforce',hypothesis_card_id=h['hypothesis_card_id'],title='추가 기록으로 호출 정황 강화',
           supporting_evidence_ids=[o['id'] for o in obs],change_reason='두 번째 기록에 같은 대상이 있습니다.')
    updated=submit(b,plan='p2')
    assert updated['id']==h['id'] and updated['lifecycle']=='strengthened'
    assert updated['revision_history'][0]==old
    assert updated['revision_history'][-1]['added_observation_ids']==[obs[1]['id']]
    refuted=submit(dict(b,action='refute',title='연결 성공 가설은 반대 기록으로 반박됨',
                       supporting_evidence_ids=[],refuting_evidence_ids=[obs[1]['id']]),plan='p3')
    assert refuted['id']==h['id'] and refuted['lifecycle']=='refuted'
    assert refuted['revision_history'][-1]['change_type']=='reinterpretation'
    assert len(s.list('observation',c['id']))==2


def test_absence_and_unknown_identity_and_unsupported_reinforcement_rejected(context):
    s,c,e,t,obs,submit,a=context
    h=submit(a)
    b=dict(a,hypothesis_card_id=h['hypothesis_card_id'],action='refute',supporting_evidence_ids=[],
           refuting_evidence_ids=[obs[1]['id']],basis='absence')
    assert submit(b,plan='p2') is None
    assert submit(dict(a,action='update',hypothesis_card_id='invented'),plan='p3') is None
    assert submit(dict(a,action='update'),plan='p4') is None
    assert submit(dict(b,action='reinforce',basis='positive_evidence'),plan='p5') is None


def test_stale_generation_and_disconnection_cannot_change_card(context):
    s,c,e,t,obs,submit,a=context
    h=submit(a)
    b=dict(a,action='update',hypothesis_card_id=h['hypothesis_card_id'])
    s.update(t['id'],retry_generation=1)
    assert submit(b,plan='old') is None
    assert submit(b,plan='new',gen=1) is None  # old card cannot silently migrate
    s.update(t['id'],retry_generation=0);s.update(e['id'],connected=False)
    assert submit(b,plan='disconnected') is None
    assert s.get(h['id'])['revision']==1


def test_hold_never_confirms_and_nested_transaction_is_safe(context):
    s,c,e,t,obs,submit,a=context
    with s.tx():h=submit(a)
    changed=submit(dict(a,action='hold',judgment='확정',hypothesis_card_id=h['hypothesis_card_id'],
                       supporting_evidence_ids=[],title='결정적 자료 부족으로 판단 보류'),plan='p2',final=True)
    assert changed['lifecycle']=='inconclusive' and changed['judgment']=='미확인'
    assert changed['revision_history'][0]['title']==a['title']
