from .retrieval import tool_scope, fingerprint_scope
"""Persistent per-lead and baseline review; final prose cannot discard a lead."""
import hashlib
import json
import re
from collections import defaultdict
import httpx
from .investigation import compact_observation, store_tool_result
from .provider import Provider
from .evidence_access import worker_request, ExecutionUnknown
from .review_validation import CONTRACT_VERSION, errors as validation_errors

BASELINES={
    '인증·SSH·권한':{'linux_authentication','linux_session','linux_ssh_trust','linux_account','linux_login_record'},
    '예약작업·시스템 지속성':{'linux_persistence','linux_cron_call','linux_persistence_link'},
    '실행·통신·시스템 변경':{'linux_command','linux_audit_group','linux_network','linux_process','linux_system_event'},
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

def seed(controller,cid,evidence,task):
    store=controller.store
    if any(belongs(r,task) for r in store.list('dossier_batch',cid)):return
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
    for title,types in BASELINES.items():
        items=[o for o in obs if o['type'] in types]
        if title=='인증·SSH·권한':
            settings=[o for o in obs if o['type']=='linux_configuration' and o['fields'].get('path','').startswith(('/etc/ssh/','/etc/sudoers'))]
            important=[o for o in settings if re.search(r'PermitRootLogin|PasswordAuthentication|AllowUsers|AllowGroups|NOPASSWD',o['fields'].get('excerpt',''),re.I)]
            items=important+items+settings
        packets.append((title,list({o['id']:o for o in items}.values()),True,('baseline',None,title)))
    # No lead disappears: oversized review sets are recorded as deferred.
    dossiers=[]
    from .review_queue import family,schedule
    history=[d for d in store.list('dossier',cid) if d['evidence_id']==evidence['id'] and d['task_id']==task['id']
             and d.get('generation',0)<task.get('retry_generation',0)]
    prior={d.get('group_key'):d for d in history if d['status']=='deferred'}
    previously_reviewed={d.get('group_key') for d in history if d['status']=='reviewed' and d.get('finding')
                         and set(d['finding']['observation_ids']).issubset(valid_ids)}
    with store.tx():
        for index,(title,items,baseline,key) in enumerate(packets):
            selected=items[:4]+items[-4:] if len(items)>8 else items
            ids=list(dict.fromkeys(o['id'] for o in selected))
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
                source_claim_id=key[2] if key[0]=='ai_candidate' else None,
                review_family='baseline' if baseline else family(key[0],key[2],priority),
                deferred_since=(previous.get('deferred_since') or previous['created_at']) if previous else None,
                previously_reviewed=group_key in previously_reviewed,
                content_sha256=items[0]['fields'].get('source_sha256') if items and key[0].startswith('ELF_') else None,
                status='pending',finding=None)
            dossiers.append(row)
        # Baselines are always reviewed even when many leads exist.
        if is_repair(task):
            targets=set(task['repair_group_keys'])
            ordered=[d for d in dossiers if d['group_key'] in targets]
            if {d['group_key'] for d in ordered}!=targets:raise ValueError('복구 대상의 원문 범위가 변경되었습니다.')
        else:ordered=schedule(dossiers,limit=256, general_limit=96)
        admitted={d['id'] for d in ordered}
        for d in dossiers:
            if d['id'] not in admitted:store.update(d['id'],status='deferred',
                error='Outside this limited failure recovery; previous reviews are historical, not revalidated' if is_repair(task) else 'AI source-family review budget; source observations retained',
                deferred_reason='outside_repair_scope' if is_repair(task) else 'review_budget')
        for offset in range(0,len(ordered),3):
            batch=ordered[offset:offset+3]
            store.add('dossier_batch',cid,task_id=task['id'],evidence_id=evidence['id'],dossier_ids=[d['id'] for d in batch],
                generation=task.get('retry_generation',0),status='pending',round=0,attempts=0,output=None,job_ids=[],deferred_checks=[])


def finish(controller,cid,evidence,task):
    store=controller.store
    existing=[j for j in store.list('judgment',cid) if belongs(j,task)]
    if existing:return existing[-1]['result']
    seed(controller,cid,evidence,task)
    batches=[b for b in store.list('dossier_batch',cid) if belongs(b,task)]
    batch=next((b for b in batches if b['status'] not in ('done','failed')),None)
    if batch is None:
        dossiers=[d for d in store.list('dossier',cid) if belongs(d,task)]
        findings=[d['finding'] for d in dossiers if d.get('finding')]
        if not findings:return {'status':'failed','complete':False,'observations':[],'error':'AI 단서 검토 실패. 탐지 사실과 원문은 보존됨.'}
        counts={level:sum(f['judgment']==level for f in findings) for level in ('확인','유력','미확인')}
        pending=sum(d['status']!='reviewed' for d in dossiers)
        critical_pending=sum(d['status']!='reviewed' and d.get('review_family') in ('access','persistence','execution') for d in dossiers)
        limited=any(b.get('deferred_checks') or b['round']>=MAX_ROUNDS-1 for b in batches)
        deferred_count=sum(len(b.get('deferred_checks',[])) for b in batches)
        result={'status':'partial' if pending or limited else 'covered','complete':not (pending or limited),'observations':[],'tool':'local-ai-dossiers-v2','judgment_counts':counts,
            'termination':'budget_or_round_limit' if limited else 'review_queue_processed','scope':'분할 검토 범위. 전체 침해 행위 부재를 의미하지 않음.',
            'dossiers_total':len(dossiers),'dossiers_reviewed':len(findings),'dossiers_unreviewed':pending,
            'critical_dossiers_unreviewed':critical_pending,'checks_deferred':deferred_count}
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
                'investigation':{'evidence_path':evidence['path'],'run_id':job['source_run'],'request':request}}
            try:
                if job['status']=='admitted':
                    worker_request('POST','/jobs',json=body,timeout=20);store.update(jid,status='submitted')
                reply=worker_request('GET','/jobs/'+job['fingerprint'],timeout=20)
            except httpx.TransportError:return None
            if reply['status'] in ('queued','running'):return None
            if reply['status']=='execution_unknown':raise ExecutionUnknown('추가 검사의 이전 실행 상태가 불명확합니다.')
            result=reply.get('result') or {'status':'failed','complete':False,'observations':[],'error':reply.get('error')}
            if reply.get('result') and digest(result)!=reply['result_sha256']:raise ValueError('추가 검사 결과 해시 불일치')
            with store.tx():
                ids=store_tool_result(controller,cid,evidence,task,result,job['request'])
                store.update(jid,status='ingested',observation_ids=ids,result_status=result['status'],result_scope={k:v for k,v in result.items() if k!='observations'})
            return None
        store.update(batch['id'],status='pending',round=batch['round']+1,attempts=0,validation_feedback=None)
        return None
    lifetime_attempts=sum(r.get('task_id')==task['id'] for r in store.list('review_input',cid))
    if batch['attempts']>=2 or (not is_repair(task) and lifetime_attempts>=576):
        with store.tx():
            for did in batch['dossier_ids']:store.update(did,status='model_failed',error='bounded model attempts exhausted')
            store.update(batch['id'],status='failed')
        return None
    if not controller.model_lock.acquire(blocking=False):return None
    try:
        dossiers=[store.get(did) for did in batch['dossier_ids']]
        all_obs={o['id']:o for o in controller.active_observations(cid) if o['evidence_id']==evidence['id']}
        ids=list(dict.fromkeys(oid for d in dossiers for oid in d['observation_ids']))
        checks=[]
        for jid in batch['job_ids']:
            job=store.get(jid);ids+=job.get('observation_ids',[])[:12]
            checks.append({'id':job['id'],'request':job['request'],'status':job.get('result_status'),'scope':job.get('result_scope',{}),'observation_ids':job.get('observation_ids',[])})
        ids=list(dict.fromkeys(oid for oid in ids if oid in all_obs))
        compact=[]
        for oid in ids:
            o=compact_observation(all_obs[oid])
            if batch['round']==0:
                for key in ('title','severity','judgment','assessment','ai_summary'):
                    o['fields'].pop(key,None)
            compact.append(o)
        pack={'observations':compact,
            'required_dossiers':[{'id':d['id'],'title':('원문에서 독립적으로 확인할 단서' if d.get('source_claim_id') and batch['round']==0 else d['title']),'baseline':d['baseline'],'records':d['total_records'],
                'origin':'source review',
                'observation_ids':d['observation_ids']} for d in dossiers],
            'executed_checks':checks,'deferred_checks':batch.get('deferred_checks',[]),'previous_assessment':batch.get('output') if batch['round'] else None,
            'review_mode':'blind_source_review' if batch['round']==0 else 'compare_and_falsify',
            'final_pass':batch['round']>=MAX_ROUNDS-1,'selection_is_partial':True}
        # Keep at least two source records per dossier; preferentially retain new checks.
        protected={oid for d in dossiers for oid in d['observation_ids'][:2]}
        protected.update(oid for jid in batch['job_ids'] for oid in store.get(jid).get('observation_ids',[])[:2])
        while len(json.dumps(pack,ensure_ascii=False))>36000:
            removable=next((o for o in pack['observations'] if o['id'] not in protected),None)
            if removable is None:break
            pack['observations'].remove(removable)
        ids=[o['id'] for o in pack['observations']]
        for d in pack['required_dossiers']:d['observation_ids']=[oid for oid in d['observation_ids'] if oid in ids]
        shared=list(dict.fromkeys(oid for jid in batch['job_ids'] for oid in store.get(jid).get('observation_ids',[])[:12] if oid in ids))
        allowed_by_dossier={d['id']:list(dict.fromkeys(d['observation_ids']+[
            oid for jid in batch['job_ids'] for oid in store.get(jid).get('observation_ids',[])
            if d['id'] in store.get(jid).get('dossier_ids',[store.get(jid)['request'].get('hypothesis_id')]) and oid in ids
        ])) for d in pack['required_dossiers']}
        for check in pack['executed_checks']:
            original_ids=check['observation_ids']
            check['omitted_observations']=len([oid for oid in original_ids if oid not in ids])
            check['observation_ids']=[oid for oid in original_ids if oid in ids]

        pack['allowed_observation_ids']=ids
        pack['allowed_observation_ids_by_dossier']=allowed_by_dossier
        pack['shared_check_observation_ids']=shared
        pack['citation_contract']=CONTRACT_VERSION
        if batch.get('validation_feedback'):pack['validation_feedback']=batch['validation_feedback']
        context={'input_sha256':digest(pack),'included_ids':ids,'contract_version':CONTRACT_VERSION,
                 'generation':task.get('retry_generation',0),'round':batch['round'],'attempt':batch['attempts']+1}
        context['dossier_allowed_ids']=allowed_by_dossier
        context['prompt_version']='forensic-provider-4'
        with store.tx():
            reservation=store.get(batch['id'])
            if reservation['status']!='pending' or reservation['round']!=batch['round'] or reservation['attempts']!=batch['attempts']:return None
            input_record=store.add('review_input',cid,task_id=task['id'],batch_id=batch['id'],pack=pack,**context)
            context['input_record_id']=input_record['id']
            store.update(batch['id'],attempts=batch['attempts']+1,active_attempt_id=input_record['id'])
        store.update(cid,investigation_stage=f"단서별 정황·반증 검토 {sum(b['status']=='done' for b in batches)+1}/{len(batches)}")
        output=None;receipt={};issues=[]
        try:
            output,receipt=Provider(store.list('config')[-1]['provider']).generate(
                '기본 점검 영역과 단서 각각을 독립적으로 검토하세요. 같은 자료의 반복을 독립 근거로 세지 마세요. '
                '가장 타당한 설명·반대 근거·확인 가능한 다음 검사를 작성하세요. 제공된 각 dossier_id당 하나의 판단이 필요합니다.',pack,role='judgment')
            issues=validation_errors(output,batch['dossier_ids'],ids,allowed_by_dossier,all_obs)
            from .review_validation import check_errors
            issues += check_errors(output, checks, allowed_by_dossier)
            if issues:raise ValueError('판단 근거 검증 실패: '+', '.join(sorted({i['code'] for i in issues})))
        except (ValueError,httpx.TransportError) as ex:
            with store.tx():
                diagnostic=store.add('review_diagnostic',cid,task_id=task['id'],batch_id=batch['id'],
                    rejected_output=output,model_metadata={k:v for k,v in receipt.items() if k!='output'},**context)
                store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='dossier_model_error',
                    batch_id=batch['id'],error=str(ex),validation_errors=issues,diagnostic_id=diagnostic['id'],**context)
                if store.get(batch['id']).get('active_attempt_id')==input_record['id']:
                    store.update(batch['id'],validation_feedback={'errors':issues,
                        'instruction':'Reassess from the provided observations. Only allowed_observation_ids may be cited. Never substitute IDs or remove citations merely to pass validation.'} if issues else None)
            return None
        with store.tx():
            current_task=store.get(task['id']);current_evidence=store.get(evidence['id']);current_batch=store.get(batch['id'])
            if (current_task.get('retry_generation',0)!=task.get('retry_generation',0) or current_task.get('superseded')
                or current_task.get('status') not in (None,'running','queued')
                or not current_evidence.get('connected',True) or current_evidence['signature']!=evidence['signature']
                or current_batch['round']!=batch['round'] or current_batch['attempts']!=batch['attempts']+1
                or current_batch.get('active_attempt_id')!=input_record['id']
                or current_batch['status']!='pending'):
                store.add('review_diagnostic',cid,task_id=task['id'],batch_id=batch['id'],rejected_output=output,
                    rejection='stale_review_scope',**context)
                store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='dossier_model_error',
                    batch_id=batch['id'],error='검토 중 근거 또는 작업 범위 변경',**context)
                return None
            r=store.add('receipt',cid,task_id=task['id'],evidence_id=evidence['id'],receipt_type='dossier_model',batch_id=batch['id'],**context,**receipt)
            store.update(batch['id'],output=output,receipt_id=r['id'])
            calls=output.get('next_checks',[]) if not pack['final_pass'] else []
            jobs=[j for j in store.list('investigation_job',cid) if belongs(j,task)]
            lifetime_jobs=[j for j in store.list('investigation_job',cid) if j.get('task_id')==task['id']]
            source_run=next((o['fields']['run_id'] for o in reversed(list(all_obs.values())) if o['type']=='linux_environment'),None)
            admitted=[];deferred=list(batch.get('deferred_checks',[]))
            for call in calls:
                fingerprint=digest([task['id'],task.get('retry_generation',0),source_run,evidence['signature'],fingerprint_scope(call)])
                old=next((j for j in jobs if j['fingerprint']==fingerprint),None)
                if old:
                    store.update(old['id'],dossier_ids=list(dict.fromkeys(old.get('dossier_ids',[old['request'].get('hypothesis_id')])+[call['hypothesis_id']])))
                    if old['id'] not in batch['job_ids']:admitted.append(old['id'])
                    else:deferred.append({'request':call,'reason':'identical input already checked; no new scope'})
                    continue
                if (len(jobs)>=4 if is_repair(task) else len(lifetime_jobs)>=72) or not source_run:
                    deferred.append({'request':call,'reason':'job budget reached' if source_run else 'source run unavailable'});continue
                job=store.add('investigation_job',cid,task_id=task['id'],evidence_id=evidence['id'],fingerprint=fingerprint,
                    generation=task.get('retry_generation',0),request=call,dossier_ids=[call['hypothesis_id']],purpose='dossier_falsification',source_run=source_run,status='admitted')
                jobs.append(job);lifetime_jobs.append(job);admitted.append(job['id'])
            if admitted:
                store.update(batch['id'],status='await_checks',job_ids=list(dict.fromkeys(batch['job_ids']+admitted)),deferred_checks=deferred)
            elif batch['round']==0 and not is_repair(task):
                store.update(batch['id'],status='pending',round=1,attempts=0,validation_feedback=None,blind_assessment=output)
            else:
                from .judgment import bound_absence
                for f in output['findings']:store.update(f['dossier_id'],status='reviewed',finding=bound_absence(f),receipt_id=r['id'])
                store.update(batch['id'],status='done',deferred_checks=deferred)
        return None
    finally:controller.model_lock.release()
