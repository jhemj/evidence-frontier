from .retrieval import tool_scope, fingerprint_scope
"""Resumable local investigation. Each controller tick crosses one checkpoint.

Graph state contains references. Plans, budget reservations and receipts live in
the case ledger, so replay cannot refund work or silently choose a new plan.
External requests are at least once; accepted results are idempotent.
"""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from typing import TypedDict
import httpx
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from .evidence_access import worker_request, ExecutionUnknown
from .investigation import evidence_pack, HYPOTHESES, store_tool_result
from .models import InvestigationToolRequest, InvestigationTool
from .provider import Provider
from .investigator import consult
from .store import now

VERSION = 'investigation-graph-2'
NODES = ['preflight', 'plan', 'dispatch', 'await_jobs', 'ingest_validate', 'assess', 'challenge', 'stop_gate', 'publish']
MAX_CONSECUTIVE_PLAN_FAILURES = 3


def completed_result_memory(jobs, presented_ids, *, max_jobs=8, max_observation_ids=16,
                            per_job_ids=4):
    """Return a small, source-preserving memory of completed tool results.

    Planner context is finite, but completed jobs must not be selected solely by
    recency: a run of one tool can otherwise hide a still-unassessed result from
    another tool. We reserve bounded lanes for failed/empty and incomplete
    rechecks, then round-robin by tool among unpresented jobs. The returned IDs are the
    original observation IDs; no summaries or synthetic IDs are substituted.
    """
    presented = set(presented_ids)
    completed = [j for j in jobs if j.get('status') == 'ingested']
    # Callers pass newest-first jobs. Preserve that order within each tool.
    fresh = [j for j in completed if any(oid not in presented for oid in j.get('observation_ids', []))]
    failed_or_empty = [j for j in completed if not j.get('observation_ids') or
                       j.get('result_status') in ('failed', 'error') or
                       j.get('result_scope', {}).get('status') in ('failed', 'error')]
    # Preserve a small re-check lane for incomplete/truncated results even when
    # their observations were already presented. This is a progress memory, not
    # a conclusion that the request succeeded or failed.
    recheck_candidates = [j for j in completed if j not in failed_or_empty and
                          all(oid in presented for oid in j.get('observation_ids', [])) and
                          (j.get('result_scope', {}).get('complete') is False or
                           j.get('result_scope', {}).get('truncated') is True)]

    def diverse_jobs(candidates):
        groups = {}
        tool_order = []
        for job in candidates:
            tool = str(job.get('request', {}).get('tool', 'unknown'))
            if tool not in groups:
                groups[tool] = []
                tool_order.append(tool)
            groups[tool].append(job)
        result = []
        while any(groups.get(tool) for tool in tool_order):
            for tool in tool_order:
                if groups[tool]:
                    result.append(groups[tool].pop(0))
        return result

    # Failure/empty and explicitly incomplete jobs are not allowed to be
    # crowded out by a burst of fresh search results.
    priority_jobs = (diverse_jobs(failed_or_empty)[:max(1, max_jobs // 2)] +
                     diverse_jobs(recheck_candidates)[:max_jobs // 4])
    groups = {}
    tool_order = []
    for job in fresh:
        tool = str(job.get('request', {}).get('tool', 'unknown'))
        if tool not in groups:
            groups[tool] = []
            tool_order.append(tool)
        groups[tool].append(job)
    fresh_diverse = []
    while any(groups.get(tool) for tool in tool_order):
        for tool in tool_order:
            if groups[tool]:
                fresh_diverse.append(groups[tool].pop(0))
    priority_job_ids = {j.get('id') for j in priority_jobs}
    fresh_budget = max(0, max_jobs - len(priority_jobs))
    ordered = priority_jobs + [j for j in fresh_diverse if j.get('id') not in priority_job_ids][:fresh_budget]
    ordered_ids = {j.get('id') for j in ordered}
    ordered.extend(j for j in completed if j.get('id') not in ordered_ids)
    selected = ordered[:max_jobs]
    ids = []
    index = []
    rows_by_job = []
    for job in selected:
        raw_ids = list(job.get('observation_ids', []))
        new_ids = [oid for oid in raw_ids if oid not in presented]
        rows_by_job.append((job, raw_ids, new_ids))
    # Round-robin observation IDs as well as jobs: the protected prefix then
    # contains one result from each tool before taking a second result from any
    # one tool.
    for offset in range(per_job_ids):
        for _, _, new_ids in rows_by_job:
            if offset >= len(new_ids) or len(ids) >= max_observation_ids:
                continue
            oid = new_ids[offset]
            if oid not in ids:
                ids.append(oid)
    for job, raw_ids, new_ids in rows_by_job:
        index.append({
            'job_id': job.get('id'),
            'tool': job.get('request', {}).get('tool'),
            # Keep the bounded, normalized locator used for fingerprinting so
            # the model can distinguish a completed request from a new one.
            'request_scope': {key: job.get('request', {}).get(key) for key in (
                'tool', 'path', 'query', 'source_offset', 'byte_offset', 'byte_length',
                'partition_offset', 'inode', 'cursor', 'limit', 'time_from', 'time_to',
                'account') if key in job.get('request', {})},
            'result_status': job.get('result_status'),
            'result_scope': {key: job.get('result_scope', {}).get(key) for key in
                             ('status', 'complete', 'truncated', 'error')
                             if key in job.get('result_scope', {})},
            'memory_reason': ('failed_or_empty' if job in failed_or_empty else
                              'incomplete_recheck' if job in recheck_candidates else
                              'unpresented_result' if job in fresh else 'recent_history'),
            'observation_ids': raw_ids[:per_job_ids],
            'observation_ids_omitted': max(0, len(raw_ids) - per_job_ids),
            'unpresented_observation_ids': new_ids[:per_job_ids],
        })
    return {
        'observation_ids': ids,
        'jobs': index,
        'jobs_total': len(completed),
        'jobs_omitted': max(0, len(completed) - len(selected)),
        'failed_or_empty_total': len(failed_or_empty),
        'failed_or_empty_retained': sum(job in selected for job in failed_or_empty),
        'recheck_total': len(recheck_candidates),
        'recheck_retained': sum(job in selected for job in recheck_candidates),
        'observation_ids_omitted': max(0, sum(len(j.get('observation_ids', [])) for j in selected) - len(ids)),
    }

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

class State(TypedDict, total=False):
    run_id: str
    plan_id: str
    job_id: str
    route: str
    result_id: str

class Investigation:
    def __init__(self, controller, case_id, evidence, task):
        self.c, self.s, self.case_id, self.e, self.task = controller, controller.store, case_id, evidence, task

    def records(self, kind):
        return [r for r in self.s.list(kind, self.case_id) if r.get('task_id') == self.task['id']]

    def preflight(self, state):
        runs = self.records('investigation_run')
        if runs:
            run = runs[-1]
            if run['version'] != VERSION or run['signature'] != self.e['signature']: raise ValueError('재개할 조사 버전·증거가 일치하지 않습니다.')
        else:
            sources = [r for r in self.s.list('receipt', self.case_id) if r.get('evidence_id') == self.e['id'] and r.get('result', {}).get('run_id')]
            if not sources: raise ValueError('원문 수집 결과가 없어 AI 조사를 시작하지 못했습니다.')
            from .coverage_map import gap_checks
            initial_checks=gap_checks([o for o in self.c.active_observations(self.case_id) if o['evidence_id']==self.e['id']])
            if self.s.get(self.case_id).get('target_os')=='windows':
                from .windows_analysis import ioc_checks
                initial_checks=ioc_checks([o for o in self.c.active_observations(self.case_id) if o['evidence_id']==self.e['id']])
            with self.s.tx():
                run = self.s.add('investigation_run', self.case_id, task_id=self.task['id'], evidence_id=self.e['id'],
                    version=VERSION, signature=self.e['signature'], source_run=sources[-1]['result']['run_id'],
                    model_calls=0, max_model_calls=max(6,min(30,int(os.getenv('INVESTIGATION_MODEL_CALLS','12')))), tool_calls=0, max_tool_calls=36, challenge_calls=0,
                    max_challenge_calls=10, review_calls=0, max_review_calls=5, domain_cursor=1,
                    plan_domain_batch_size=3, consecutive_plan_failures=0, started_at=now(), stop_reason=None)
                self.s.add('investigation_plan', self.case_id, task_id=self.task['id'], run_id=run['id'], revision=0,
                    output={'summary':'필수 설정·실행 기록 대조', 'claims':[], 'hypotheses':[], 'remaining_questions':[],
                            'tool_calls':[{'tool':'correlate','reason':'AI 선택과 관계없이 원문 연결 분석'}]+initial_checks},
                    valid_ids=[], focus=[], assessed=False)
        return {'run_id': run['id']}

    def run(self, state): return self.s.get(state['run_id'])

    def model(self, question, pack, run, purpose):
        from .runtime_contract import guard
        guard(self.c,self.case_id,self.task)
        configs = self.s.list('config'); config = configs[-1]['provider'] if configs else {}
        if not config.get('model'): raise ValueError('로컬 모델을 연결한 뒤 계속하세요.')
        if not self.c.model_lock.acquire(blocking=False): raise ValueError('다른 AI 요청이 끝난 뒤 계속하세요.')
        try:
            # Reserve before transmission. A crash never refunds this reservation.
            with self.s.tx():
                guard(self.c,self.case_id,self.task)
                config=self.s.list('config')[-1]['provider']
                reservation = self.s.add('model_reservation', self.case_id, task_id=self.task['id'],
                    generation=self.task.get('retry_generation',0),purpose=purpose,status='reserved',
                    target='조사 영역 '+', '.join(str(h['number']) for h in pack.get('hypotheses',[]) if h.get('number')))
                self.s.update(run['id'], model_calls=run['model_calls'] + 1)
            from . import model_availability
            try:
                output, receipt = consult(config, question, pack, role='investigator', provider_factory=Provider)
                model_availability.recovered(self.s,self.case_id,receipt.get('transport_identity'))
            except (httpx.TransportError,ValueError) as ex:
                if model_availability.unavailable(ex):
                    with self.s.tx():
                        model_availability.defer(self.s,self.case_id,self.task,ex,reservation_id=reservation['id'])
                        self.s.update(reservation['id'],status='service_unavailable',error=str(ex))
                    return None
                category=getattr(ex,'category','transport_or_validation')
                failures=run.get('consecutive_plan_failures',0)+1
                # Partition subsequent responses, never accept/repair truncated JSON
                # or silently increase pinned generation budgets. Coverage stays queued.
                batch_size=1 if category=='output_budget' else run.get('plan_domain_batch_size',3)
                recovery={'action':'partition_and_compact' if category=='output_budget' else 'retry_with_feedback',
                    'domain_batch_size':batch_size,'consecutive_failures':failures,
                    'consecutive_failure_limit':MAX_CONSECUTIVE_PLAN_FAILURES}
                metadata=getattr(ex,'metadata',{})
                with self.s.tx():
                    self.s.add('receipt',self.case_id,task_id=self.task['id'],evidence_id=self.e['id'],receipt_type='model_error',reservation_id=reservation['id'],error=str(ex),
                        failure_category=category,raw_output=getattr(ex,'raw_output',None),recovery=recovery,
                        **{k:metadata[k] for k in ('usage','generation_settings','prompt_characters','elapsed_seconds') if k in metadata})
                    self.s.update(reservation['id'],status='failed',error=str(ex))
                    self.s.update(run['id'],consecutive_plan_failures=failures,plan_domain_batch_size=batch_size)
                return None
            with self.s.tx():
                guard(self.c,self.case_id,self.task)
                self.s.add('receipt', self.case_id, task_id=self.task['id'], evidence_id=self.e['id'], receipt_type='investigator_model', reservation_id=reservation['id'], **receipt)
                self.s.update(reservation['id'], status='received', usage=receipt.get('usage'))
                self.s.update(run['id'],consecutive_plan_failures=0)
            return output
        finally: self.c.model_lock.release()

    def plan(self, state):
        run = self.run(state); revision = len(self.records('investigation_plan'))
        existing = [p for p in self.records('investigation_plan') if not p.get('assessed')]
        if existing: return {'plan_id': existing[-1]['id'], 'route': 'dispatch'}
        if run.get('consecutive_plan_failures',0)>=MAX_CONSECUTIVE_PLAN_FAILURES:
            self.s.update(run['id'],stop_reason='planner_failure_limit')
            raise ValueError('조사 계획 생성이 연속 실패했습니다. 오류와 복구 이력을 보존했으며 조사는 미완료입니다.')
        if run['model_calls'] >= run['max_model_calls']:
            self.s.update(run['id'], stop_reason='model_budget_exhausted'); return {'route': 'stop_gate'}
        from . import question_engine
        memory=question_engine.refresh(self.c,self.case_id,self.e,self.task)
        question_context=question_engine.view(memory,run.get('plan_domain_batch_size',3))
        scoped=[o for o in self.c.active_observations(self.case_id) if o['evidence_id']==self.e['id']]
        domain_rows=[h for h in self.s.list('hypothesis',self.case_id)
                     if h.get('evidence_id')==self.e['id'] and h.get('hypothesis_kind','coverage_domain')=='coverage_domain']
        seen_domains={n for p in self.records('investigation_plan') for n in p.get('focus',[])}
        # Evidence availability and unresolved questions guide the order. The
        # checklist is exposed for coverage; it no longer owns stop/next policy.
        domains=sorted((h for h in domain_rows if h.get('number') not in seen_domains),
            key=lambda h:(-len({o['type'] for o in scoped if o['type'] in h.get('expected_source_types',[])}),h['number']))
        focus=[h['number'] for h in domains[:run.get('plan_domain_batch_size',3)]]
        final_synthesis = run['model_calls'] == run['max_model_calls'] - 1
        from .platforms import WINDOWS_HYPOTHESES
        hypotheses=WINDOWS_HYPOTHESES if self.s.get(self.case_id).get('target_os')=='windows' else HYPOTHESES
        presented=[oid for p in self.records('investigation_plan') for oid in p.get('valid_ids',[])]
        completed = list(reversed([j for j in self.records('investigation_job')
                                   if j.get('status') == 'ingested']))
        result_memory = completed_result_memory(completed, presented)
        preferred = [o['id'] for o in self.c.active_observations(self.case_id) if o['evidence_id'] == self.e['id'] and
                     o['type'] in {t for n in focus for t in hypotheses[n-1][1]}]
        recent_results = [oid for job in completed for oid in job.get('observation_ids', [])]
        priority_ids = list(dict.fromkeys([i for q in question_context['questions'] for i in q.get('observation_ids',[])]
                                         +result_memory['observation_ids']))
        # Keep the first bounded memory lane protected; the remaining IDs stay
        # disclosed in completed_result_memory and may be deferred if their
        # source payload would exceed the final context budget.
        protected_priority_ids = priority_ids[:8]
        pack = evidence_pack(self.c, self.case_id, self.s.get(self.case_id)['question'], priority_ids + recent_results + preferred,
            evidence_id=self.e['id'],focus=focus,presented=presented,task_id=self.task['id'],generation=self.task.get('retry_generation',0))
        from .evidence_selection import audit as selection_audit
        scoped_observations = [o for o in self.c.active_observations(self.case_id)
                               if o.get('evidence_id') == self.e['id']]
        coverage = [h for h in self.s.list('hypothesis', self.case_id)
                    if h.get('contract') in ('linux-v1', 'windows-v1') and
                    h.get('evidence_id') == self.e['id'] and
                    (not focus or h.get('number') in focus)]
        # evidence_selection deliberately interleaves fairness lanes.  That
        # means a requested result can still fall just beyond the byte budget
        # after the selector has produced its order. Promote bounded, fresh
        # result IDs into the final candidate set before context fitting so the
        # model actually receives the completion we are trying not to repeat.
        selected_ids = {o.get('id') for o in pack.get('observations', [])}
        missing_priority = [oid for oid in protected_priority_ids if oid not in selected_ids]
        if missing_priority:
            from .investigation import compact_observation
            available = {o['id']: o for o in self.c.active_observations(self.case_id)
                         if o.get('evidence_id') == self.e['id']}
            replacements = [compact_observation(available[oid]) for oid in missing_priority if oid in available]
            for replacement in replacements:
                replace_at = next((i for i in range(len(pack['observations']) - 1, -1, -1)
                                   if pack['observations'][i].get('id') not in protected_priority_ids), None)
                if replace_at is None:
                    break
                pack['observations'][replace_at] = replacement
        # Rebuild the complete audit even when promotion did not occur. The
        # selector's original audit no longer describes the final observation
        # set after any replacement/removal, and must remain evidence-scoped.
        prior_reasons = {row.get('id'): row.get('reason', 'baseline_rank')
                         for row in pack.get('selection_audit', {}).get('selected', [])}
        reasons = {o.get('id'): ('completed_result_memory' if o.get('id') in protected_priority_ids
                                 else prior_reasons.get(o.get('id'), 'baseline_rank'))
                   for o in pack.get('observations', [])}
        pack['selection_audit'] = selection_audit(
            scoped_observations, pack['observations'], reasons, coverage, presented, 'guided')
        pack['included_observations'] = len(pack['observations'])
        from .review_context import bounded, fit
        pack['completed_result_memory'] = result_memory
        pack['question_memory']=question_context
        from .test_admission import catalog
        pack['tool_capabilities']=catalog(pack.get('target_os','linux'))
        pack['coverage_checklist']=[{'number':h['number'],'question':h['text'],
            'presented_for_planning':h['number'] in seen_domains} for h in domain_rows]
        pack['completed_tools']=[{'id':j['id'],'request':tool_scope(j['request']),
            'result_scope':bounded(j.get('result_scope',{}),400,4)} for j in reversed(completed[:12])]
        # These counters describe the separate 12-entry completed_tools lane;
        # the smaller result memory publishes its own omission counters.
        pack['completed_tools_total']=len(completed)
        pack['completed_tools_omitted']=max(0, len(completed)-12)
        pack['remaining_tool_budget']=max(0,run['max_tool_calls']-run['tool_calls'])
        pack['remaining_model_calls']=max(0,run['max_model_calls']-run['model_calls']-1)
        failures=[r for r in self.records('receipt') if r.get('receipt_type')=='model_error' and r.get('reservation_id')]
        if failures and run.get('consecutive_plan_failures',1):
            category=failures[-1].get('failure_category','transport_or_validation')
            instruction=('Rejected output is not evidence. Correct the JSON schema; do not repeat invalid arguments. '
                         'read_source accepts path, not artifact_path.')
            if category=='output_budget':
                instruction=('Rejected truncated output is not evidence. Start a fresh complete JSON response, not a continuation. '
                             'The response exceeded the generation budget, not necessarily the input context. '
                             'Process only the current focus; other domains remain pending. Use concise fields and do not repeat evidence or prose.')
            pack['output_validation_feedback']={'error':failures[-1]['error'][:2000],
                'category':category,'instruction':instruction}
        compact=run.get('plan_domain_batch_size',3)==1
        if compact:
            pack['response_budget_guidance']={'mode':'partition_and_compact','focus':focus,
                'scope':'This limits one response, not the total number of hypotheses. Unprocessed domains and further questions remain pending.',
                'instruction':'Use short title/card_summary/change_reason/reasoning without duplicating the same prose. Preserve essential qualifiers and exact source IDs.'}
        # fit() may reject a real large source after its lossless compaction
        # passes. Remove only ordinary candidates, one at a time, and retry;
        # promoted result IDs remain in the pack. Each retry rebuilds the
        # selection audit so counts and family omissions remain truthful.
        from copy import deepcopy
        while True:
            candidate = deepcopy(pack)
            try:
                fit(candidate)
                pack = candidate
                break
            except ValueError as ex:
                remove_at = next((i for i in range(len(pack['observations']) - 1, -1, -1)
                                  if pack['observations'][i].get('id') not in protected_priority_ids), None)
                if remove_at is None:
                    raise
                pack['observations'].pop(remove_at)
                pack['included_observations'] = len(pack['observations'])
                reasons = {row.get('id'): row.get('reason', 'baseline_rank')
                           for row in pack.get('selection_audit', {}).get('selected', [])}
                pack['selection_audit'] = selection_audit(
                    scoped_observations, pack['observations'], reasons,
                    coverage, presented, 'guided')
        question = ('question_memory의 미해결 질문·반론·새 검사 결과를 우선 검토하세요. 중요한 설명과 정상 운영 대안을 구별할 다음 검사를 선택하세요. '
                    f'조사 범위 누락 점검용 영역은 {focus}입니다. 영역 수를 채우려고 가설을 만들지 마세요. '
                    '최소한 사건 핵심 질문과 가장 타당한 경쟁 설명을 검토하되, 자료가 부족하면 열린 질문으로 남기세요. '
                    'hypotheses에는 원문으로 검증 가능한 새 가설과 변경을 자연스럽게 작성하세요. 점검 문자열·정상 작업과 실제 행위를 구분하세요. '
                    '추가 도구를 최대 4개 선택하세요. 이미 실행한 요청은 반복하지 마세요. 설정/시도/실행/성공은 별도 주장입니다. '
                    '이번 응답의 JSON schema 한도는 hypotheses 최대 3개, remaining_questions 최대 10개입니다. '
                    '이는 전체 조사 가설 수 제한이 아니며 다음 호출에서 계속 제안할 수 있습니다.')
        if compact:
            question += (' 응답 예산 복구 모드입니다. 이번 영역과 새로 발견한 가장 중요한 질문에 집중하세요. '
                         'summary는 200자 이내, 각 설명 필드는 한두 문장으로 간결하게 쓰고 같은 내용을 여러 필드에 반복하지 마세요. '
                         '불확실성·근거 ID는 유지하고 나머지 질문은 remaining_questions에 짧게 남기세요. 이는 전체 가설 수 제한이 아닙니다.')
        if final_synthesis:
            question += (' 이번 호출은 마지막 결과 통합입니다. tool_calls는 반드시 빈 배열로 두세요. '
                         '가장 최근 실제 검사 결과를 반영하고 이전 설명의 미확인 사항 중 해소된 내용을 정정하세요. '
                         '남은 검사는 remaining_questions에 남기고 완료한 것으로 표현하지 마세요.')
        self.s.update(self.case_id, investigation_stage='로컬 AI · 미해결 질문과 다음 검사', investigation_round=revision + 1)
        output = self.model(question, pack, run, 'plan')
        if output is None:return {'route':'plan'}
        if final_synthesis:
            # Keep the final model reservation for interpretation of completed work.
            # No tool may run afterward without a remaining synthesis budget.
            output['remaining_questions'] += [f"미실행 추가 제안: {call['tool']} {call.get('path') or call.get('query') or ''}" for call in output['tool_calls']]
            output['tool_calls'] = []
        with self.s.tx():
            plan = self.s.add('investigation_plan', self.case_id, task_id=self.task['id'], run_id=run['id'],
                revision=revision, generation=self.task.get('retry_generation',0), output=output, valid_ids=[o['id'] for o in pack['observations']], focus=focus, assessed=False,
                selection_audit=pack.get('selection_audit',{}))
            self.s.update(plan['id'],question_context=question_context)
            self.s.update(run['id'], domain_cursor=min(11,1+len(seen_domains|set(focus))))
            self.s.add('message', self.case_id, role='assistant', text=output['summary'], mode='ai_candidate', partial=True, automatic=True)
        return {'plan_id': plan['id'], 'route': 'dispatch'}

    def admit(self, state, calls, purpose, claim_id=None):
        run = self.run(state)
        for call in calls:
            from . import question_engine
            proposal=dict(call)
            call = tool_scope(call)
            key = digest([VERSION, run['id'], run['signature'], run['source_run'], fingerprint_scope(call)])
            questions=question_engine.case_memory.project(self.s,self.case_id,self.task)
            qid=proposal.get('question_id')
            q=next((q for q in questions if q['id']==qid),None)
            if qid and q is None:
                self.s.add('receipt',self.case_id,task_id=self.task['id'],receipt_type='test_intent_rejected',
                    error='현재 범위를 벗어난 질문 참조',request=proposal)
                continue
            q=q or (questions[0] if questions else {'question_key':self.task['id']+':'+purpose,'version':0})
            intent=question_engine.reserve(self.s,self.case_id,self.task,q,proposal,self.e,run['source_run'])
            if not intent['admission']['eligible']:continue
            from .test_admission import reusable,bind_reuse,execution_fingerprint
            key=execution_fingerprint(self.records('investigation_job'),key)
            old=reusable(self.records('investigation_job'),key,proposal,source_run=run['source_run'],evidence_id=self.e['id'])
            if old:
                bind_reuse(self.s,self.case_id,old,intent)
                old=self.s.update(old['id'],test_intent_ids=list(dict.fromkeys(old.get('test_intent_ids',[])+[intent['id']])),
                    claim_ids=list(dict.fromkeys(old.get('claim_ids',[])+([claim_id] if claim_id else []))))
                if old['status']=='ingested':question_engine.finish_intents(self.s,self.case_id,old)
                continue
            budget_key = 'challenge_calls' if purpose == 'challenge' else 'tool_calls'
            maximum = 'max_challenge_calls' if purpose == 'challenge' else 'max_tool_calls'
            run = self.run(state)
            if run[budget_key] >= run[maximum]:
                self.s.update(run['id'], stop_reason=purpose + '_budget_exhausted'); break
            with self.s.tx():
                # The controller serializes job admission and pause under this lock.
                if self.s.get(self.case_id)['status'] != 'running': return
                self.s.add('investigation_job', self.case_id, task_id=self.task['id'], run_id=run['id'],
                    source_run=run['source_run'],
                    evidence_id=self.e['id'],generation=self.task.get('retry_generation',0),
                    plan_id=state.get('plan_id'), fingerprint=key, request=call, purpose=purpose, status='admitted',
                    test_intent_ids=[intent['id']],claim_ids=[claim_id] if claim_id else [])
                self.s.update(run['id'], **{budget_key: run[budget_key] + 1})

    def dispatch(self, state):
        plan = self.s.get(state['plan_id'])
        self.admit(state, plan['output']['tool_calls'], 'exploration')
        pending = [j for j in self.records('investigation_job') if j['status'] in ('admitted','submitted')]
        if not pending: return {'route': 'assess', 'job_id': ''}
        job = pending[0]; run = self.run(state)
        if self.s.get(self.case_id)['status'] != 'running': return {'route':'await_jobs' if job['status']=='submitted' else 'dispatch', 'job_id':job['id']}
        body = {'job_key': job['fingerprint'], 'signature': self.e['signature'], 'action': 'investigation_tool', 'path': self.e['path'],
                'investigation': {'evidence_path': self.e['path'], 'run_id': run['source_run'], 'request': job['request'],
                                  'target_os': self.s.get(self.case_id).get('target_os','linux')}}
        self.s.update(self.case_id, investigation_stage='추가 조사 · ' + job['request']['tool'])
        if not job.get('dispatched_at'):self.s.update(job['id'],dispatched_at=now())
        try:
            worker_request('POST', '/jobs', json=body, timeout=20)
            self.s.update(job['id'], status='submitted')
        except httpx.TransportError:
            # Delivery may have happened. Re-submit the SAME identity only.
            self.s.update(job['id'], status='submitted')
        return {'job_id': job['id'], 'route': 'await_jobs'}

    def await_jobs(self, state):
        job = self.s.get(state['job_id'])
        try: reply = worker_request('GET', '/jobs/' + job['fingerprint'], timeout=20)
        except httpx.TransportError: return {'route': 'await_jobs'}
        except httpx.HTTPStatusError as ex:
            if ex.response.status_code == 404: return {'route': 'dispatch'}
            raise
        if reply['status'] in ('queued','running'):
            if job.get('worker_status')!=reply['status']:self.s.update(job['id'],worker_status=reply['status'])
            return {'route': 'await_jobs'}
        if reply['status'] == 'execution_unknown': raise ExecutionUnknown('작업 실행 여부가 불명확합니다. 이전 실행을 확인할 때까지 재실행하지 않습니다.')
        result = reply.get('result', {'status':'failed','observations':[], 'error':reply.get('error')})
        if reply.get('result') and digest(result) != reply['result_sha256']: raise ValueError('작업 결과 해시 불일치')
        self.s.update(job['id'], status='received', result=result,ended_at=now(),result_status=result['status'],error=result.get('error'))
        return {'route': 'ingest_validate'}

    def ingest_validate(self, state):
        job = self.s.get(state['job_id'])
        if job['status'] != 'ingested':
            with self.s.tx():
                ids = store_tool_result(self.c, self.case_id, self.e, self.task, job['result'], job['request'])
                self.s.update(job['id'], status='ingested', observation_ids=ids, result_status=job['result']['status'], result_scope={k:v for k,v in job['result'].items() if k!='observations'}, result=None)
                from .question_engine import finish_intents
                finish_intents(self.s,self.case_id,self.s.get(job['id']))
        return {'route': 'dispatch'}

    def assess(self, state):
        plan = self.s.get(state['plan_id'])
        if not plan['assessed']:
            valid = set(plan['valid_ids'])
            with self.s.tx():
                from .question_engine import apply_updates
                apply_updates(self.s,self.case_id,self.task,plan)
                for candidate in plan['output']['claims']:
                    if candidate['claim_type'] == 'absence' or not candidate['observation_ids'] or not set(candidate['observation_ids']).issubset(valid): continue
                    if any(c['text'] == candidate['text'] for c in self.records('claim')): continue
                    self.s.add('claim', self.case_id, task_id=self.task['id'], status='candidate', automatic=True,
                               falsification=None, challenge_status='pending', **candidate)
                for proposal_index, assessment in enumerate(plan['output']['hypotheses']):
                    refs = assessment['supporting_evidence_ids'] + assessment['refuting_evidence_ids']
                    if not set(refs).issubset(valid) or set(assessment['supporting_evidence_ids'])&set(assessment['refuting_evidence_ids']): continue
                    from .hypothesis_ledger import apply as apply_hypothesis
                    action=assessment.get('action','update')
                    hid=assessment.get('hypothesis_card_id') or assessment.get('hypothesis_id')
                    h=next((h for h in self.s.list('hypothesis',self.case_id) if h.get('evidence_id')==self.e['id'] and
                        h.get('hypothesis_kind','coverage_domain')=='coverage_domain' and
                        (h['id']==hid if hid else h.get('number')==assessment.get('number'))),None)
                    # Explicit create always goes through the dynamic ledger.
                    # A known domain ID is a category hint, not a dynamic card
                    # update. Never erase an unknown/wrong-scope supplied ID.
                    if action=='create' or h is None:
                        safe = dict(assessment)
                        if action=='create' and h is not None:
                            safe['hypothesis_id']=''
                            safe['hypothesis_card_id']=''
                            safe['number']=None
                        safe['source_plan_id'] = plan['id']
                        safe['task_id'] = self.task['id']
                        safe['generation'] = self.task.get('retry_generation', 0)
                        adopted=apply_hypothesis(self.s, self.case_id, self.e['id'], safe, valid,
                                         task_id=self.task['id'], generation=safe['generation'], source_plan_id=plan['id'], proposal_index=proposal_index)
                        if adopted is None:
                            self.s.add('receipt',self.case_id,task_id=self.task['id'],evidence_id=self.e['id'],
                                receipt_type='hypothesis_update_rejected',source_plan_id=plan['id'],proposal_index=proposal_index,
                                error='가설 ID·원문 인용·변경 범위 검증 실패. 이 제안은 현재 판단에 반영하지 않았습니다.')
                        continue
                    basis=assessment.get('basis','positive_evidence')
                    support=assessment['supporting_evidence_ids']; refute=assessment['refuting_evidence_ids']
                    if (action=='refute' and (not refute or basis=='absence')) or (action=='reinforce' and (not support or basis=='absence')):
                        continue
                    judgment='미확인'
                    if action not in ('hold','refute') and support and basis!='absence':
                        judgment='유력' if assessment['judgment'] in ('확정','확인') else assessment['judgment']
                    self.s.update(h['id'], status='reviewed_with_gaps', judgment=judgment,
                        ai_suggested_judgment=assessment['judgment'], ai_candidate=True, reasoning=assessment['reasoning'],
                        observation_ids=refs, supporting_evidence_ids=support, refuting_evidence_ids=refute,
                        remaining_checks=assessment['remaining_checks'], judgment_history=h.get('judgment_history',[]) +
                        [{'at':now(), 'judgment':judgment, 'action':action, 'reason':assessment['reasoning'],
                          'source':'로컬 AI 후보 · 원문 판정 범위 유지'}])
                self.s.update(plan['id'], assessed=True)
        return {}

    def challenge(self, state):
        # Re-read cited originals and search the target path in other collected
        # event sources. These receipts establish what was actually checked;
        # they do NOT automatically validate the model's interpretation.
        run = self.run(state)
        # Revisit the oldest untested claim, not permanently the first five.
        # Resource limits bound calls, never the set of eligible hypotheses.
        claims = sorted(self.records('claim'),key=lambda c:(bool(c.get('falsification')),c['created_at'],c['id']))
        claims = [c for c in claims if c.get('challenge_status')=='pending'][:1]
        specified=[]
        for claim in claims:
            if claim.get('challenge_status') != 'pending': continue
            paths = []
            for oid in claim['observation_ids']:
                fields = self.s.get(oid)['fields']
                if fields.get('path', '').startswith('/'): paths.append(fields['path'])
            if paths:
                calls=[{'tool':'read_file','path':paths[0], 'reason':'주장 근거 원문과 점검/설정 문맥 재검사'},
                       {'tool':'search','query':Path(paths[0]).name, 'reason':'별도 실행 기록·정상 운영 문맥 대조'}]
                self.admit(state,calls,'challenge',claim_id=claim['id'])
            specified.append((claim,paths))
        for claim,paths in specified:
            self.s.update(claim['id'], challenge_status='specified' if paths else 'unavailable',
                          challenge_limit='원문 대조·기록 검색은 해석의 자동 승인이나 독립 발생원 검증이 아닙니다.')
        jobs=self.records('investigation_job')
        if any(j['status']!='ingested' for j in jobs):return {}
        claim=next((c for c in self.records('claim') if c.get('challenge_status')=='specified' and not c.get('falsification')),None)
        run=self.run(state)
        if claim and run.get('review_calls',0)<run.get('max_review_calls',5):
            configs=self.s.list('config');config=configs[-1]['provider'] if configs else {}
            from .claim_review import build_pack, bound_jobs, validate_scope, input_hash
            from .review_context import InputBudgetError, fit_metadata_only
            # Leave room for bounded validation feedback in a retry. The source
            # floor must fit before optional context may consume this budget.
            try:pack=build_pack(self.c,claim,jobs,maximum=33952)
            except InputBudgetError as ex:
                self.s.add('receipt',self.case_id,task_id=self.task['id'],claim_id=claim['id'],
                    receipt_type='claim_review_input_error',failure_category='input_projection',error=str(ex))
                self.s.update(claim['id'],challenge_status='input_blocked',challenge_limit=str(ex))
                return {}
            failures=[r for r in self.records('receipt') if r.get('claim_id')==claim['id'] and r.get('receipt_type')=='model_error']
            if failures:pack['output_validation_feedback']={'error':failures[-1]['error'][:1600],
                'instruction':'Rejected output is not evidence. Return only alternatives, contradicting_observation_ids, missing_checks as defined by Falsification.'}
            if not self.c.model_lock.acquire(blocking=False):raise ValueError('로컬 AI 응답 대기')
            try:
                from .runtime_contract import guard
                with self.s.tx():
                    guard(self.c,self.case_id,self.task)
                    validate_scope(self.c,claim,pack)
                    fit_metadata_only(pack)
                    input_record=self.s.add('falsifier_input',self.case_id,task_id=self.task['id'],claim_id=claim['id'],
                        pack=pack,input_sha256=input_hash(pack))
                    config=self.s.list('config')[-1]['provider']
                    self.s.update(run['id'],review_calls=run.get('review_calls',0)+1)
                    reservation=self.s.add('model_reservation',self.case_id,task_id=self.task['id'],
                        generation=self.task.get('retry_generation',0),purpose='falsifier',status='reserved',
                        target=claim['text'][:300],claim_id=claim['id'])
                self.s.update(self.case_id,investigation_stage='원문 대조 후 경쟁 설명 검토')
                from .review_stream import resolved
                allowed_ids=sorted({o['id'] for o in resolved(pack)['observations']})
                question=('실제로 실행한 검사와 원문을 바탕으로 다음 주장의 대안 설명·상충 근거·미완료 검사를 검토하세요. '
                    '동일 출처 재읽기를 독립 검증으로 부르지 마세요: '+claim['text']+
                    '\n반증 검토의 contradicting_observation_ids에는 아래 허용 목록의 ID만 정확히 복사하세요. '
                    '목록 밖 ID는 사용하지 마세요: '+', '.join(allowed_ids))
                from . import model_availability
                try:
                    review,receipt=consult(config,question,pack,role='falsifier',provider_factory=Provider)
                    model_availability.recovered(self.s,self.case_id,receipt.get('transport_identity'))
                except (httpx.TransportError,ValueError) as ex:
                    if model_availability.unavailable(ex):
                        with self.s.tx():
                            model_availability.defer(self.s,self.case_id,self.task,ex,reservation_id=reservation['id'],input_record_id=input_record['id'])
                            self.s.update(reservation['id'],status='service_unavailable',error=str(ex),ended_at=now())
                        return {}
                    self.s.add('receipt',self.case_id,task_id=self.task['id'],evidence_id=self.e['id'],receipt_type='model_error',claim_id=claim['id'],reservation_id=reservation['id'],input_record_id=input_record['id'],error=str(ex))
                    self.s.update(reservation['id'],status='failed',error=str(ex),ended_at=now())
                    return {}
                invalid=sorted(set(review['contradicting_observation_ids'])-set(allowed_ids))
                if invalid:
                    # Never silently delete a citation: retain the rejected model
                    # attempt and spend only the already-reserved challenge slot.
                    error='반증 검토가 허용 목록에 없는 근거를 참조함: '+', '.join(invalid[:20])
                    self.s.add('receipt',self.case_id,task_id=self.task['id'],evidence_id=self.e['id'],receipt_type='model_error',claim_id=claim['id'],reservation_id=reservation['id'],input_record_id=input_record['id'],failure_category='citation_contract',error=error,rejected_observation_ids=invalid)
                    self.s.update(reservation['id'],status='failed',error=error,ended_at=now())
                    return {}
                with self.s.tx():
                    guard(self.c,self.case_id,self.task)
                    validate_scope(self.c,claim,pack)
                    self.s.add('receipt',self.case_id,task_id=self.task['id'],evidence_id=self.e['id'],receipt_type='automatic_falsifier',claim_id=claim['id'],reservation_id=reservation['id'],input_record_id=input_record['id'],**receipt)
                    self.s.update(reservation['id'],status='received',ended_at=now())
                    self.s.update(claim['id'],falsification=review,challenge_status='reviewed_with_limits',
                        actual_check_ids=[j['id'] for j in bound_jobs(claim,jobs)],review_type='실제 도구 대조 후 로컬 AI 경쟁 설명 검토 · 독립 해석 검증 아님')
            finally:self.c.model_lock.release()
        return {}

    def stop_gate(self, state):
        run = self.run(state)
        pending = [j for j in self.records('investigation_job') if j['status'] != 'ingested']
        if pending: return {'route':'dispatch'}
        if run.get('review_calls',0)<run.get('max_review_calls',5) and any(c.get('challenge_status') in ('pending','specified') and not c.get('falsification') for c in self.records('claim')):return {'route':'challenge'}
        plan = self.s.get(state['plan_id']) if state.get('plan_id') else None
        if plan and plan['output']['tool_calls'] and not any(j.get('plan_id')==plan['id'] and j['purpose']=='exploration' for j in self.records('investigation_job')):
            # All mandatory domain passes are already processed above. Repeated
            # requests over identical inputs do not justify another model loop.
            self.s.update(run['id'],stop_reason=run['stop_reason'] or 'no_new_check_scope')
            return {'route':'publish'}
        if not run['stop_reason'] and plan and plan['output']['tool_calls'] and run['model_calls'] < run['max_model_calls']:
            # One further synthesis can use completed challenge tool results.
            return {'route':'plan'}
        reason = run['stop_reason'] or ('model_budget_exhausted' if run['model_calls'] >= run['max_model_calls'] else 'planning_gap_no_next_test')
        self.s.update(run['id'], stop_reason=reason)
        return {'route':'publish'}

    def publish(self, state):
        run = self.run(state)
        old = self.records('investigation_result')
        if old: return {'result_id':old[-1]['id']}
        result = {'status':'partial','complete':False,'observations':[], 'truncated':False, 'tool':VERSION, 'version':'1',
                  'stop_reason':run['stop_reason'], 'model_calls':run['model_calls'], 'tool_calls':run['tool_calls'],
                  'challenge_calls':run['challenge_calls'], 'domains_requested':min(10,run['domain_cursor']-1),
                  'domains_assessed':sum(h.get('status')=='reviewed_with_gaps' for h in self.s.list('hypothesis',self.case_id) if h.get('evidence_id')==self.e['id'] and h.get('contract') in ('linux-v1','windows-v1')),
                  'reviews_completed':sum(bool(c.get('falsification')) for c in self.records('claim')),
                  'reviews_unfinished':sum(not c.get('falsification') for c in self.records('claim')),
                  'review_stop_reason':'review_budget_exhausted_with_unfinished_claims' if run.get('review_calls',0)>=run.get('max_review_calls',5) and any(not c.get('falsification') for c in self.records('claim')) else None,
                  'error':'자동 조사 종료. 지원 범위·자료 공백·AI 중간 해석을 포함합니다.'}
        with self.s.tx():
            record = self.s.add('investigation_result', self.case_id, task_id=self.task['id'], run_id=run['id'], result=result)
            self.s.update(self.case_id, investigation_stage='결과 패키지 생성', investigation_result='자동 조사 종료 · 확인 제한 있음', result_revision=record['id'])
        return {'result_id':record['id']}

    def graph(self, saver):
        builder = StateGraph(State)
        for name in NODES: builder.add_node(name, getattr(self,name))
        builder.add_edge(START,'preflight'); builder.add_edge('preflight','plan')
        for name in ('plan','dispatch','await_jobs','ingest_validate','stop_gate'):
            builder.add_conditional_edges(name, lambda s:s['route'], NODES)
        builder.add_edge('assess','challenge'); builder.add_edge('challenge','stop_gate'); builder.add_edge('publish',END)
        return builder.compile(checkpointer=saver, interrupt_after=NODES)

def tick(controller, case_id, evidence, task):
    from .model_availability import waiting
    if waiting(controller.store,case_id):return None
    from .runtime_contract import guard
    guard(controller,case_id,task)
    root = Path(os.getenv('DATA_ROOT', str(Path(__file__).resolve().parent.parent / 'data')))
    root.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(root/'investigation-checkpoints.sqlite3', check_same_thread=False) as connection:
        connection.execute('PRAGMA synchronous=FULL')
        graph = Investigation(controller,case_id,evidence,task).graph(SqliteSaver(connection))
        config = {'configurable':{'thread_id':task['id']}, 'recursion_limit':100}
        snapshot = graph.get_state(config)
        if controller.store.get(case_id)['status'] == 'pause_requested' and not set(snapshot.next).intersection({'await_jobs','ingest_validate'}):
            controller.store.update(case_id,status='paused')
            return None
        if snapshot.values and not snapshot.next:
            return controller.store.get(snapshot.values['result_id'])['result']
        graph.invoke(None if snapshot.values else {}, config, durability='sync')
        snapshot = graph.get_state(config)
        if not snapshot.next: return controller.store.get(snapshot.values['result_id'])['result']
    return None
