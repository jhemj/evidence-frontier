from datetime import datetime,timezone,timedelta
from workbench.eta import estimate
from test_activity import setup


def test_cold_start_visible_range_and_paused_no_countdown(tmp_path):
    s,cid,e,t=setup(tmp_path)
    out=estimate(s,cid)
    assert out['high_seconds']>=out['low_seconds']>=60
    assert out['sample_count']==0 and out['basis']=='초기 실행 예산 기반'
    assert '전체 종료' in out['assumptions']
    s.update(cid,status='paused');assert estimate(s,cid) is None
    s.update(cid,status='pause_requested');assert estimate(s,cid) is None


def test_plan_remaining_budget_and_slow_call_extend_range(tmp_path):
    s,cid,e,t=setup(tmp_path)
    run=s.add('investigation_run',cid,task_id=t['id'],domain_cursor=4,model_calls=2,max_model_calls=12,
        plan_domain_batch_size=3,tool_calls=10,max_tool_calls=36,review_calls=3,max_review_calls=5)
    for duration in [100,150]:
        s.add('receipt',cid,task_id=t['id'],receipt_type='investigator_model',role='investigator',elapsed_seconds=duration)
    # Another transport/role must not turn slow planner calls into 2-second ones.
    s.add('receipt',cid,task_id=t['id'],receipt_type='automatic_falsifier',role='falsifier',elapsed_seconds=2)
    before=estimate(s,cid)
    assert before['sample_count']==3 and before['low_seconds']>=3*150
    s.update(run['id'],domain_cursor=10,model_calls=10)
    after=estimate(s,cid)
    assert after['low_seconds']<before['low_seconds'] and after['high_seconds']<before['high_seconds']
    reservation=s.add('model_reservation',cid,task_id=t['id'],purpose='plan',status='reserved')
    s.update(reservation['id'],created_at=(datetime.now(timezone.utc)-timedelta(minutes=20)).isoformat())
    assert estimate(s,cid)['high_seconds']>after['high_seconds']


def test_review_queue_uses_measured_judgment_not_planner_speed(tmp_path):
    s,cid,e,t=setup(tmp_path);s.update(t['id'],action='ai_judgment')
    for i in range(4):s.add('dossier_batch',cid,task_id=t['id'],generation=0,status='pending',round=0)
    s.add('receipt',cid,task_id=t['id'],role='judgment',elapsed_seconds=10)
    s.add('receipt',cid,task_id=t['id'],role='investigator',elapsed_seconds=250)
    out=estimate(s,cid)
    assert out['low_seconds']==120 and out['sample_count']==1
    assert out['scope']=='단서 판단·추가 검사 단계'


def test_slow_review_call_extends_estimate_without_borrowing_old_attempts(tmp_path):
    s,cid,e,t=setup(tmp_path);s.update(t['id'],action='ai_judgment')
    s.add('dossier_batch',cid,task_id=t['id'],generation=0,status='pending',round=1)
    s.add('receipt',cid,task_id=t['id'],role='judgment',elapsed_seconds=10)
    before=estimate(s,cid)
    old=s.add('review_input',cid,task_id=t['id'],generation=0)
    later=datetime.now(timezone.utc)+timedelta(minutes=20)
    assert estimate(s,cid,now=later)['low_seconds']>before['low_seconds']
    newer=s.add('review_input',cid,task_id=t['id'],generation=0)
    s.add('receipt',cid,task_id=t['id'],input_record_id=newer['id'],receipt_type='dossier_model_error')
    assert estimate(s,cid,now=later)['low_seconds']==before['low_seconds']
