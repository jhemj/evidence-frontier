from .retrieval import tool_scope, fingerprint_scope
from .review_contracts import REVIEW_BUDGET, contract, contracts, attach, model_exhausted, model_attempts, review_exhausted, tools_exhausted
"""Persistent per-lead and baseline review; final prose cannot discard a lead."""
import hashlib
from copy import deepcopy
import json
import re
import copy
import threading
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
import httpx
from .store import now
from .investigation import compact_observation, store_tool_result
from .provider import Provider
from .investigator import consult
from .evidence_access import worker_request, ExecutionUnknown
from .review_validation import CONTRACT_VERSION, errors as validation_errors

BASELINES={
    '인증·SSH·권한':{'linux_authentication','linux_session','linux_ssh_trust','linux_account','linux_login_record'},
    '예약작업·시스템 지속성':{'linux_persistence','linux_cron_call','linux_persistence_link'},
    '실행·통신·시스템 변경':{'linux_command','linux_audit_group','linux_network','linux_process','linux_system_event'},
}
WINDOWS_BASELINES={
    'Windows 인증·계정·시스템':{'windows_event'},
    'Windows 예약작업·방어 설정':{'windows_task','windows_registry'},
    'Windows 실행·통신':{'windows_process','windows_powershell_start','windows_network','windows_scriptblock','windows_srum_application','windows_srum_network'},
    'Windows 파일·기존 해석 재검증':{'windows_file','windows_prior_interpretation','windows_prefetch','windows_amcache','windows_shimcache','windows_counterevidence','windows_correlation'},
}
MAX_ROUNDS=3


def lead_priority(key):
    rule,_,path=key
    if rule=='package_digest_mismatch':
        if path.startswith(('/bin/','/sbin/','/usr/bin/','/usr/sbin/')) or re.search(r'\.so(?:\.[\w.]+)?$',path):return 0
        if path.startswith(('/etc/ssh/','/etc/pam.d/','/etc/cron','/etc/sudoers','/etc/rc','/etc/systemd/','/etc/ld.so','/etc/profile','/etc/security/')) or path in ('/etc/passwd','/etc/group'):return 1
        return 6  # A local package baseline often differs after normal configuration.
    return {'extra_uid_zero':1,'preload_config':1,'writable_persistence':1,
            'prior_report_alert':2,'ai_candidate':2,'ELF_socket_filter_and_process_disguise':3,'ELF_mining_protocol_traits':3,
            'reverse_shell':4,'download_execute':4,'webshell_code':4,'ELF_directory_hooking_traits':9}.get(rule,6)


def belongs(row,task):
    return row['task_id']==task['id'] and row.get('generation',0)==task.get('retry_generation',0)

def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def is_repair(task):return task.get('repair_generation')==task.get('retry_generation',0)

def relevant_jobs(store,batch):
    """Scope inherited results to their logical owners, preserving shared jobs.

    Legacy jobs with unknown ownership stay visible conservatively. This is a
    reader projection, never removal of the underlying result or check ledger.
    """
    wanted=set(batch['dossier_ids']);result=[]
    for jid in batch.get('job_ids',[]):
        job=store.get(jid)
        owners=({c['dossier_id'] for c in contracts(job)}|set(job.get('dossier_ids',[])))-{None,''}
        if not owners or owners&wanted:result.append(job)
    return result


def dependency_fingerprint(store,batch):
    """Bind model adoption to actual source memberships and logical contracts."""
    return digest({'dossiers':[(did,store.get(did)['observation_ids']) for did in batch['dossier_ids']],
        'jobs':[{k:job.get(k) for k in ('id','status','observation_ids','contracts','request','result_scope')}
                for job in relevant_jobs(store,batch)]})


def split_assessment(output,did):
    if not output:return None
    return {'summary':'분할된 단서의 이전 판단입니다. 다른 단서의 판단은 부모 배치 원장에 보존됩니다.',
        'findings':[f for f in output.get('findings',[]) if f.get('dossier_id')==did],
        'check_assessments':[a for a in output.get('check_assessments',[]) if a.get('dossier_id')==did],
        'next_checks':[x for x in output.get('next_checks',[]) if x.get('hypothesis_id')==did]}


def split_context(store,batch,did):
    child={**batch,'dossier_ids':[did]}
    return {'dossier_ids':[did], 'round':batch['round'],
        'output':split_assessment(batch.get('output'),did),
        'job_ids':[j['id'] for j in relevant_jobs(store,child)],
        'deferred_checks':[x for x in batch.get('deferred_checks',[]) if x.get('request',{}).get('hypothesis_id',did)==did]}


def input_projection_failure(store, cid, task, batch, error):
    """Pre-model scheduling failure is never a spent/failed model attempt.

    Repartition independent dossiers exactly once. An indivisible source scope
    is an explicit input gap, not a fabricated model judgment. Raw observations,
    prior assessments and pending logical contracts remain in the ledger.
    """
    with store.tx():
        current = store.get(batch['id'])
        if current['status'] != 'pending':
            return
        diagnostic = store.add('review_diagnostic',cid,task_id=task['id'],batch_id=batch['id'],
            failure_category='input_projection',error=str(error),rejected_output=None,
            model_called=False,model_attempts_unchanged=current['attempts'])
        if len(batch['dossier_ids']) > 1:
            store.update(batch['id'],status='split',termination='input_projection_split',
                         input_diagnostic_id=diagnostic['id'])
            for did in batch['dossier_ids']:
                store.add('dossier_batch',cid,task_id=task['id'],evidence_id=batch['evidence_id'],
                    generation=task.get('retry_generation',0),parent_batch_id=batch['id'],
                    status='pending',attempts=0,**split_context(store,batch,did),
                    validation_feedback=None)
        else:
            store.update(batch['id'],status='input_projection_blocked',
                termination='irreducible_input_scope',input_diagnostic_id=diagnostic['id'])
            for did in batch['dossier_ids']:
                store.update(did,status='input_projection_blocked',
                    error='모델 호출 전 입력 구성 한계. 원문·검사·이전 판단은 원장에 보존되며 검토 완료가 아닙니다.',
                    input_diagnostic_id=diagnostic['id'])


def seed(controller,cid,evidence,task,questions=()):
    store=controller.store
    if any(belongs(r,task) for r in store.list('dossier',cid)):return
    obs=[o for o in controller.active_observations(cid) if o['evidence_id']==evidence['id']]
    groups=defaultdict(list)
    for o in obs:
        if o['type']=='linux_detection':
            f=o['fields']
            groups[(f['rule_id'],f.get('partition_offset'),f['path'])].append(o)
        elif o['type']=='linux_inspection_result' and re.search(r'\[(?:DETECT|REVIEW|WARN|FAIL|탐지)\]',o['fields'].get('excerpt',''),re.I):
            groups[('prior_report_alert',o['fields'].get('partition_offset'),o['fields']['path'])].append(o)
    packets=[]
    for key,items in sorted(groups.items(),key=lambda pair:(lead_priority(pair[0]),str(pair[0]))):
        packets.append((items[0]['fields'].get('title','이전 점검 경보의 원문·실제 상태 재검증'),items,False,key))
    # Tool-assisted AI discoveries are hypotheses to review, even when no static
    # rule named them. Their original source records remain the only evidence.
    by_id={o['id']:o for o in obs}
    valid_ids=set(by_id)
    active_tasks={t['id'] for t in store.list('task',cid) if not t.get('superseded')}
    for claim in store.list('claim',cid):
        refs=claim.get('observation_ids',[])
        if not claim.get('automatic') or claim.get('task_id') not in active_tasks or not refs or not set(refs).issubset(valid_ids):continue
        packets.append((claim['text'][:200],[by_id[oid] for oid in refs],False,('ai_candidate',None,claim['id'])))
    baselines=WINDOWS_BASELINES if store.get(cid).get('target_os')=='windows' else BASELINES
    for title,types in baselines.items():
        items=[o for o in obs if o['type'] in types]
        if title=='인증·SSH·권한':
            settings=[o for o in obs if o['type']=='linux_configuration' and o['fields'].get('path','').startswith(('/etc/ssh/','/etc/sudoers'))]
            important=[o for o in settings if re.search(r'PermitRootLogin|PasswordAuthentication|AllowUsers|AllowGroups|NOPASSWD',o['fields'].get('excerpt',''),re.I)]
            items=important+items+settings
        packets.append((title,list({o['id']:o for o in items}.values()),True,('baseline',None,title)))
    samples=[o for o in obs if o['type']=='linux_baseline_sample']
    if samples:packets.append(('미분류 자료 원문 표본',samples,True,('baseline',None,'unclassified')))
    from .review_partition import partition, BASELINE_FAMILIES, PARTITION_VERSION
    expanded=[]
    for title,items,baseline,key in packets:
        for label,chunk,child_key in partition(title,items,key):
            expanded.append((label,chunk,baseline,child_key,title,key))
    packets=expanded
    # No lead disappears: oversized review sets are recorded as deferred.
    dossiers=[]
    from .review_queue import family,schedule
    history=[d for d in store.list('dossier',cid) if d['evidence_id']==evidence['id'] and d['task_id']==task['id']
             and d.get('generation',0)<task.get('retry_generation',0)]
    prior={d.get('group_key'):d for d in history if d['status']=='deferred'}
    previously_reviewed={d.get('group_key') for d in history if d['status']=='reviewed' and d.get('finding')
                         and set(d['finding']['observation_ids']).issubset(valid_ids)}
    with store.tx():
        for index,(title,items,baseline,key,parent_title,parent_key) in enumerate(packets):
            ids=list(dict.fromkeys(o['id'] for o in items))
            related=set()
            for o in items:
                for ref in o['fields'].get('referenced_paths',[]):
                    path=ref.get('absolute') if isinstance(ref,dict) else ref
                    if path:related.add(path)
            for o in obs:
                if len(ids)>=12:break
                if o['fields'].get('path') in related and o['id'] not in ids:ids.append(o['id'])
            group_key=digest(key);previous=prior.get(group_key);priority=lead_priority(key)
            row=store.add('dossier',cid,task_id=task['id'],evidence_id=evidence['id'],title=title,
                baseline=baseline,generation=task.get('retry_generation',0),observation_ids=ids,total_records=len(items),
                all_observation_ids=[o['id'] for o in items],group_key=group_key,review_priority=priority,
                parent_group_key=digest(parent_key),partition_version=PARTITION_VERSION,
                scope_note='계정·기록된 세션·날짜·파티션·대상별 검토 묶음. 동일 행위자 또는 인과관계의 증명이 아님.',
                source_claim_id=key[2] if key[0]=='ai_candidate' else None,
                review_family=BASELINE_FAMILIES.get(parent_title,'other') if baseline else family(key[0],parent_key[2],priority),
                deferred_since=(previous.get('deferred_since') or previous['created_at']) if previous else None,
                previously_reviewed=group_key in previously_reviewed,
                content_sha256=items[0]['fields'].get('source_sha256') if items and key[0].startswith('ELF_') else None,
                status='unavailable' if baseline and not items else 'pending',finding=None)
            dossiers.append(row)
        # Every unit is admitted or explicitly deferred; no source silently drops.
        if is_repair(task):
            targets=set(task['repair_group_keys'])
            ordered=[d for d in dossiers if d['group_key'] in targets]
            if {d['group_key'] for d in ordered}!=targets:raise ValueError('복구 대상의 원문 범위가 변경되었습니다.')
        else:ordered=schedule([d for d in dossiers if d['status']!='unavailable'],limit=256, general_limit=96,questions=questions)
        admitted={d['id'] for d in ordered}
        for d in dossiers:
            if d['id'] not in admitted and d['status']!='unavailable':store.update(d['id'],status='deferred',
                error='Outside this limited failure recovery; previous reviews are historical, not revalidated' if is_repair(task) else 'AI source-family review budget; source observations retained',
                deferred_reason='outside_repair_scope' if is_repair(task) else 'review_budget',
                disposition='untriaged_deferred',
                reopen_on={'new_question_link':True,'new_object_or_source_version':True,'explicit_scope_extension':True})
        # Reuse only source-only closed reviews; tool-dependent judgments must
        # rebind and assess their contracts in the new generation.
        from .semantic_contract import review_key
        by_observation={o['id']:o for o in obs}
        runtime=controller.runtime_binding()
        reused=set()
        if not is_repair(task):
            for d in ordered:
                key=review_key([by_observation[i] for i in d['observation_ids']],evidence,runtime)
                old=next((x for x in reversed(history) if x.get('reusable_review_key')==key and x['status']=='reviewed' and x.get('finding')),None)
                if not old:continue
                finding={**old['finding'],'dossier_id':d['id']}
                if validation_errors({'findings':[finding]},[d['id']],d['observation_ids'],{d['id']:d['observation_ids']},by_observation):continue
                receipt=store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='review_reuse',
                    generation=task.get('retry_generation',0),source_dossier_id=old['id'],source_receipt_id=old.get('receipt_id'),
                    review_key=key,included_ids=d['observation_ids'],basis='Unchanged exact sources, membership, runtime and review contract; not a new independent review')
                store.update(d['id'],status='reviewed',finding=finding,receipt_id=receipt['id'],reusable_review_key=key,reused_from=old['id'])
                reused.add(d['id'])
        ordered=[d for d in ordered if d['id'] not in reused]
        for offset in range(0,len(ordered),3):
            batch=ordered[offset:offset+3]
            store.add('dossier_batch',cid,task_id=task['id'],evidence_id=evidence['id'],dossier_ids=[d['id'] for d in batch],
                generation=task.get('retry_generation',0),status='pending',round=0,attempts=0,output=None,job_ids=[],deferred_checks=[])


def finish(controller,cid,evidence,task):
    # Coordinate with interactive model calls and case-level mutations while
    # allowing the independent batch workers below to run concurrently.
    if not controller.model_lock.acquire(blocking=False):
        return None
    try:
        before={d['id']:digest(d.get('finding')) for d in controller.store.list('dossier',cid)
            if belongs(d,task) and d.get('status')=='reviewed'}
        result=_dispatch_finish(controller,cid,evidence,task)
        _incremental_checkpoint(controller,cid,evidence,task,before,result)
        return result
    finally:
        controller.model_lock.release()


def _incremental_checkpoint(controller,cid,evidence,task,before,result):
    """After a joined review wave, advance one relevant dynamic checkpoint."""
    store=controller.store
    if result is not None or task.get('review_policy')!='autonomous-v1':return
    if store.get(cid)['status'] in ('paused','pause_requested'):return
    current_task=store.get(task['id'])
    if current_task.get('superseded') or current_task.get('retry_generation',0)!=task.get('retry_generation',0):return
    dossiers=[d for d in store.list('dossier',cid) if belongs(d,task)]
    if not any(d.get('status')=='reviewed' and before.get(d['id'])!=digest(d.get('finding')) for d in dossiers):return
    # Parent holds the UI/coordinator lock; all batch threads have joined.
    worker=copy.copy(controller);worker.model_lock=threading.Lock()
    from .case_synthesis import tick
    tick(worker,cid,evidence,current_task,dossiers,dynamic_only=True,incremental=True)


def _dispatch_finish(controller,cid,evidence,task):
    """Advance up to two independent pending batches in parallel.

    Workers get separate controller lock objects but share the same Store,
    whose RLock/BEGIN IMMEDIATE transactions serialize reservations and
    adoption.  The normal local endpoint remains serial through
    ``model_concurrency.lease``; test transports explicitly opt into two.
    """
    case=controller.store.get(cid)
    if case['status'] in ('paused','pause_requested'):
        return None
    from .runtime_contract import guard
    guard(controller,cid,task)
    # Mutating queue preparation belongs to the coordinator, not model threads.
    with controller.store.lock:
        prepared=_prepare_queue(controller,cid,evidence,task)
    task,memory,all_batches,queue=prepared
    from .models import ProviderConfig
    provider=(controller.store.list('config') or [{}])[-1].get('provider',{})
    concurrency=ProviderConfig.model_validate(provider).review_concurrency
    pending=[];owned=set()
    for batch in queue:
        if batch['status']=='pending' and not owned.intersection(batch['dossier_ids']):
            pending.append(batch);owned.update(batch['dossier_ids'])
    def single(batch_id=None):
        worker=copy.copy(controller)
        worker.model_lock=threading.Lock()
        return _finish_one(worker,cid,evidence,task,batch_id=batch_id,prepared=prepared)
    # Follow-up tool work has priority over fresh model batches; preserving
    # this ordering avoids starving a revisit behind the baseline queue.
    if concurrency < 2 or any(b['status']=='await_checks' for b in all_batches) or len(pending)<2:
        return single()
    pending=pending[:2]
    from .model_concurrency import try_reserve_batch
    def advance(batch):
        # Do not share model_lock: it is a case/UI coordination lock, not a
        # model request lock. Store transactions and batch attempt guards are
        # the adoption boundary for independent workers.
        token=try_reserve_batch(cid,batch['id'])
        if token is None:
            return None
        try:
            if controller.store.get(cid)['status'] in ('paused','pause_requested'):return None
            return single(batch['id'])
        finally:
            # Release only after this worker actually exits, including errors.
            token.release()
    with ThreadPoolExecutor(max_workers=2,thread_name_prefix='dossier-batch') as pool:
        futures=[pool.submit(advance,batch) for batch in pending]
        results=[future.result() for future in futures]
    return next((r for r in results if r is not None),None)


def _prepare_queue(controller,cid,evidence,task):
    store=controller.store
    if not store.get(task['id']).get('review_budget'):store.update(task['id'],review_budget=REVIEW_BUDGET)
    from . import question_engine
    memory=question_engine.refresh(controller,cid,evidence,task)
    seed(controller,cid,evidence,task,questions=memory['questions'])
    question_engine.reopen_deferred(store,cid,task,memory['questions'])
    task=store.update(task['id'],investigation_scheduler='questions-v1') if task.get('investigation_scheduler')!='questions-v1' else task
    batches=[b for b in store.list('dossier_batch',cid) if belongs(b,task)]
    from .discovery import admit
    terminal=('done','failed','split','input_projection_blocked')
    # Revisit after a completed review instead of waiting behind hundreds of
    # baseline units. At most one discovery tranche is in flight.
    if (any(b['status']=='done' for b in batches)
        and not any(b['status']=='await_checks' or b.get('discovery_key') and b['status'] not in terminal for b in batches)):
        if admit(controller,cid,evidence,task):
            batches=[b for b in store.list('dossier_batch',cid) if belongs(b,task)]
    indexed_dossiers={d['id']:d for d in store.list('dossier',cid) if belongs(d,task)}
    queue=sorted((b for b in batches if b['status'] not in terminal),
        key=lambda b:question_engine.batch_priority(b,memory['questions'],indexed_dossiers))
    return task,memory,batches,queue


def _finish_one(controller,cid,evidence,task,batch_id=None,prepared=None):
    from .runtime_contract import guard
    from . import model_availability, question_engine
    from .discovery import admit
    guard(controller,cid,task)
    store=controller.store
    if model_availability.waiting(store,cid):return None
    existing=[j for j in store.list('judgment',cid) if belongs(j,task)]
    if existing:return existing[-1]['result']
    if prepared is None:
        with store.lock:prepared=_prepare_queue(controller,cid,evidence,task)
    task,memory,batches,queue=prepared
    batch=(next((b for b in queue if b['id']==batch_id),None) if batch_id
           else queue[0] if queue else None)
    if batch_id and batch is None:return None
    if batch is None:
        if admit(controller,cid,evidence,task):return None
        dossiers=[d for d in store.list('dossier',cid) if belongs(d,task)]
        if task.get('review_policy')=='autonomous-v1':
            from .case_synthesis import tick
            if not tick(controller,cid,evidence,task,dossiers):return None
        findings=[d['finding'] for d in dossiers if d.get('finding') and d['status']=='reviewed']
        if not findings and any(d['status']=='model_failed' for d in dossiers):return {'status':'failed','complete':False,'observations':[],'error':'AI 단서 검토 실패. 탐지 사실과 원문은 보존됨.'}
        counts={level:sum(f['judgment']==level and f.get('timeline_role')!='반증됨' for f in findings) for level in ('확인','유력','미확인')}
        pending=sum(d['status']!='reviewed' for d in dossiers)
        critical_pending=sum(d['status']!='reviewed' and d.get('review_family') in ('access','persistence','execution') for d in dossiers)
        limited=any(b.get('deferred_checks') or b['round']>=MAX_ROUNDS-1 for b in batches)
        from .check_ledger import project as check_ledger
        checks=check_ledger([j for j in store.list('investigation_job',cid) if belongs(j,task)],batches)
        deferred_count=checks['not_executed']
        unassessed=checks['unassessed_contracts']
        limited=limited or bool(unassessed)
        termination_reasons=[]
        if any(d['status']=='unavailable' for d in dossiers):termination_reasons.append('source_unavailable')
        if any(d['status']=='model_failed' for d in dossiers):termination_reasons.append('model_failure_or_attempt_limit')
        if any(d['status']=='input_projection_blocked' for d in dossiers):termination_reasons.append('input_projection_incomplete')
        if any(d['status']=='deferred' for d in dossiers):termination_reasons.append('review_scope_limit')
        if deferred_count:termination_reasons.append('duplicate_or_tool_budget')
        if unassessed:termination_reasons.append('check_contracts_unassessed')
        synthesis=[s for s in store.list('case_synthesis',cid) if belongs(s,task)]
        if any(s['status']!='reviewed' for s in synthesis):
            limited=True;termination_reasons.append('synthesis_incomplete')
        if any(b['round']>=MAX_ROUNDS-1 for b in batches):termination_reasons.append('round_limit')
        from .discovery import project as frontier_project, current as discovery_current
        frontier=frontier_project({'observations':[o for o in controller.active_observations(cid) if o['evidence_id']==evidence['id']],
            'evidence':[evidence],'dossiers':dossiers,'check_ledger':checks},discovery_current(store,cid,task))
        if frontier['open_leads'] or frontier['deferred_discovery']:
            limited=True;termination_reasons.append('follow_through_gap')
        result={'status':'partial' if pending or limited else 'covered','complete':not (pending or limited),'observations':[],'tool':'local-ai-dossiers-v2','judgment_counts':counts,
            'termination':'budget_or_round_limit' if limited else 'review_queue_processed','scope':'분할 검토 범위. 전체 침해 행위 부재를 의미하지 않음.',
            'dossiers_total':len(dossiers),'dossiers_reviewed':len(findings),'dossiers_unreviewed':pending,
            'termination_reasons':termination_reasons or ['no_further_checks_requested'],
            'unavailable_baselines':sum(d['status']=='unavailable' for d in dossiers),
            'critical_dossiers_unreviewed':critical_pending,'checks_deferred':deferred_count,
            'checks_unassessed':unassessed,'synthesis_ids':[s['id'] for s in synthesis],
            'open_leads':frontier['open_leads'],'deferred_discovery':frontier['deferred_discovery']}
        if is_repair(task):result['limited_recovery']={k:task[k] for k in ('repair_source_generation','repair_group_keys','repair_source_dossier_ids','repair_budget')}
        with store.tx():
            record=store.add('judgment',cid,task_id=task['id'],evidence_id=evidence['id'],generation=task.get('retry_generation',0),
                findings=findings,result=result,included_ids=list(dict.fromkeys(oid for f in findings for oid in f['observation_ids'])),
                selection_is_partial=True,summary=f"기본 영역과 탐지 단서 {len(dossiers)}개 중 {len(findings)}개를 AI가 검토했습니다. 확인 {counts['확인']} · 유력 {counts['유력']} · 미확인 {counts['미확인']}. 검토 미완료 {pending}개(실행·권한·지속성 {critical_pending}개) · 중복·한도로 추가 실행하지 않은 검사 {deferred_count}개. "+('반복 한도에서 부분 결과를 저장했습니다.' if limited else '검토 대기열 처리를 마쳤습니다. 수집·파서 한계는 별도입니다.'),
                dossier_ids=[d['id'] for d in dossiers])
            store.update(cid,result_revision=record['id'],investigation_result='단서별 AI 판단 완료 · 확인 / 유력 / 미확인',investigation_stage='결과 패키지 생성')
        return result
    if batch['status']=='await_checks':
        for jid in batch['job_ids']:
            job=store.get(jid)
            if job['status']=='ingested':continue
            request=tool_scope(job['request'])
            body={'job_key':job['fingerprint'],'signature':evidence['signature'],'action':'investigation_tool','path':evidence['path'],
                'investigation':{'evidence_path':evidence['path'],'run_id':job['source_run'],'request':request,'target_os':store.get(cid).get('target_os','linux')}}
            try:
                if job['status']=='admitted':
                    if not job.get('dispatched_at'):store.update(jid,dispatched_at=now())
                    worker_request('POST','/jobs',json=body,timeout=20);store.update(jid,status='submitted')
                reply=worker_request('GET','/jobs/'+job['fingerprint'],timeout=20)
            except httpx.TransportError:return None
            if reply['status'] in ('queued','running'):
                if job.get('worker_status')!=reply['status']:store.update(jid,worker_status=reply['status'])
                return None
            if reply['status']=='execution_unknown':raise ExecutionUnknown('추가 검사의 이전 실행 상태가 불명확합니다.')
            result=reply.get('result') or {'status':'failed','complete':False,'observations':[],'error':reply.get('error')}
            if reply.get('result') and digest(result)!=reply['result_sha256']:raise ValueError('추가 검사 결과 해시 불일치')
            with store.tx():
                ids=store_tool_result(controller,cid,evidence,task,result,job['request'])
                store.update(jid,status='ingested',observation_ids=ids,result_status=result['status'],result_scope={k:v for k,v in result.items() if k!='observations'},ended_at=now(),error=result.get('error'))
                question_engine.finish_intents(store,cid,store.get(jid))
            return None
        store.update(batch['id'],status='pending',round=batch['round']+1,attempts=0,validation_feedback=None)
        return None
    lifetime_attempts=model_attempts(store,cid,task)
    if batch['attempts']>=2 and any(e.get('code')=='focus_selection_over_budget'
            for e in (batch.get('validation_feedback') or {}).get('errors',[])):
        input_projection_failure(store,cid,task,batch,
            ValueError('Source refocusing could not produce a bounded comparison after the allowed validated attempts; original pages remain unadopted'))
        return None
    if batch['attempts']>=2 or review_exhausted(store,cid,task):
        # Isolate a failing work unit automatically. Valid neighbouring dossiers
        # should not all fail because one output in a three-dossier batch did.
        if (task.get('review_policy')=='autonomous-v1' and len(batch['dossier_ids'])>1
            and not review_exhausted(store,cid,task,2*len(batch['dossier_ids']))):
            with store.tx():
                store.update(batch['id'],status='split',termination='isolate_invalid_batch')
                for did in batch['dossier_ids']:
                    store.add('dossier_batch',cid,task_id=task['id'],evidence_id=evidence['id'],
                        generation=task.get('retry_generation',0),parent_batch_id=batch['id'],
                        status='pending',attempts=0,**split_context(store,batch,did),
                        validation_feedback=batch.get('validation_feedback'))
            return None
        with store.tx():
            for did in batch['dossier_ids']:store.update(did,status='model_failed',error='bounded model attempts exhausted')
            assessed={(a['check_id'],a.get('dossier_id',''),a.get('contract_id','')) for a in (batch.get('output') or {}).get('check_assessments',[])}
            missing=[{'check_id':jid,'dossier_id':c['dossier_id'],'contract_id':c['contract_id'],
                'outcome':'inconclusive','evaluation_status':'unassessed','reason':'검토 예산 소진으로 이 논리 계약은 미평가','observation_ids':[]}
                for jid in batch['job_ids'] for c in contracts(store.get(jid))
                if c['dossier_id'] in batch['dossier_ids'] and (jid,c['dossier_id'],c['contract_id']) not in assessed]
            store.update(batch['id'],status='failed',unassessed_checks=missing)
        return None
    if not controller.model_lock.acquire(blocking=False):return None
    try:
        dossiers=[store.get(did) for did in batch['dossier_ids']]
        all_obs={o['id']:o for o in controller.active_observations(cid) if o['evidence_id']==evidence['id']}
        from . import objection_ledger
        obligations=objection_ledger.current(store,cid,task,batch['dossier_ids'])
        ids=list(dict.fromkeys(oid for d in dossiers for oid in d['observation_ids']))
        ids += [oid for o in obligations for oid in o['observation_ids']]
        checks=[]
        jobs=relevant_jobs(store,batch)
        for job in jobs:
            # The ledger owns the full result membership. The bounded stream
            # schedules source pages instead of silently sampling every job.
            ids+=job.get('observation_ids',[])
            checks.append({'id':job['id'],'request':tool_scope(job['request']),
                'contracts':[c for c in contracts(job) if c['dossier_id'] in batch['dossier_ids']],
                'status':job.get('result_status'),'scope':job.get('result_scope',{}),'observation_ids':job.get('observation_ids',[])})
        ids=list(dict.fromkeys(oid for oid in ids if oid in all_obs))
        compact=[]
        for oid in ids:
            o=compact_observation(all_obs[oid],preserve_content=True)
            if batch['round']==0:
                omitted=[]
                for key in ('title','severity','judgment','assessment','ai_summary'):
                    if key in o['fields']:omitted.append('/fields/'+key)
                    o['fields'].pop(key,None)
                if omitted:o['projection_omissions']={'prior_interpretation_fields':omitted,
                    'reason':'Blind source review omits prior labels, not raw source excerpt content.'}
            compact.append(o)
        pack={'target_os':store.get(cid).get('target_os','linux'),'observations':compact,
            'case_question':store.get(cid).get('question',''),
            'required_dossiers':[{'id':d['id'],'title':('원문에서 독립적으로 확인할 단서' if d.get('source_claim_id') and batch['round']==0 else d['title']),'baseline':d['baseline'],'records':d['total_records'],
                'origin':'source review',
                'observation_ids':d['observation_ids']} for d in dossiers],
            'executed_checks':deepcopy(checks),'deferred_checks':deepcopy(batch.get('deferred_checks',[])),
            'previous_assessment':deepcopy(batch.get('output')) if batch['round'] else None,
            'open_objections':objection_ledger.view(obligations),
            'review_mode':'blind_source_review' if batch['round']==0 else 'compare_and_falsify',
            'final_pass':batch['round']>=MAX_ROUNDS-1,'selection_is_partial':True}
        from .test_admission import catalog
        pack['tool_capabilities']=catalog(pack['target_os'])
        pack['question_context']=question_engine.view(memory)
        # Compact excerpts, not the membership of a review unit. A record must
        # not count as AI-presented when it disappeared to satisfy prompt limits.
        ids=[o['id'] for o in pack['observations']]
        for d in pack['required_dossiers']:d['observation_ids']=[oid for oid in d['observation_ids'] if oid in ids]
        shared=list(dict.fromkeys(oid for job in jobs for oid in job.get('observation_ids',[]) if oid in ids))
        allowed_by_dossier={d['id']:list(dict.fromkeys(d['observation_ids']+[
            oid for job in jobs for oid in job.get('observation_ids',[])
            if d['id'] in ({c['dossier_id'] for c in contracts(job)}|set(job.get('dossier_ids',[]))) and oid in ids
        ])) for d in pack['required_dossiers']}
        for obligation in obligations:
            allowed_by_dossier[obligation['dossier_id']]=list(dict.fromkeys(
                allowed_by_dossier[obligation['dossier_id']]+[i for i in obligation['observation_ids'] if i in ids]))
        for check in pack['executed_checks']:
            original_ids=check['observation_ids']
            check['omitted_observations']=len([oid for oid in original_ids if oid not in ids])
            check['observation_ids']=[oid for oid in original_ids if oid in ids]

        pack['allowed_observation_ids']=ids
        pack['allowed_observation_ids_by_dossier']=allowed_by_dossier
        pack['shared_check_observation_ids']=shared
        pack['citation_contract']=CONTRACT_VERSION
        from .semantic_contract import source_facts
        pack['literal_fact_candidates']=source_facts([all_obs[i] for i in ids])
        if batch.get('validation_feedback'):pack['validation_feedback']=batch['validation_feedback']
        from .review_context import fit_metadata_only as fit, InputBudgetError
        from . import review_stream
        canonical=deepcopy(pack)
        input_maximum=batch.get('review_input_maximum',36000)
        dependencies=dependency_fingerprint(store,batch)
        stream_meta=None
        stream_id=batch.get('review_stream_id')
        if stream_id and store.get(stream_id)['round']!=batch['round']:stream_id=None
        if stream_id and review_stream.scope_fingerprint(store.get(stream_id)['canonical'])!=review_stream.scope_fingerprint(canonical):
            with store.tx():
                store.update(stream_id,status='superseded',reason='source_or_contract_scope_changed')
                store.add('review_diagnostic',cid,task_id=task['id'],batch_id=batch['id'],
                    failure_category='stale_review_stream',stream_id=stream_id,model_called=False,
                    previous_scope_sha256=review_stream.scope_fingerprint(store.get(stream_id)['canonical']),
                    current_scope_sha256=review_stream.scope_fingerprint(canonical))
                store.update(batch['id'],review_stream_id=None)
            batch=store.get(batch['id']);stream_id=None
        try:
            if stream_id:
                pack,stream_meta=review_stream.prepare(store,cid,stream_id)
            else:
                try:fit(pack,input_maximum)
                except InputBudgetError:
                    if len(batch['dossier_ids'])>1:raise
                    stream=review_stream.start(store,cid,task,batch,canonical,maximum=input_maximum)
                    pack,stream_meta=review_stream.prepare(store,cid,stream['id'])
            if stream_meta:
                if batch.get('validation_feedback'):
                    pack['validation_feedback']=deepcopy(batch['validation_feedback'])
                    fit(pack)
                # Validation sees exact predicates and only the current page's
                # sources. A compressed model view cannot mutate that contract.
                validation_pack=review_stream.resolved(pack)
                ids=[o['id'] for o in validation_pack['observations']]
                allowed_by_dossier=validation_pack['allowed_observation_ids_by_dossier']
                checks=validation_pack['executed_checks']
        except (InputBudgetError,review_stream.ProjectionTooLarge) as ex:
            input_projection_failure(store,cid,task,batch,ex)
            return None
        context={'input_sha256':digest(pack),'included_ids':ids,'contract_version':CONTRACT_VERSION,
                 'generation':task.get('retry_generation',0),'round':batch['round'],'attempt':batch['attempts']+1}
        context['dossier_allowed_ids']=allowed_by_dossier
        context['prompt_version']='forensic-provider-5'
        if stream_meta:context['review_stream']=stream_meta
        from .evidence_spans import manifest
        presentation=manifest(pack)
        presented_observations={o['id']:o for o in review_stream.resolved(pack)['observations']}
        with store.tx():
            guard(controller,cid,task)
            if store.get(cid)['status'] in ('paused','pause_requested'):return None
            provider_config=store.list('config')[-1]['provider']
            reservation=store.get(batch['id'])
            if reservation['status']!='pending' or reservation['round']!=batch['round'] or reservation['attempts']!=batch['attempts']:return None
            if review_exhausted(store,cid,task):return None
            activity_target=f"단서 {len(batch['dossier_ids'])}개 · {batch['round']+1}차 검토"
            if stream_meta:
                progress=pack['review_stream']
                activity_target+=(f" · 자료 {progress['pages_reviewed']+1}/{progress['pages_total']}"
                    if stream_meta['phase']=='source_page' else
                    ' · 원문 근거 재선택' if stream_meta['phase']=='focus_page' else
                    ' · 근거 간 비교' if stream_meta['phase']=='comparison_page' else ' · 분할 근거 종합')
            input_record=store.add('review_input',cid,task_id=task['id'],batch_id=batch['id'],pack=pack,
                evidence_presentation=presentation,activity_target=activity_target,**context)
            context['input_record_id']=input_record['id']
            store.update(batch['id'],attempts=batch['attempts']+1,active_attempt_id=input_record['id'])
        store.update(cid,investigation_stage=f"단서별 정황·반증 검토 {sum(b['status']=='done' for b in batches)+1}/{len(batches)}")
        output=None;receipt={};issues=[]
        try:
            output,receipt=consult(provider_config,
                '기본 점검 영역과 단서 각각을 독립적으로 검토하세요. 같은 자료의 반복을 독립 근거로 세지 마세요. '
                '가장 타당한 설명·반대 근거·확인 가능한 다음 검사를 작성하세요. 제공된 각 dossier_id당 하나의 판단이 필요합니다.',pack,role='judgment',provider_factory=Provider)
            model_availability.recovered(store,cid)
            issues=validation_errors(output,batch['dossier_ids'],ids,allowed_by_dossier,presented_observations,
                require_literals=task.get('review_policy')=='autonomous-v1',canonical_observations=all_obs)
            from .review_validation import check_errors
            issues += check_errors(output, checks, allowed_by_dossier)
            issues += objection_ledger.errors(output,pack.get('open_objections',[]),allowed_by_dossier,
                intermediate=bool(stream_meta and stream_meta['phase']!='synthesis'))
            from .review_focus import project
            try:
                selected_pack=project(pack,output)
                if stream_meta and stream_meta['phase']!='synthesis':
                    issues+=review_stream.focus_budget_errors(store,stream_meta,pack,output,selected_pack)
            except ValueError as ex:
                issues.append({'code':'excerpt_selection_invalid','detail':str(ex)})
            if issues:raise ValueError('판단 근거 검증 실패: '+', '.join(sorted({i['code'] for i in issues})))
            if task.get('review_policy')=='autonomous-v1' and not (stream_meta and stream_meta['phase']!='synthesis'):
                from .presentation_claims import bind
                output['findings']=[bind(f,presented_observations) for f in output['findings']]
        except (ValueError,httpx.TransportError) as ex:
            if model_availability.unavailable(ex):
                with store.tx():
                    model_availability.defer(store,cid,task,ex,batch_id=batch['id'],input_record_id=input_record['id'])
                    if store.get(batch['id']).get('active_attempt_id')==input_record['id']:
                        store.update(batch['id'],attempts=batch['attempts'],active_attempt_id=None)
                return None
            from .review_diagnostics import classify, repair_feedback, smaller_input_budget
            category=classify(ex,issues)
            with store.tx():
                diagnostic=store.add('review_diagnostic',cid,task_id=task['id'],batch_id=batch['id'],
                    rejected_output=output,raw_output=getattr(ex,'raw_output',None),failure_category=category,
                    validation_errors=issues,error=str(ex),
                    model_metadata={**getattr(ex,'metadata',{}),
                        **{k:v for k,v in receipt.items() if k!='output'}},**context)
                store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='dossier_model_error',
                    batch_id=batch['id'],error=str(ex),failure_category=category,validation_errors=issues,diagnostic_id=diagnostic['id'],**context)
                from .partial_review import isolate
                if (dependency_fingerprint(store,batch)==dependencies
                    and isolate(store,cid,task,batch,output,issues,presented_observations,context,diagnostic['id'])):
                    return None
                if store.get(batch['id']).get('active_attempt_id')==input_record['id']:
                    store.update(batch['id'],validation_feedback=repair_feedback(ex,issues,diagnostic['id']))
                    if category=='input_context_pressure':
                        smaller=smaller_input_budget(input_maximum,getattr(ex,'metadata',{}))
                        if stream_meta:
                            store.update(stream_meta['stream_id'],status='superseded',reason='measured_model_context_pressure')
                        store.update(batch['id'],review_input_maximum=smaller,review_stream_id=None,
                            attempts=0,active_attempt_id=None,input_budget_diagnostic_id=diagnostic['id'])
            return None
        with store.tx():
            current_task=store.get(task['id']);current_evidence=store.get(evidence['id']);current_batch=store.get(batch['id'])
            guard(controller,cid,task)
            if (current_task.get('retry_generation',0)!=task.get('retry_generation',0) or current_task.get('superseded')
                or current_task.get('status') not in (None,'running','queued')
                or not current_evidence.get('connected',True) or current_evidence['signature']!=evidence['signature']
                or current_batch['round']!=batch['round'] or current_batch['attempts']!=batch['attempts']+1
                or current_batch.get('active_attempt_id')!=input_record['id']
                or dependency_fingerprint(store,current_batch)!=dependencies
                or current_batch['status']!='pending'):
                store.add('review_diagnostic',cid,task_id=task['id'],batch_id=batch['id'],rejected_output=output,
                    rejection='stale_review_scope',**context)
                store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='dossier_model_error',
                    batch_id=batch['id'],error='검토 중 근거 또는 작업 범위 변경',**context)
                return None
            is_page=stream_meta and stream_meta['phase']!='synthesis'
            r=store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],
                receipt_type='dossier_page_model' if is_page else 'dossier_model',batch_id=batch['id'],**context,**receipt)
            discovered=objection_ledger.capture(store,cid,task,output,r['id'],
                manifest(selected_pack) if is_page else presentation)
            if is_page:
                review_stream.accept_page(store,batch,stream_meta,output,r['id'],ids,
                    objection_ids=list(dict.fromkeys(discovered+[o['id'] for o in pack.get('open_objections',[])])),
                    presented_pack=pack)
                return None
            objection_ledger.resolve(store,output,r['id'])
            question_engine.record_check_assessments(store,cid,task,output,r['id'])
            output=objection_ledger.qualify(output,objection_ledger.current(store,cid,task,batch['dossier_ids']))
            if stream_meta:review_stream.complete(store,stream_meta,r['id'])
            from .card_evolution import assessment_revision
            for finding in output['findings']:
                dossier=store.get(finding['dossier_id'])
                store.update(dossier['id'],assessment_history=assessment_revision(dossier,finding,r['id']))
            store.update(batch['id'],output=output,receipt_id=r['id'])
            calls=output.get('next_checks',[])
            jobs=[j for j in store.list('investigation_job',cid) if belongs(j,task)]
            lifetime_jobs=[j for j in store.list('investigation_job',cid) if j.get('task_id')==task['id']]
            source_run=next((o['fields']['run_id'] for o in reversed(list(all_obs.values())) if o['type'] in ('linux_environment','windows_environment')),None)
            admitted=[];deferred=list(batch.get('deferred_checks',[]))
            for call in calls:
                explicit=call.get('question_id')
                linked_question=next((q for q in memory['questions'] if q['id']==explicit),None) if explicit else None
                if explicit and linked_question is None:
                    deferred.append({'request':call,'reason':'question_scope_mismatch'});continue
                linked_question=linked_question or next((q for q in memory['questions'] if set(q.get('observation_ids',[]))&
                    set(store.get(call['hypothesis_id']).get('observation_ids',[]))),None)
                linked_question=linked_question or next((q for q in memory['questions'] if q.get('source_kind')=='case_question'),
                    {'question_key':call['hypothesis_id'],'version':batch['round']})
                intent=question_engine.reserve(store,cid,task,linked_question,call,evidence,source_run)
                if not intent['admission']['eligible']:
                    deferred.append({'request':call,'reason':intent['admission']['reason'],'test_intent_id':intent['id']});continue
                if store.get(cid).get('target_os')=='windows' and call['tool'] not in ('search','read_source','correlate'):
                    deferred.append({'request':call,'reason':'unsupported_windows_tool'});continue
                if pack['final_pass']:
                    deferred.append({'request':call,'reason':'round_limit'});continue
                fingerprint=digest([task['id'],task.get('retry_generation',0),source_run,evidence['signature'],fingerprint_scope(call)])
                from .test_admission import reusable,bind_reuse,execution_fingerprint
                fingerprint=execution_fingerprint(jobs,fingerprint)
                old=reusable(jobs,fingerprint,call,source_run=source_run,evidence_id=evidence['id'])
                if old:
                    old=store.get(old['id'])
                    bind_reuse(store,cid,old,intent)
                    new_contract=contract(call) not in contracts(old)
                    old=store.update(old['id'],dossier_ids=list(dict.fromkeys(old.get('dossier_ids',[old['request'].get('hypothesis_id')])+[call['hypothesis_id']])),contracts=attach(old,call),
                        test_intent_ids=list(dict.fromkeys(old.get('test_intent_ids',[])+[intent['id']])))
                    if old['status']=='ingested':question_engine.finish_intents(store,cid,old)
                    if old['id'] not in batch['job_ids'] or new_contract:admitted.append(old['id'])
                    # Already assessed logical contract is satisfied by its
                    # original job, not an outstanding "deferred" check.
                    continue
                reserve=20 if task.get('review_policy')=='autonomous-v1' and not is_repair(task) else 0
                remaining=REVIEW_BUDGET['total_model_attempts' if is_repair(task) else 'normal_model_attempts']-lifetime_attempts-1-reserve
                queued=sum(b['status']=='pending' and b['id']!=batch['id'] for b in batches)
                family_name=next(d['review_family'] for d in dossiers if d['id']==call['hypothesis_id'])
                family_used=sum(j.get('review_family')==family_name for j in lifetime_jobs)
                # Reserve the next assessment before spending a worker slot,
                # and leave room for other source families' discriminating tests.
                blocked=('assessment_budget' if remaining<2+queued else
                    'family_tool_reserve' if family_used>=24 and any(store.get(did).get('review_family')!=family_name for b in batches if b['status']=='pending' for did in b['dossier_ids']) else
                    'job_budget' if tools_exhausted(len(lifetime_jobs),len(jobs),is_repair(task)) else
                    'source_unavailable' if not source_run else None)
                if blocked:
                    deferred.append({'request':call,'reason':blocked});continue
                job=store.add('investigation_job',cid,task_id=task['id'],evidence_id=evidence['id'],fingerprint=fingerprint,
                    generation=task.get('retry_generation',0),request=call,dossier_ids=[call['hypothesis_id']],contracts=[contract(call)],purpose='dossier_falsification',source_run=source_run,status='admitted',review_family=family_name,test_intent_ids=[intent['id']])
                jobs.append(job);lifetime_jobs.append(job);admitted.append(job['id'])
            if admitted:
                store.update(batch['id'],status='await_checks',job_ids=list(dict.fromkeys(batch['job_ids']+admitted)),deferred_checks=deferred)
            elif any(a.get('evaluation_status')=='unassessed' for a in output.get('check_assessments',[])) and batch['round']<MAX_ROUNDS-1:
                store.update(batch['id'],status='pending',round=batch['round']+1,attempts=0,deferred_checks=deferred,
                    validation_feedback={'errors':[{'code':'missing_check_assessment'}],
                        'instruction':'Assess every executed logical contract. Execution status is not a hypothesis verdict. Do not rerun the same physical job.'})
            elif batch['round']==0 and not is_repair(task):
                store.update(batch['id'],status='pending',round=1,attempts=0,validation_feedback=None,blind_assessment=output)
            else:
                from .judgment import bound_absence
                from .semantic_contract import review_key
                for f in output['findings']:
                    d=store.get(f['dossier_id'])
                    reusable=(not stream_meta and not batch['job_ids'] and not deferred and not f.get('remaining_checks')
                        and not f.get('open_objections') and not d.get('discovery_key'))
                    key=review_key([all_obs[i] for i in d['observation_ids']],evidence,controller.runtime_binding()) if reusable else None
                    accepted=bound_absence(f)
                    from .claim_scope import qualify as qualify_scope
                    accepted=qualify_scope(accepted,all_obs)
                    store.update(f['dossier_id'],status='reviewed',finding=accepted,receipt_id=r['id'],reusable_review_key=key,
                        disposition='reviewed_context' if accepted.get('incident_relevance',{}).get('level')=='context' else 'reviewed_finding',
                        reopen_on={'related_source_version_change':True,'new_question_or_objection_link':True},
                        assessment_history=assessment_revision(store.get(f['dossier_id']),accepted,r['id'],published=True))
                store.update(batch['id'],status='done',deferred_checks=deferred)
        return None
    finally:controller.model_lock.release()
