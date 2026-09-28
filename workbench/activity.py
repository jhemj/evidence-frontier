"""Read-only projection of actual executions, not scheduler/polling chatter."""
import json

TASKS={'integrity':'증거 무결성 확인','inventory':'저장 구조 확인','normalize':'원문 기록 정리',
       'timeline':'시간 기록 추출','crosscheck':'파일시스템 대조','linux_scan':'Linux 단서 수집',
       'windows_scan':'Windows 단서 수집','investigation_report':'보고서 저장'}
TOOLS={'search':'기록 검색','read_file':'원문 읽기','read_source':'보존 원문 읽기',
       'static_file':'파일 정적 분석','archive_list':'압축파일 목록 확인','correlate':'기록 연결 분석'}
ROLES={'plan':'다음 조사 계획','falsifier':'주장의 반대 근거 검토','judgment':'단서별 증거 판단','synthesis':'사건 가설 종합'}


def records(store,kind,cid):
    # Never load/re-send evidence packs, model prose or large worker results for
    # this display. The authoritative records remain unmodified in the ledger.
    with store.lock:
        rows=store.db.execute("""SELECT json_remove(body,'$.pack','$.output','$.raw_output',
            '$.rejected_output','$.result','$.result_scope','$.observation_ids')
            FROM records WHERE kind=? AND case_id=? ORDER BY created_at,id""",(kind,cid)).fetchall()
    return [json.loads(r[0]) for r in rows]


def project(store,cid):
    case=store.get(cid,'case')
    tasks={t['id']:t for t in records(store,'task',cid)}
    evidence={e['id']:e for e in records(store,'evidence',cid)}
    receipts=records(store,'receipt',cid)
    by_reservation={r['reservation_id']:r for r in receipts if r.get('reservation_id')}
    by_input={r['input_record_id']:r for r in receipts if r.get('input_record_id')}
    items=[]

    def add(row,kind,title,target,status,started=None,ended=None,error=None):
        task=tasks.get(row.get('task_id',row['id']),{})
        eid=row.get('evidence_id') or task.get('evidence_id')
        old=bool(task.get('superseded') or evidence.get(eid,{}).get('connected') is False or
                 row.get('generation',task.get('retry_generation',0))!=task.get('retry_generation',0))
        # A reservation records transmission intent, not continuous liveness.
        # If its owner has stopped, don't leave it glowing as an active task.
        if status in ('running','waiting') and (old or case['status'] not in ('running','pause_requested') or task.get('status') not in (None,'running','queued')):
            status='paused' if case['status']=='paused' and not old else 'interrupted'
        items.append({'id':row['id'],'task_id':task.get('id'),'kind':kind,'title':title,'target':str(target or '')[:500],
            'status':status,'started_at':started,'ended_at':ended,
            'at':started or ended or row['created_at'],'error':str(error or '')[:1600],
            'archived_scope':old})

    def outcome(receipt):
        return 'failed' if receipt and ('error' in receipt or str(receipt.get('receipt_type','')).endswith('_error')) else 'done'

    for t in tasks.values():
        if t.get('action') not in TASKS or not t.get('started_at'):continue
        state=t['status']
        status={'covered':'done','covered_zero':'done','partial':'partial','unsupported':'partial',
                'failed':'failed','blocked':'failed','queued':'waiting','running':'running'}.get(state,'interrupted')
        add(t,'report' if t['action']=='investigation_report' else 'collection',TASKS[t['action']],
            evidence.get(t.get('evidence_id'),{}).get('name'),status,t['started_at'],t.get('ended_at'),t.get('error'))
    for j in records(store,'investigation_job',cid):
        if j['status']=='admitted':continue # Only proposed, never dispatched.
        terminal=j['status'] in ('received','ingested')
        result=j.get('result_status')
        state=('failed' if result=='failed' else 'partial' if result in ('partial','unsupported','blocked') else 'done') if terminal else ('running' if j.get('worker_status')=='running' else 'waiting')
        req=j.get('request',{})
        target=' · '.join(str(req[k]) for k in ('path','query') if req.get(k))
        add(j,'tool',TOOLS.get(req.get('tool'),'도구 실행'),target,state,
            j.get('dispatched_at'),j.get('ended_at'),j.get('error'))
    for r in records(store,'model_reservation',cid):
        receipt=by_reservation.get(r['id'])
        status=outcome(receipt) if receipt else {'failed':'failed','received':'done','reserved':'running'}.get(r['status'],'interrupted')
        add(r,'model',ROLES.get(r.get('purpose'),'AI 검토'),r.get('target',''),status,
            r['created_at'],receipt.get('created_at') if receipt else r.get('ended_at'),r.get('error') or (receipt or {}).get('error'))
    for kind,role in (('review_input','judgment'),('synthesis_input','synthesis')):
        for r in records(store,kind,cid):
            receipt=by_input.get(r['id'])
            add(r,'model',ROLES[role],r.get('activity_target',''),
                outcome(receipt) if receipt else 'running',r['created_at'],
                receipt.get('created_at') if receipt else None,(receipt or {}).get('error'))
    # Legacy falsifier calls have completion receipts but no start reservation.
    # Do not invent start times or elapsed durations for those old records.
    for r in receipts:
        if r.get('reservation_id'):continue
        if r.get('receipt_type')=='automatic_falsifier' or r.get('receipt_type')=='model_error' and r.get('claim_id'):
            add(r,'model',ROLES['falsifier'],r.get('claim_id'),outcome(r),ended=r['created_at'],error=r.get('error'))
    items.sort(key=lambda r:(r['at'],r['id']),reverse=True)
    latest={}
    for item in items:
        if item['kind']!='model':continue
        if item['status']=='running' and item['task_id'] in latest:
            item['status']='interrupted'
        latest[item['task_id']]=item['id']
    return items


def page(store,cid,status='',kind='',offset=0,limit=50):
    items=project(store,cid)
    filtered=[r for r in items if (not status or r['status']==status) and (not kind or r['kind']==kind)]
    return {'items':filtered[offset:offset+limit],'total':len(filtered),'all_total':len(items),
            'offset':offset,'has_more':offset+limit<len(filtered)}
