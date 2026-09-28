"""Source-bound post-pivot searches feeding the existing review/check ledger.

This is a bounded lead queue, not an IOC verdict or an independent source.
Unadmitted candidates remain reproducible in the exported inventory.
"""
from .semantic_contract import digest
from .review_contracts import contract, review_exhausted, tools_exhausted
from .retrieval import fingerprint_scope

VERSION = 'discovery-follow-through-1'
MAX_ADMITTED = 6


def inventory(observations, evidence):
    sources = sorted((o['id'], o['fields']) for o in observations if o['type'] in ('linux_environment', 'windows_environment'))
    revision = digest([VERSION, evidence['id'], evidence.get('signature'), sources])
    candidates = {}
    sequence=lambda value:value if isinstance(value,list) else []
    for o in observations:
        f = o['fields']; values = []
        if o['type'] == 'linux_detection':
            values.append(f.get('path'))
        if o['type'] == 'windows_task':
            values.extend(a.get('Command') for a in sequence(f.get('actions')) if isinstance(a, dict))
        for ref in sequence(f.get('referenced_paths')):
            values.append(ref.get('absolute') if isinstance(ref, dict) else ref)
        for indicator in sequence(f.get('indicators')):
            if isinstance(indicator, dict):
                values.append(indicator.get('value'))
        for value in values:
            if not isinstance(value, str) or not 4 <= len(value) <= 200 or any(ord(c) < 32 for c in value):
                continue
            key = digest([revision, value])
            priority=0 if o['type'] in ('linux_detection','windows_task') else 1
            row = candidates.setdefault(key, {'key': key, 'query': value, 'source_revision': revision, 'observation_ids': [],'priority':priority})
            row['priority']=min(row['priority'],priority)
            if o['id'] not in row['observation_ids']:
                row['observation_ids'].append(o['id'])
    for row in candidates.values():
        row['observation_ids'].sort()
        row['key']=digest([row['key'],row['observation_ids']])
    return sorted(candidates.values(), key=lambda r: (r['priority'],r['key']))


def current(store, cid, task):
    return [r for r in store.list('discovery_lead', cid) if r['task_id'] == task['id']
            and r.get('generation', 0) == task.get('retry_generation', 0)]


def admit(controller, cid, evidence, task):
    """One bounded tranche after a review; no separate worker execution path."""
    if task.get('review_policy') != 'autonomous-v1' or task.get('repair_generation') == task.get('retry_generation', 0):
        return False
    store = controller.store
    observations = [o for o in controller.active_observations(cid) if o['evidence_id'] == evidence['id']]
    candidates = inventory(observations, evidence)
    previous = current(store, cid, task)
    done = {r['key'] for r in previous}
    pending = [c for c in candidates if c['key'] not in done]
    if not pending or len(previous) >= MAX_ADMITTED or review_exhausted(store, cid, task, 6):
        return False
    jobs = [j for j in store.list('investigation_job', cid) if j['task_id'] == task['id']]
    active_jobs = [j for j in jobs if j.get('generation', 0) == task.get('retry_generation', 0)]
    if tools_exhausted(len(jobs), len(active_jobs), False):
        return False
    run = next((o['fields'].get('run_id') for o in reversed(observations) if o['type'] in ('linux_environment', 'windows_environment')), None)
    if not run:
        return False
    row = pending[0]
    # Kept small enough to ensure every origin and returned record fits one unit.
    refs = row['observation_ids'][:4]
    with store.tx():
        d = store.add('dossier', cid, task_id=task['id'], evidence_id=evidence['id'], generation=task.get('retry_generation', 0),
            title='새 단서의 다른 원문 대조 · ' + row['query'][:140], baseline=False,
            observation_ids=refs, all_observation_ids=row['observation_ids'], total_records=len(row['observation_ids']),
            group_key=row['key'], review_priority=2, review_family='discovery', status='pending', finding=None,
            discovery_key=row['key'], origin_scope_partial=len(refs) < len(row['observation_ids']))
        call = {'tool': 'search', 'query': row['query'], 'limit': 8, 'hypothesis_id': d['id'],
                'reason': '새 원문 단서를 보존된 다른 자료에서 재검색; 동일 발생원 여부와 정상 대안도 확인',
                'success_condition': '정확한 값이 기록된 문맥과 출처를 대조하여 좁은 관계만 확인',
                'refutation_condition': '직접적인 반대 기록이 해당 관계 가설을 반박; 단순 0건은 반증 아님',
                'inconclusive_condition': '원문 부재, 부분 검색, 같은 출처 반복, 식별 불일치 또는 실행/성공 근거 부족'}
        # Fingerprint encoding is shared with dossiers.digest (default separators).
        from .dossiers import digest as job_digest
        fp = job_digest([task['id'], task.get('retry_generation', 0), run, evidence['signature'], fingerprint_scope(call)])
        old = next((j for j in active_jobs if j['fingerprint'] == fp), None)
        if old:
            from .review_contracts import attach
            job = store.update(old['id'], contracts=attach(old, call),
                               dossier_ids=list(dict.fromkeys(old.get('dossier_ids', []) + [d['id']])))
        else:
            job = store.add('investigation_job', cid, task_id=task['id'], evidence_id=evidence['id'], generation=task.get('retry_generation', 0),
                fingerprint=fp, request=call, contracts=[contract(call)], dossier_ids=[d['id']],
                purpose='discovery_revisit', source_run=run, status='admitted', review_family='discovery')
        store.add('dossier_batch', cid, task_id=task['id'], evidence_id=evidence['id'], generation=task.get('retry_generation', 0),
                  dossier_ids=[d['id']], status='await_checks', round=-1, attempts=0, output=None,
                  job_ids=[job['id']], deferred_checks=[], discovery_key=row['key'])
        store.add('discovery_lead', cid, task_id=task['id'], evidence_id=evidence['id'], generation=task.get('retry_generation', 0),
                  **row, dossier_id=d['id'], job_id=job['id'], origin_ids_presented=refs)
    return True


def project(document, leads):
    """No pending test disappears when a model refutes/omits its parent finding."""
    dossiers = {d['id']: d for d in document.get('dossiers', [])}
    checks = document.get('check_ledger', {}).get('checks', [])
    rows = []
    for d in dossiers.values():
        if d.get('baseline'):
            continue
        relevant = [(c, x) for c in checks for x in c['contracts'] if x['dossier_id'] == d['id']]
        open_checks = [(c, x) for c, x in relevant if not c['job_ids'] or x['evaluation_status'] != 'assessed']
        f = d.get('finding') or {}
        state = 'disposed' if d['status'] == 'reviewed' and not open_checks else 'investigating' if relevant else 'open'
        rows.append({'dossier_id': d['id'], 'title': d.get('title',f.get('title','단서')), 'state': state,
                     'disposition': ('excluded' if f.get('timeline_role') == '반증됨' else 'included') if state == 'disposed' else 'pending',
                     'reason': f.get('reason') or d.get('error', ''),
                     'next_tests': [{'scope_key': c['scope_key'], 'request': c['request'], 'execution_status': c['execution_status'],
                                     'evaluation_status': x['evaluation_status']} for c, x in open_checks],
                     'remaining_checks': f.get('remaining_checks', [])})
    candidates = []
    for evidence in document.get('evidence', []):
        candidates.extend(inventory([o for o in document['observations'] if o['evidence_id'] == evidence['id']], evidence))
    admitted = {r['key']: r for r in leads}
    backlog = [{**c, 'state': 'admitted' if c['key'] in admitted else 'deferred',
                'reason': '' if c['key'] in admitted else 'Bounded discovery budget or source not yet available; not examined'} for c in candidates]
    return {'version': VERSION, 'leads': rows, 'discovery_inventory': backlog, 'discovery_receipts': leads,
            'open_leads': sum(r['state'] != 'disposed' for r in rows),
            'deferred_discovery': sum(r['state'] == 'deferred' for r in backlog),
            'scope': '단서·후속 검사 처리 상태. 다른 원문의 일치는 독립 증거·악성·동일 행위자 확정이 아님.'}
