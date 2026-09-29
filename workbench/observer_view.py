"""Pure read-only display contract. No Controller, Store, provider or scheduler.

This projection does not adjudicate claims. Missing semantics remain unknown.
Object revisions identify content, not confidence or event ordering.
"""
from datetime import datetime, timezone
import hashlib
import json

from .evidence_semantics import exact_utc_ns, observation_time
from .temporal import file_anchors

VERSION = 'observer-view-1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def stamp():
    return datetime.now(timezone.utc).isoformat()


def time_assertions(observation):
    """Keep raw clock meaning, candidates and metadata separate from acts."""
    f = observation.get('fields', {})
    saved = f.get('time_record') or {}
    source = observation_time(observation)
    raw = saved.get('raw') or observation.get('timestamp')
    basis = f.get('time_basis') or '시각 근거 미제공'
    values = saved.get('candidates') or f.get('time_candidates')
    interval = saved.get('interval') or f.get('time_interval')
    if values:
        values = [v.get('raw') if isinstance(v, dict) else v for v in values]
        shape = 'candidates'
    elif isinstance(interval, dict):
        values = [interval.get('start'), interval.get('end')]
        shape = 'interval'
    else:
        values, shape = [raw], 'point'
    normalized = [str(exact_utc_ns(v)) if exact_utc_ns(v) is not None else None for v in values]
    explicitly_unknown = saved.get('timezone_known') is False or saved.get('year_known') is False
    explicitly_unknown |= f.get('timezone_known') is False or f.get('year_known') is False
    # A synthetic offset is not a provenance record. Require an explicit basis.
    unknown_basis = any(s in str(basis).lower() for s in ('시간대 미상', '연도 미상', 'unknown timezone', 'unknown year'))
    comparable = bool(values and all(v is not None for v in normalized)
                      and not explicitly_unknown and not unknown_basis
                      and f.get('time_basis'))
    if shape == 'interval' and comparable and int(normalized[0]) > int(normalized[-1]):
        comparable = False
    kind = source.get('time_kind')
    # Parser "occurred" means a record of an act, not independently verified execution.
    meaning = '기록 시각' if kind == 'occurred' else '의미 미분류 시각'
    if kind == 'file_metadata':
        meaning = '파일 시각 · 종류 미제공'
    if kind in ('collected', 'ingested', 'derived', 'observed'):
        comparable = False  # Never mix extraction/analysis clocks into an incident axis.
        meaning = '수집·분석 시각 · 사건 축 제외'
    result = []
    if raw or values != [None]:
        result.append({'id': observation['id'] + ':record-time', 'observation_id': observation['id'],
            'meaning': meaning, 'shape': shape, 'raw_values': values,
            'normalized_ns': normalized if comparable else [], 'comparable': comparable,
            'basis': basis, 'estimated': bool(saved.get('timezone_assumed') or
                any(x in str(basis).lower() for x in ('추정', 'assum', 'infer'))),
            'clock_accuracy': '미검증', 'lane': 'file' if kind == 'file_metadata' else 'record',
            'limitation': '기록상 시각이며 실행 성공·행위자·최초 침입 시각을 입증하지 않습니다.'})
    for anchor in file_anchors(observation):
        result.append({'id': observation['id'] + ':' + anchor['time_type'],
            'observation_id': observation['id'], 'meaning': '파일 ' + anchor['time_type'] + ' · ' + anchor['label'],
            'shape': 'point', 'raw_values': [anchor['raw']], 'normalized_ns': [anchor['epoch_nanoseconds']],
            'comparable': True, 'estimated': False, 'basis': anchor['basis'], 'pointer': anchor['pointer'],
            'clock_accuracy': '미검증', 'lane': 'file',
            'limitation': '파일 메타데이터 값입니다. ctime은 생성·실행 시각이 아니며 시계 오차는 미검증입니다.'})
    if not result:
        result.append({'id': observation['id'] + ':unknown', 'observation_id': observation['id'],
            'meaning': '시각 미상', 'shape': 'unknown', 'raw_values': [], 'normalized_ns': [],
            'comparable': False, 'estimated': False, 'basis': basis, 'lane': 'unknown',
            'clock_accuracy': '미검증', 'limitation': '현재 보존 항목에 배치 가능한 시각이 없습니다.'})
    return result


def project(records, *, case_id, run_id, data_mode, captured_at, ledger_position,
            sequence=1, source_revision=None):
    if data_mode not in ('replay', 'example', 'live') or not run_id or not case_id:
        raise ValueError('An explicit case, run and data mode are required')
    by_kind = {}
    for row in records:
        if row.get('case_id') != case_id:
            raise ValueError('Cross-case record in snapshot')
        by_kind.setdefault(row['kind'], []).append(row)
    cases = by_kind.get('case', [])
    if len(cases) != 1 or cases[0]['id'] != case_id:
        raise ValueError('Exactly one matching case is required')
    case = cases[0]
    if data_mode == 'live' and not case.get('runtime_binding', {}).get('fingerprint'):
        raise ValueError('Live observation requires a frozen runtime binding')
    tasks = {r['id']: r for r in by_kind.get('task', []) if not r.get('superseded')}
    active = {r['id'] for r in by_kind.get('evidence', []) if r.get('connected', True)}

    def scoped(row):
        scope = row.get('scope') if row['kind'] == 'test_intent' else row
        scope = scope or row
        task = tasks.get(scope.get('task_id'))
        if scope.get('task_id') and not task:
            return False
        return ((not scope.get('evidence_id') or scope['evidence_id'] in active)
                and (not task or scope.get('generation', 0) == task.get('retry_generation', 0)))

    entities, aliases = {}, {}

    def add(kind, identity, body, owner=None):
        key = kind + ':' + identity
        obj = {'key': key, 'id': identity, 'type': kind, **body}
        obj['version'] = digest(obj)
        entities[key] = obj
        if owner:
            aliases.setdefault(owner, []).append(key)
        return key

    observations = [r for r in by_kind.get('observation', []) if scoped(r)]
    for row in observations:
        f = row.get('fields', {})
        add('observation', row['id'], {'title': f.get('path') or row.get('type') or '원문 관측',
            'source_type': row.get('type'), 'source_location': row.get('source_location'),
            'excerpt': f.get('excerpt'), 'excerpt_characters': row.get('_excerpt_characters'),
            'excerpt_partial': bool(row.get('_excerpt_partial') or f.get('excerpt_truncated')),
            'source_version': row.get('_source_version') or digest(row),
            'locator': {k: f.get(k) for k in ('source_sha256', 'artifact_path', 'partition_offset',
                'inode', 'byte_offset', 'byte_length', 'source_complete')},
            'record_count': f.get('occurrences'), 'independence': '미평가',
            'limitation': f.get('interpretation_limit') or '원문 존재와 행위 성공·악성 판단은 별개입니다.'}, row['id'])

    def claim(row, finding, identity, *, stage=False, index=None):
        refs = list(dict.fromkeys(finding.get('observation_ids', [])))
        counters = list(dict.fromkeys(finding.get('counterevidence_ids', []) +
                                     finding.get('contradicting_observation_ids', [])))
        admitted = (row['kind'] == 'dossier' and row.get('status') == 'reviewed' or
                    row['kind'] == 'claim' and row.get('status') == 'approved')
        invalid = row.get('status') in ('invalidated', 'retracted', 'superseded') or row.get('superseded')
        missing = [i for i in refs + counters if 'observation:' + i not in entities]
        validity = 'invalidated' if invalid else 'unresolved_references' if missing else 'adopted' if admitted else 'candidate'
        return add('claim', identity, {
            'title': finding.get('statement') or finding.get('text') or finding.get('title') or '표제 미제공',
            'statement': finding.get('statement') or finding.get('text') or finding.get('card_summary') or '',
            'reason': finding.get('reason') or '',
            'claim_kind': finding.get('claim_type') or ('stage_assertion' if stage else 'unclassified'),
            'validity': validity, 'judgment': finding.get('judgment') or '미평가',
            'scope': finding.get('stage') or row.get('scope_note') or '인용된 원문 범위',
            'counterarguments': finding.get('alternatives', []) + (row.get('falsification') or {}).get('alternatives', []),
            'gaps': finding.get('remaining_checks', []) + (row.get('falsification') or {}).get('missing_checks', []),
            'source_ids': refs, 'counter_ids': counters, 'missing_ids': missing,
            'owner_id': row['id'], 'owner_pointer': '/finding/stages/' + str(index) if stage else '/finding' if row['kind'] == 'dossier' else '/text',
            'ledger_revision': row.get('revision') or len(row.get('assessment_history') or []) or None,
            'changed_at': (row.get('assessment_history') or [{}])[-1].get('at') or row.get('created_at'),
            'change_reason': finding.get('change_reason'),
            'review_recency': '보존 원장의 채택 상태 · 독립적인 의미 검증 아님',
            'source_keys': ['observation:' + i for i in refs + counters if 'observation:' + i in entities],
        }, row['id'])

    for row in by_kind.get('claim', []):
        if scoped(row):
            claim(row, row, row['id'])
    for row in by_kind.get('dossier', []):
        if not scoped(row) or not row.get('finding'):
            continue
        keys = [claim(row, row['finding'], row['id'])]
        for i, stage in enumerate(row['finding'].get('stages', [])):
            scoped_stage = {**stage, 'alternatives': row['finding'].get('alternatives', []),
                            'remaining_checks': row['finding'].get('remaining_checks', [])}
            keys.append(claim(row, scoped_stage, row['id'] + ':stage:' + str(i), stage=True, index=i))
        if row.get('source_claim_id'):
            aliases.setdefault(row['source_claim_id'], []).extend(keys)

    jobs = [j for j in by_kind.get('investigation_job', []) if scoped(j)]
    jobs_by_test = {}
    for j in jobs:
        for tid in j.get('test_intent_ids', []):
            jobs_by_test.setdefault(tid, []).append(j)
    for t in by_kind.get('test_intent', []):
        if not scoped(t):
            continue
        linked = jobs_by_test.get(t['id'], [])
        result_job = (t.get('result_scope') or {}).get('job_id')
        linked += [j for j in jobs if j['id'] == result_job and j not in linked]
        admission = t.get('admission') or {}
        raw_status = t.get('status', 'unknown')
        latest = linked[-1] if linked else {}
        if admission.get('eligible') is False or raw_status == 'blocked':
            execution = 'blocked'
        elif latest.get('status') in ('ingested', 'received'):
            execution = {'covered': 'succeeded', 'complete': 'succeeded', 'failed': 'failed'}.get(latest.get('result_status'), 'partial')
        elif latest.get('worker_status') == 'running':
            execution = 'running'
        elif latest.get('status') == 'submitted':
            execution = 'queued'
        else:
            execution = 'candidate'  # admission eligibility alone is not scheduling.
        assessment = t.get('assessment') or {}
        add('test', t['id'], {'title': t.get('tool') or (t.get('scope') or {}).get('request', {}).get('tool') or '검사',
            'execution': execution, 'admission': admission.get('eligible'),
            'execution_reason': admission.get('reason') or latest.get('error'),
            'discrimination': assessment.get('outcome') or assessment.get('result') or 'unassessed',
            'assessment_status': t.get('assessment_status', 'unassessed'),
            'assessment': assessment, 'conditions': t.get('conditions') or {},
            'request': (t.get('scope') or {}).get('request', {}),
            'question_id': t.get('question_id'), 'job_ids': [j['id'] for j in linked],
            'result_scope': {k: (t.get('result_scope') or {}).get(k) for k in ('complete', 'status', 'truncated', 'error')},
            'source_keys': ['observation:' + i for i in (t.get('result_scope') or {}).get('observation_ids', []) if 'observation:' + i in entities],
            'design': (t.get('scope') or {}).get('test_design') or {},
        }, t['id'])

    for h in by_kind.get('hypothesis', []):
        if not scoped(h) or h.get('hypothesis_kind') != 'dynamic':
            continue
        add('hypothesis', h['id'], {'title': h.get('title') or h.get('text'),
            'statement': h.get('card_summary'), 'judgment': h.get('judgment'),
            'scope': (h.get('scenario_assessment') or {}).get('comparison_question') or '비교 질문 미제공',
            'counterarguments': [(h.get('scenario_assessment') or {}).get('alternative_explanation', '')],
            'gaps': h.get('remaining_checks', []), 'last_change': h.get('lifecycle'),
            'validity': 'historical_assessment',
            'review_recency': '원장 해석 · 의존성 최신성은 이 재생 화면에서 별도 입증하지 않음',
            'source_keys': ['observation:' + i for i in h.get('observation_ids', []) if 'observation:' + i in entities],
            'ledger_revision': h.get('revision'),
        }, h['id'])

    decisions = {r['id']: r for r in by_kind.get('decision_revision', [])}
    for q in by_kind.get('case_question', []):
        if not scoped(q):
            continue
        decision = decisions.get(q.get('decision_id'), {})
        keys = [key for sid in q.get('source_ids', []) for key in aliases.get(sid, [])]
        tests = [e['key'] for e in entities.values() if e['type'] == 'test' and e['question_id'] == q['id']]
        add('question', q['id'], {'title': q.get('question') or '질문 문구 미제공',
            'state': q.get('state') or q.get('status') or 'unknown',
            'answer': decision.get('reason') or '현재 범위의 답이 아직 기록되지 않았습니다.',
            'decision_id': decision.get('id'), 'decision_version': digest(decision) if decision else None,
            'claim_keys': [key for key in keys if entities[key]['type'] == 'claim'],
            'hypothesis_keys': [key for key in keys if entities[key]['type'] == 'hypothesis'],
            'test_keys': tests,
            'source_keys': ['observation:' + i for i in q.get('observation_ids', []) if 'observation:' + i in entities],
            'next_test_state': 'recorded' if any(entities[k]['execution'] in ('candidate', 'queued', 'running', 'blocked') for k in tests) else 'not_selected',
            'unlinked_source_ids': [sid for sid in q.get('source_ids', []) if sid not in aliases],
        }, q['id'])

    receipts = by_kind.get('receipt', [])
    by_reservation = {r.get('reservation_id'): r for r in receipts if r.get('reservation_id')}
    by_input = {r.get('input_record_id'): r for r in receipts if r.get('input_record_id')}
    activities = []
    for j in jobs:
        terminal = j.get('status') in ('received', 'ingested')
        state = (j.get('result_status') or 'unknown') if terminal else 'running' if j.get('worker_status') == 'running' else 'waiting'
        activities.append({'id': j['id'], 'kind': 'tool', 'title': (j.get('request') or {}).get('tool') or '도구',
            'state': state, 'target': (j.get('request') or {}).get('path') or (j.get('request') or {}).get('query') or '범위는 검사 상세 참조',
            'at': j.get('ended_at') or j.get('dispatched_at') or j.get('created_at'), 'error': j.get('error'),
            'task_id': j.get('task_id'), 'result_adopted': j.get('status') == 'ingested'})
    for r in by_kind.get('model_reservation', []) + by_kind.get('review_input', []) + by_kind.get('synthesis_input', []):
        if not scoped(r):
            continue
        receipt = by_reservation.get(r['id']) or by_input.get(r['id'])
        error = (receipt or {}).get('error') or r.get('error')
        received = bool(receipt) or r.get('status') == 'received'
        model_state = 'failed' if error or r.get('status') == 'failed' else 'received' if received else 'input_registered'
        activities.append({'id': r['id'], 'kind': 'model',
            'title': '모델 응답 수신 · 채택과 별개' if received and not error else '모델 요청 실패' if model_state == 'failed' else '모델 입력 등록 · 실제 요청·응답 상태 미제공',
            'state': model_state,
            'target': r.get('activity_target') or r.get('purpose') or '구조화 판단',
            'at': (receipt or {}).get('created_at') or r.get('created_at'), 'error': error,
            'task_id': r.get('task_id'), 'result_adopted': None})
    for t in tasks.values():
        if t.get('status') == 'running' and not any(a['task_id'] == t['id'] and a['state'] in ('running', 'waiting') for a in activities):
            activities.append({'id': t['id'], 'kind': 'task', 'title': t.get('action') or '조사 단계',
                'state': 'running', 'target': '단계 실행 중 · 세부 실행 상태 미제공',
                'at': t.get('started_at') or t.get('created_at'), 'error': t.get('error'), 'task_id': t['id'], 'result_adopted': None})
    if case.get('status') not in ('running', 'pause_requested'):
        for a in activities:
            if a['state'] in ('running', 'waiting', 'input_registered'):
                a['state'] = 'interrupted'
    activities.sort(key=lambda a: (a.get('at') or '', a['id']), reverse=True)

    relations = []
    for e in list(entities.values()):
        for key in e.get('source_keys', []):
            relations.append({'from': e['key'], 'to': key, 'kind': 'cites',
                              'independence': 'unknown', 'causality': 'not_asserted'})
    for use in by_kind.get('test_result_use', []):
        if 'test:' + str(use.get('test_intent_id')) in entities:
            for oid in use.get('original_observation_ids', []):
                if 'observation:' + oid in entities:
                    relations.append({'from': 'test:' + use['test_intent_id'], 'to': 'observation:' + oid,
                        'kind': 'reuses_physical_result', 'new_execution': use.get('new_execution'),
                        'independent_evidence': use.get('independent_evidence'), 'job_id': use.get('job_id')})

    # Resolve every displayed reference to an exact version in the same envelope.
    for e in entities.values():
        keys = e.get('source_keys', []) + e.get('claim_keys', []) + e.get('hypothesis_keys', []) + e.get('test_keys', [])
        e['refs'] = [{'key': key, 'version': entities[key]['version']} for key in dict.fromkeys(keys)]
        e['version'] = digest({k: v for k, v in e.items() if k != 'version'})
    times = []
    for observation in observations:
        for t in time_assertions(observation):
            key = 'observation:' + observation['id']
            times.append({**t, 'source_ref': {'key': key, 'version': entities[key]['version']},
                          'claim_refs': [{'key': e['key'], 'version': e['version']} for e in entities.values()
                                         if e['type'] == 'claim' and key in e.get('source_keys', [])]})
    times.sort(key=lambda t: (not t['comparable'], min(map(int, t['normalized_ns'])) if t['comparable'] else 0, t['id']))
    adopted = [e for e in entities.values() if e['type'] == 'claim' and e['validity'] == 'adopted']
    # One ledger stage, not three repeated summary/interpretation bubbles.
    representative = next((e for e in adopted if e['claim_kind'] == 'stage_assertion'), next(iter(adopted), None))
    narrative = [{'kind': 'adopted_claim', 'text': e['statement'] or e['title'],
                  'limitations': e['gaps'], 'refs': [{'key': e['key'], 'version': e['version']}],
                  'dedupe_key': e['key'], 'validity': e['validity']} for e in [representative] if e]
    if not narrative:
        narrative = [{'kind': 'status', 'text': '보존 시점에 채택된 주장이 없습니다. 수집량은 판단 완료율이 아닙니다.',
                      'limitations': [], 'refs': [], 'dedupe_key': 'no-adopted-claims', 'validity': 'status_only'}]
    reports = []
    for r in by_kind.get('report', []):
        snap = r.get('snapshot') or {}
        comparable_revision = source_revision is not None and snap.get('scope_revision') is not None
        reports.append({'id': r['id'], 'report_id': r.get('report_id'), 'generated_at': r.get('created_at'),
            'snapshot': snap, 'formats': r.get('distributed_files') or {},
            'freshness': 'same_ledger_scope' if comparable_revision and snap['scope_revision'] == source_revision else 'older_scope' if comparable_revision else 'unknown',
            'claim_versions': None, 'claim_version_limitation': '기존 보고서의 객체별 버전 목록은 미제공입니다.',
            'status': 'generated', 'correction_impact': 'possible' if comparable_revision and snap['scope_revision'] != source_revision else 'unknown'})
    body = {'case': {'id': case_id, 'name': case.get('name'), 'status': case.get('status')},
            'objects': entities, 'relations': relations, 'timeline': times, 'narrative': narrative,
            'activity': {'checked_at': captured_at, 'items': activities, 'eta': None,
                         'eta_reason': '원장에 비교 가능한 작업량·소요시간 기준이 제공되지 않아 산정할 수 없습니다.'},
            'reports': reports,
            'summary': {'adopted_claims': len(adopted), 'questions': sum(e['type'] == 'question' for e in entities.values()),
                'unassessed_tests': sum(e['type'] == 'test' and e['assessment_status'] == 'unassessed' for e in entities.values()),
                'missing_reference_claims': sum(e['type'] == 'claim' and e['validity'] == 'unresolved_references' for e in entities.values()),
                'last_meaningful_change': max((e.get('changed_at') or '' for e in adopted), default=None) or None,
                'meaningful_change_scope': '채택 시점만 집계. 의미 진전·침해 규명률은 미평가.'}}
    envelope = {'schema_version': VERSION, 'case_id': case_id, 'run_id': run_id, 'data_mode': data_mode,
                'sequence': sequence, 'projection_revision': digest(body), 'ledger_position': ledger_position,
                'captured_at': captured_at, 'generated_at': stamp(), 'freshness': 'retained_snapshot',
                'source_revision': source_revision, 'source_binding': case.get('runtime_binding', {}).get('fingerprint')}
    return {'envelope': envelope, **body}


def validate(view):
    env = view['envelope']
    if env.get('schema_version') != VERSION or env.get('data_mode') not in ('replay', 'example', 'live'):
        raise ValueError('Unsupported view contract')
    if env.get('data_mode') == 'live' and not env.get('source_binding'):
        raise ValueError('Live source binding missing')
    if env['case_id'] != view['case']['id'] or not env['run_id'] or not isinstance(env['sequence'], int) or env['sequence'] < 1:
        raise ValueError('Invalid identity/sequence')
    body = {k: v for k, v in view.items() if k != 'envelope'}
    if digest(body) != env['projection_revision']:
        raise ValueError('Projection hash mismatch')
    objects = view['objects']
    for obj in objects.values():
        if digest({k: v for k, v in obj.items() if k != 'version'}) != obj['version']:
            raise ValueError('Object hash mismatch')
        for ref in obj['refs']:
            if ref['key'] not in objects or objects[ref['key']]['version'] != ref['version']:
                raise ValueError('Incompatible object reference')
    for narrative in view['narrative']:
        for ref in narrative['refs']:
            if objects.get(ref['key'], {}).get('version') != ref['version']:
                raise ValueError('Incompatible narrative reference')
    for time in view['timeline']:
        for ref in [time['source_ref']] + time['claim_refs']:
            if objects.get(ref['key'], {}).get('version') != ref['version']:
                raise ValueError('Incompatible time reference')
    return view
