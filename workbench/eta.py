"""Conservative phase ranges, never a promised whole-case completion time."""
import math
import os
from datetime import datetime,timezone
from .activity import records


def estimate(store,cid,now=None):
    now=now or datetime.now(timezone.utc)
    case=store.get(cid,'case')
    if case['status']!='running':return None
    active={e['id'] for e in records(store,'evidence',cid) if e.get('connected',True)}
    tasks=[t for t in records(store,'task',cid) if not t.get('superseded') and t.get('evidence_id') in active]
    task=next((t for t in tasks if t['status']=='running'),None) or next((t for t in tasks if t['status']=='queued'),None)
    if not task:return None
    timeout=max(30,min(600,int(os.getenv('MODEL_TIMEOUT','300'))))
    receipts=[r for r in records(store,'receipt',cid) if r.get('task_id')==task['id']]
    role_samples={}
    for r in receipts:
        role=r.get('role') or {'investigator_model':'investigator','automatic_falsifier':'falsifier',
            'dossier_model':'judgment','dossier_page_model':'judgment','case_synthesis':'synthesis'}.get(r.get('receipt_type'))
        seconds=r.get('elapsed_seconds')
        if isinstance(seconds,(float,int)) and math.isfinite(seconds) and seconds>0:
            role_samples.setdefault(role,[]).append(seconds)
    sample_count=0
    def unit(role):
        nonlocal sample_count
        samples=role_samples.get(role,[])[-12:]
        sample_count+=len(samples)
        # Slow recent calls dominate; deliberately not optimistic mean throughput.
        return max(samples)*1.25 if samples else timeout
    def active_cost(cost,kind):
        completed={r.get('input_record_id') for r in receipts}
        attempts=[r for r in records(store,kind,cid) if current(r)]
        if attempts and attempts[-1]['id'] not in completed:
            # An old abandoned attempt must not masquerade as the current call.
            elapsed=max(0,(now-datetime.fromisoformat(attempts[-1]['created_at'])).total_seconds())
            return max(cost,elapsed*1.25)
        return cost
    action=task['action'];scope=task.get('label','현재 단계');low=high=timeout
    current=lambda r:r.get('task_id')==task['id'] and r.get('generation',0)==task.get('retry_generation',0)
    if action in ('linux_investigate','windows_investigate'):
        runs=[r for r in records(store,'investigation_run',cid) if r.get('task_id')==task['id']]
        run=runs[-1] if runs else {}
        reserved=[r for r in records(store,'model_reservation',cid) if current(r) and r.get('status')=='reserved' and r.get('purpose')=='plan']
        pending=len(reserved)
        remaining=max(0,run.get('max_model_calls',12)-run.get('model_calls',0))+pending
        required=min(remaining,max(1,math.ceil(max(0,11-run.get('domain_cursor',1))/max(1,run.get('plan_domain_batch_size',3)))))
        cost=unit('investigator')
        if reserved:
            elapsed=max(0,(now-datetime.fromisoformat(reserved[-1]['created_at'])).total_seconds())
            cost=max(cost,elapsed*1.25) # A slow call extends the range; never sticks at zero.
        jobs=[j for j in records(store,'investigation_job',cid) if j.get('task_id')==task['id'] and j.get('status') in ('admitted','submitted','received')]
        tool_left=max(len(jobs),run.get('max_tool_calls',36)-run.get('tool_calls',0))
        review_left=max(0,run.get('max_review_calls',5)-run.get('review_calls',0))
        low=max(1,required)*cost+len(jobs)*30
        high=max(1,remaining)*cost*1.5+tool_left*60+review_left*unit('falsifier')
        scope='AI 추가 조사 단계'
    elif action=='ai_judgment':
        from .dossiers import MAX_ROUNDS
        from .review_contracts import REVIEW_BUDGET
        batches=[b for b in records(store,'dossier_batch',cid) if current(b) and b.get('status') not in ('done','failed','split','input_projection_blocked')]
        if batches:
            cost=active_cost(unit('judgment'),'review_input')
            low=sum(max(1,2-b.get('round',0)) for b in batches)*cost
            high=sum(max(1,MAX_ROUNDS-b.get('round',0)) for b in batches)*cost*1.5
            stream_ids={b.get('review_stream_id') for b in batches if b.get('review_stream_id')}
            page_left=sum(p.get('stream_id') in stream_ids and p.get('status')!='reviewed'
                          for p in records(store,'review_page',cid))
            # Each page is a real call before the already counted synthesis.
            low+=page_left*cost
            high+=page_left*cost*1.5
            jobs=[j for j in records(store,'investigation_job',cid) if j.get('task_id')==task['id']]
            tool_left=max(0,task.get('review_budget',REVIEW_BUDGET)['normal_tool_jobs']-len(jobs))
            high+=min(tool_left,len(batches)*2)*60
            scope='단서 판단·추가 검사 단계'
        else:
            hypotheses=[h for h in records(store,'hypothesis',cid) if h.get('evidence_id')==task.get('evidence_id')]
            done={r.get('hypothesis_id') for r in records(store,'case_synthesis',cid) if current(r)}
            count=max(1,len([h for h in hypotheses if h['id'] not in done]))
            cost=active_cost(unit('synthesis'),'synthesis_input');low=count*cost;high=low*2
            scope='사건 가설 종합 단계' if records(store,'dossier_batch',cid) else '단서 검토 준비 단계'
    elif action=='integrity':
        # Before a throughput measurement, disclose a broad configured-budget
        # range, not a fabricated disk speed or an exact verification ETA.
        budget=max(60,int(os.getenv('EWF_TIMEOUT','7200')))
        low=budget/4;high=budget;scope='증거 무결성 단계'
    else:
        historical=[r for r in tasks if r.get('action')==action and r.get('started_at') and r.get('ended_at')]
        durations=[max(1,(datetime.fromisoformat(r['ended_at'])-datetime.fromisoformat(r['started_at'])).total_seconds()) for r in historical]
        cost=max(durations)*1.5 if durations else timeout
        low=cost/2;high=cost*2;sample_count+=len(durations)
        scope=task.get('label') or ('보고서 저장 단계' if action=='investigation_report' else '자료 수집 단계')
    return {'low_seconds':math.ceil(max(60,low)/60)*60,'high_seconds':math.ceil(max(120,low,high)/60)*60,
            'scope':scope,'basis':'최근 실측 + 재시도·추가 검사 여유' if sample_count else '초기 실행 예산 기반',
            'sample_count':sample_count,'estimated_at':now.isoformat(),
            'assumptions':'현재 단계만의 보수적 추정 범위. 새 단서·추가 검사·재시도·모델 속도에 따라 늘어날 수 있으며 전체 종료 시각이나 상한 보장이 아닙니다.'}
