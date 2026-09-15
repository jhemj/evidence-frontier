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
from .store import now

VERSION = 'investigation-graph-2'
NODES = ['preflight', 'plan', 'dispatch', 'await_jobs', 'ingest_validate', 'assess', 'challenge', 'stop_gate', 'publish']

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
            with self.s.tx():
                run = self.s.add('investigation_run', self.case_id, task_id=self.task['id'], evidence_id=self.e['id'],
                    version=VERSION, signature=self.e['signature'], source_run=sources[-1]['result']['run_id'],
                    model_calls=0, max_model_calls=max(6,min(30,int(os.getenv('INVESTIGATION_MODEL_CALLS','12')))), tool_calls=0, max_tool_calls=36, challenge_calls=0,
                    max_challenge_calls=10, review_calls=0, max_review_calls=5, domain_cursor=1, started_at=now(), stop_reason=None)
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
                reservation = self.s.add('model_reservation', self.case_id, task_id=self.task['id'], purpose=purpose, status='reserved')
                self.s.update(run['id'], model_calls=run['model_calls'] + 1)
            try:output, receipt = Provider(config).generate(question, pack, role='investigator')
            except (httpx.TransportError,ValueError) as ex:
                with self.s.tx():
                    self.s.add('receipt',self.case_id,task_id=self.task['id'],evidence_id=self.e['id'],receipt_type='model_error',reservation_id=reservation['id'],error=str(ex))
                    self.s.update(reservation['id'],status='failed',error=str(ex))
                return None
            guard(self.c,self.case_id,self.task)
            with self.s.tx():
                self.s.add('receipt', self.case_id, task_id=self.task['id'], evidence_id=self.e['id'], receipt_type='investigator_model', reservation_id=reservation['id'], **receipt)
                self.s.update(reservation['id'], status='received', usage=receipt.get('usage'))
            return output
        finally: self.c.model_lock.release()

    def plan(self, state):
        run = self.run(state); revision = len(self.records('investigation_plan'))
        existing = [p for p in self.records('investigation_plan') if not p.get('assessed')]
        if existing: return {'plan_id': existing[-1]['id'], 'route': 'dispatch'}
        if run['model_calls'] >= run['max_model_calls']:
            self.s.update(run['id'], stop_reason='model_budget_exhausted'); return {'route': 'stop_gate'}
        focus = list(range(run['domain_cursor'], min(11, run['domain_cursor'] + 3)))
        final_synthesis = run['model_calls'] == run['max_model_calls'] - 1
        preferred = [o['id'] for o in self.c.active_observations(self.case_id) if o['evidence_id'] == self.e['id'] and
                     o['type'] in {t for n in focus for t in HYPOTHESES[n-1][1]}]
        recent_results = [oid for job in reversed(self.records('investigation_job')) if job['status'] == 'ingested'
                          for oid in job.get('observation_ids', [])]
        pack = evidence_pack(self.c, self.case_id, self.s.get(self.case_id)['question'], recent_results[:8] + preferred[:10])
        pack['completed_tools'] = [{'request':j['request'],'result_scope':j.get('result_scope',{})} for j in self.records('investigation_job') if j['status'] == 'ingested'][-20:]
        question = (f'현재 조사 영역 {focus or "전체 결과 통합"}를 검토하세요. 각 영역의 구체적 검사 가능한 주장을 작성하세요. '
                    'hypotheses는 해당 영역의 근거·한계를 작성하고 자동 확정하지 마세요. 점검 문자열·정상 작업과 실제 행위를 구분하세요. '
                    '추가 도구를 최대 4개 선택하세요. 이미 실행한 요청은 반복하지 마세요. 설정/시도/실행/성공은 별도 주장입니다.')
        if final_synthesis:
            question += (' 이번 호출은 마지막 결과 통합입니다. tool_calls는 반드시 빈 배열로 두세요. '
                         '가장 최근 실제 검사 결과를 반영하고 이전 설명의 미확인 사항 중 해소된 내용을 정정하세요. '
                         '남은 검사는 remaining_questions에 남기고 완료한 것으로 표현하지 마세요.')
        self.s.update(self.case_id, investigation_stage='로컬 AI · 조사 영역 검토', investigation_round=revision + 1)
        output = self.model(question, pack, run, 'plan')
        if output is None:return {'route':'plan'}
        if final_synthesis:
            # Keep the final model reservation for interpretation of completed work.
            # No tool may run afterward without a remaining synthesis budget.
            output['remaining_questions'] += [f"미실행 추가 제안: {call['tool']} {call.get('path') or call.get('query') or ''}" for call in output['tool_calls']]
            output['tool_calls'] = []
        with self.s.tx():
            plan = self.s.add('investigation_plan', self.case_id, task_id=self.task['id'], run_id=run['id'],
                revision=revision, output=output, valid_ids=[o['id'] for o in pack['observations']], focus=focus, assessed=False)
            self.s.update(run['id'], domain_cursor=min(11, run['domain_cursor'] + 3))
            self.s.add('message', self.case_id, role='assistant', text=output['summary'], mode='ai_candidate', partial=True, automatic=True)
        return {'plan_id': plan['id'], 'route': 'dispatch'}

    def admit(self, state, calls, purpose):
        run = self.run(state)
        for call in calls:
            call = InvestigationTool(**call).model_dump()
            key = digest([VERSION, run['id'], run['signature'], run['source_run'], fingerprint_scope(call)])
            if any(j['fingerprint'] == key for j in self.records('investigation_job')): continue
            budget_key = 'challenge_calls' if purpose == 'challenge' else 'tool_calls'
            maximum = 'max_challenge_calls' if purpose == 'challenge' else 'max_tool_calls'
            run = self.run(state)
            if run[budget_key] >= run[maximum]:
                self.s.update(run['id'], stop_reason=purpose + '_budget_exhausted'); break
            with self.s.tx():
                # The controller serializes job admission and pause under this lock.
                if self.s.get(self.case_id)['status'] != 'running': return
                self.s.add('investigation_job', self.case_id, task_id=self.task['id'], run_id=run['id'],
                    plan_id=state.get('plan_id'), fingerprint=key, request=call, purpose=purpose, status='admitted')
                self.s.update(run['id'], **{budget_key: run[budget_key] + 1})

    def dispatch(self, state):
        plan = self.s.get(state['plan_id'])
        self.admit(state, plan['output']['tool_calls'], 'exploration')
        pending = [j for j in self.records('investigation_job') if j['status'] in ('admitted','submitted')]
        if not pending: return {'route': 'assess', 'job_id': ''}
        job = pending[0]; run = self.run(state)
        if self.s.get(self.case_id)['status'] != 'running': return {'route':'await_jobs' if job['status']=='submitted' else 'dispatch', 'job_id':job['id']}
        body = {'job_key': job['fingerprint'], 'signature': self.e['signature'], 'action': 'investigation_tool', 'path': self.e['path'],
                'investigation': {'evidence_path': self.e['path'], 'run_id': run['source_run'], 'request': job['request']}}
        self.s.update(self.case_id, investigation_stage='추가 조사 · ' + job['request']['tool'])
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
        if reply['status'] in ('queued','running'): return {'route': 'await_jobs'}
        if reply['status'] == 'execution_unknown': raise ExecutionUnknown('작업 실행 여부가 불명확합니다. 이전 실행을 확인할 때까지 재실행하지 않습니다.')
        result = reply.get('result', {'status':'failed','observations':[], 'error':reply.get('error')})
        if reply.get('result') and digest(result) != reply['result_sha256']: raise ValueError('작업 결과 해시 불일치')
        self.s.update(job['id'], status='received', result=result)
        return {'route': 'ingest_validate'}

    def ingest_validate(self, state):
        job = self.s.get(state['job_id'])
        if job['status'] != 'ingested':
            with self.s.tx():
                ids = store_tool_result(self.c, self.case_id, self.e, self.task, job['result'], job['request'])
                self.s.update(job['id'], status='ingested', observation_ids=ids, result_status=job['result']['status'], result_scope={k:v for k,v in job['result'].items() if k!='observations'}, result=None)
        return {'route': 'dispatch'}

    def assess(self, state):
        plan = self.s.get(state['plan_id'])
        if not plan['assessed']:
            valid = set(plan['valid_ids'])
            with self.s.tx():
                for candidate in plan['output']['claims']:
                    if candidate['claim_type'] == 'absence' or not candidate['observation_ids'] or not set(candidate['observation_ids']).issubset(valid): continue
                    if any(c['text'] == candidate['text'] for c in self.records('claim')): continue
                    self.s.add('claim', self.case_id, task_id=self.task['id'], status='candidate', automatic=True,
                               falsification=None, challenge_status='pending', **candidate)
                for assessment in plan['output']['hypotheses']:
                    refs = assessment['supporting_evidence_ids'] + assessment['refuting_evidence_ids']
                    if not set(refs).issubset(valid): continue
                    h = next((h for h in self.s.list('hypothesis', self.case_id) if h.get('evidence_id') == self.e['id'] and h.get('number') == assessment['number']), None)
                    if h:
                        self.s.update(h['id'], status='reviewed_with_gaps', judgment=('확인' if assessment['judgment']=='확정' else assessment['judgment']) if refs else '미확인', ai_suggested_judgment=assessment['judgment'],
                            ai_candidate=True, reasoning=assessment['reasoning'], observation_ids=refs,
                            supporting_evidence_ids=assessment['supporting_evidence_ids'], refuting_evidence_ids=assessment['refuting_evidence_ids'],
                            remaining_checks=assessment['remaining_checks'], judgment_history=h['judgment_history'] +
                            [{'at':now(), 'judgment':'미확인', 'reason':assessment['reasoning'], 'source':'로컬 AI 후보 · 원문 판정 범위 유지'}])
                self.s.update(plan['id'], assessed=True)
        return {}

    def challenge(self, state):
        # Re-read cited originals and search the target path in other collected
        # event sources. These receipts establish what was actually checked;
        # they do NOT automatically validate the model's interpretation.
        run = self.run(state); claims = self.records('claim')[:5]
        calls = []; specified=[]
        for claim in claims:
            if claim.get('challenge_status') != 'pending': continue
            paths = []
            for oid in claim['observation_ids']:
                fields = self.s.get(oid)['fields']
                if fields.get('path', '').startswith('/'): paths.append(fields['path'])
            if paths:
                calls += [{'tool':'read_file','path':paths[0], 'reason':'주장 근거 원문과 점검/설정 문맥 재검사'},
                          {'tool':'search','query':Path(paths[0]).name, 'reason':'별도 실행 기록·정상 운영 문맥 대조'}]
            specified.append((claim,paths))
        self.admit(state, calls, 'challenge')
        for claim,paths in specified:
            self.s.update(claim['id'], challenge_status='specified' if paths else 'unavailable',
                          challenge_limit='원문 대조·기록 검색은 해석의 자동 승인이나 독립 발생원 검증이 아닙니다.')
        jobs=self.records('investigation_job')
        if any(j['status']!='ingested' for j in jobs):return {}
        claim=next((c for c in self.records('claim')[:5] if c.get('challenge_status')=='specified' and not c.get('falsification')),None)
        run=self.run(state)
        if claim and run.get('review_calls',0)<run.get('max_review_calls',5):
            configs=self.s.list('config');config=configs[-1]['provider'] if configs else {}
            preferred=claim['observation_ids']+[oid for j in jobs if j['purpose']=='challenge' for oid in j.get('observation_ids',[])][:20]
            pack=evidence_pack(self.c,self.case_id,claim['text'],preferred)
            pack['observations']=pack['observations'][:16]
            pack['selection_is_partial']=True
            pack['executed_checks']=[{'request':j['request'],'status':j.get('result_status'),'observation_ids':j.get('observation_ids',[])} for j in jobs if j['purpose']=='challenge']
            if not self.c.model_lock.acquire(blocking=False):raise ValueError('로컬 AI 응답 대기')
            try:
                from .runtime_contract import guard
                guard(self.c,self.case_id,self.task)
                self.s.update(run['id'],review_calls=run.get('review_calls',0)+1)
                self.s.update(self.case_id,investigation_stage='원문 대조 후 경쟁 설명 검토')
                try:review,receipt=Provider(config).generate('실제로 실행한 검사와 원문을 바탕으로 다음 주장의 대안 설명·상충 근거·미완료 검사를 검토하세요. 동일 출처 재읽기를 독립 검증으로 부르지 마세요: '+claim['text'],pack,role='falsifier')
                except (httpx.TransportError,ValueError) as ex:
                    self.s.add('receipt',self.case_id,task_id=self.task['id'],evidence_id=self.e['id'],receipt_type='model_error',claim_id=claim['id'],error=str(ex))
                    return {}
                if not set(review['contradicting_observation_ids']).issubset({o['id'] for o in pack['observations']}):raise ValueError('반증 검토가 존재하지 않는 근거를 참조함')
                with self.s.tx():
                    guard(self.c,self.case_id,self.task)
                    self.s.add('receipt',self.case_id,task_id=self.task['id'],evidence_id=self.e['id'],receipt_type='automatic_falsifier',claim_id=claim['id'],**receipt)
                    self.s.update(claim['id'],falsification=review,challenge_status='reviewed_with_limits',
                        actual_check_ids=[j['id'] for j in jobs if j['purpose']=='challenge'],review_type='실제 도구 대조 후 로컬 AI 경쟁 설명 검토 · 독립 해석 검증 아님')
            finally:self.c.model_lock.release()
        return {}

    def stop_gate(self, state):
        run = self.run(state)
        pending = [j for j in self.records('investigation_job') if j['status'] != 'ingested']
        if pending: return {'route':'dispatch'}
        if run.get('review_calls',0)<run.get('max_review_calls',5) and any(c.get('challenge_status')=='specified' and not c.get('falsification') for c in self.records('claim')[:5]):return {'route':'challenge'}
        if run['domain_cursor'] <= 10 and run['model_calls'] < run['max_model_calls']: return {'route':'plan'}
        plan = self.s.get(state['plan_id']) if state.get('plan_id') else None
        if plan and plan['output']['tool_calls'] and not any(j.get('plan_id')==plan['id'] and j['purpose']=='exploration' for j in self.records('investigation_job')):
            # All mandatory domain passes are already processed above. Repeated
            # requests over identical inputs do not justify another model loop.
            self.s.update(run['id'],stop_reason=run['stop_reason'] or 'no_new_check_scope')
            return {'route':'publish'}
        if not run['stop_reason'] and plan and plan['output']['tool_calls'] and run['model_calls'] < run['max_model_calls']:
            # One further synthesis can use completed challenge tool results.
            return {'route':'plan'}
        reason = run['stop_reason'] or ('model_budget_exhausted' if run['model_calls'] >= run['max_model_calls'] else 'available_plan_exhausted')
        self.s.update(run['id'], stop_reason=reason)
        return {'route':'publish'}

    def publish(self, state):
        run = self.run(state)
        old = self.records('investigation_result')
        if old: return {'result_id':old[-1]['id']}
        result = {'status':'partial','complete':False,'observations':[], 'truncated':False, 'tool':VERSION, 'version':'1',
                  'stop_reason':run['stop_reason'], 'model_calls':run['model_calls'], 'tool_calls':run['tool_calls'],
                  'challenge_calls':run['challenge_calls'], 'domains_requested':min(10,run['domain_cursor']-1),
                  'domains_assessed':sum(h.get('status')=='reviewed_with_gaps' for h in self.s.list('hypothesis',self.case_id) if h.get('evidence_id')==self.e['id'] and h.get('contract')=='linux-v1'),
                  'reviews_completed':sum(bool(c.get('falsification')) for c in self.records('claim')[:5]),
                  'reviews_unfinished':sum(not c.get('falsification') for c in self.records('claim')[:5]),
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
