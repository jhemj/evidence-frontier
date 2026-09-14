"""Automatic three-level judgments, separate from immutable source observations."""
import httpx
import re
from .investigation import evidence_pack
from .provider import Provider

LEVELS = ('확인', '유력', '미확인')


def bound_absence(finding):
    """Missing execution records cannot establish that execution never happened."""
    # Match the asserted title only: a reason may quote or explicitly reject this inference.
    if finding['judgment'] == '확인' and re.search(r'실행(?:되지 않았|된 적이 없|되지 않음| 부재)', finding['title']):
        finding = {**finding, 'judgment':'미확인', 'title':'실제 실행 여부 미확인',
            'reason':'선택한 원문에서 실행을 뒷받침하는 기록을 찾지 못했습니다. 기록의 부재만으로 실행되지 않았다고 판단할 수 없습니다.',
            'scope_correction':'원래 AI 응답은 모델 영수증에 보존. 기록 부재와 행위 부재를 구분하여 판정 범위를 제한함.'}
    return finding


def current(controller, case_id, source_observations=None):
    records = controller.store.list('judgment', case_id)
    if not records: return []
    active = controller.active_ids(case_id)
    observations = {o['id'] for o in (source_observations if source_observations is not None else controller.active_observations(case_id)) if o['evidence_id'] in active}
    latest = {}
    for record in records:
        if record['evidence_id'] not in active: continue
        if any(not set(f['observation_ids']).issubset(observations) for f in record['findings']): continue
        latest[record['evidence_id']] = record
    return list(latest.values())


def finish(controller, case_id, evidence, task):
    if task.get('analysis_version')=='linux-hunt-2':
        from .dossiers import finish as review_dossiers
        return review_dossiers(controller,case_id,evidence,task)
    store = controller.store
    generation = task.get('retry_generation', 0)
    existing = [r for r in store.list('judgment', case_id) if r['task_id'] == task['id'] and r.get('generation',0)==generation]
    if existing: return existing[-1]['result']
    attempts = [r for r in store.list('judgment_attempt', case_id) if r['task_id'] == task['id'] and r.get('generation',0)==generation]
    if len(attempts) >= 2:
        return {'status':'failed', 'complete':False, 'observations':[], 'error':'AI 판단을 완료하지 못했습니다. 기존 원문과 조사 기록은 보존되어 있습니다.'}
    config = store.list('config')
    if not config or not config[-1]['provider'].get('model'):
        raise ValueError('최종 판단에 사용할 로컬 모델을 연결하세요.')
    if not controller.model_lock.acquire(blocking=False): return None
    try:
        preferred = [oid for job in reversed(store.list('investigation_job', case_id))
                     if job.get('status') == 'ingested' for oid in job.get('observation_ids', [])][:12]
        pack = evidence_pack(controller, case_id, store.get(case_id)['question'], preferred)
        valid = {o['id'] for o in controller.active_observations(case_id) if o['evidence_id'] == evidence['id']}
        pack['observations'] = [o for o in pack['observations'] if o['id'] in valid]
        included = {o['id'] for o in pack['observations']}
        pack['prior_interpretations'] = [{'text': c['text'], 'observation_ids': c['observation_ids'],
            'uncertainty': c.get('uncertainty'), 'falsification': c.get('falsification')}
            for c in store.list('claim', case_id)[-12:] if set(c['observation_ids']).issubset(included)]
        pack['coverage'] = [{'action': c['action'], 'status': c['status'], 'limitation': c.get('error')}
            for c in store.list('coverage', case_id) if c['evidence_id'] == evidence['id'] and c['action'] not in ('ai_judgment','investigation_report')]
        store.add('judgment_attempt', case_id, task_id=task['id'], evidence_id=evidence['id'], generation=generation, included_ids=sorted(included))
        store.update(case_id, investigation_stage='AI가 확인·유력·미확인으로 판단 중')
        try:
            output, receipt = Provider(config[-1]['provider']).generate(
                '수집된 실제 근거와 대안 검토를 종합하여 최종 판단하세요. 확인·유력·미확인별 핵심 결과와 이유를 작성하세요. '
                '사람의 승인 없이 결과를 제공하며 유력한 판단도 포함합니다. 확인은 구체적인 사실의 범위에 한정하세요.', pack, role='judgment')
            for finding in output['findings']:
                if not set(finding['observation_ids']).issubset(included): raise ValueError('AI 판단이 제공하지 않은 근거를 참조했습니다.')
                if finding['judgment'] != '미확인' and not finding['observation_ids']: raise ValueError('확인·유력 판단에는 실제 근거가 필요합니다.')
            output = {**output, 'findings':[bound_absence(f) for f in output['findings']]}
        except (ValueError, httpx.TransportError) as ex:
            store.add('receipt', case_id, task_id=task['id'], evidence_id=evidence['id'], receipt_type='judgment_error', error=str(ex))
            return None
        result = {'status':'covered', 'complete':True, 'observations':[], 'tool':'local-ai-judgment-v1',
                  'judgment_counts':{level:sum(f['judgment']==level for f in output['findings']) for level in LEVELS},
                  'scope':'AI 결과 정리 완료. 개별 판단의 확실성과 수집 한계는 별도로 표시.'}
        with store.tx():
            receipt = store.add('receipt', case_id, task_id=task['id'], evidence_id=evidence['id'], receipt_type='final_judgment_model', **receipt)
            record = store.add('judgment', case_id, task_id=task['id'], evidence_id=evidence['id'], receipt_id=receipt['id'],
                source_result_revision=store.get(case_id).get('result_revision'), generation=generation, included_ids=sorted(included),
                selection_is_partial=pack['selection_is_partial'], result=result, **output)
            store.update(case_id, result_revision=record['id'], investigation_result='AI 판단 완료 · 확인 / 유력 / 미확인', investigation_stage='결과 패키지 생성')
        return result
    finally:
        controller.model_lock.release()
