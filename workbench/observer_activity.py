"""Describe recorded work, not a second AI judgment or a new investigation plan."""
import hashlib
import json
import re


def text(value, limit=180):
    if not isinstance(value, str):
        return ''
    value = re.sub(r'[\x00-\x20]+', ' ', value).strip()
    return value[:limit] + ('…' if len(value) > limit else '')


def input_context(pack):
    """Bounded descriptions from the EXACT saved input, never today's dossier title.

    Snapshot capture supplies only these fields, not the full prompt or result.
    The examples describe what is being reviewed; they are not adopted facts.
    """
    pack = pack if isinstance(pack, dict) else {}
    required = pack.get('required_dossiers') or []
    observations = pack.get('observations') or []
    sources = {o['id']: o for o in observations if isinstance(o, dict) and isinstance(o.get('id'), str)}
    subjects = []
    for d in required[:8]:
        if not isinstance(d, dict) or not isinstance(d.get('id'), str):
            continue
        ids = list(dict.fromkeys(i for i in d.get('observation_ids', []) if isinstance(i, str)))
        shown = [sources[i] for i in ids if i in sources]
        examples = []
        for o in shown[:2]:
            f = o.get('fields') or {}
            command, path = text(f.get('command'), 160), text(f.get('path'), 180)
            excerpt = text(f.get('excerpt'), 160)
            examples.append({'observation_id': o['id'], 'path': path,
                'label': '명령 기록: ' + command if command else '기록 구간: ' + excerpt if excerpt else path or '기록 내용 미제공',
                'field': 'command' if command else 'excerpt' if excerpt else 'path',
                'partial': bool(f.get('_display_partial') or any(v.endswith('…') for v in (command, path, excerpt)))})
        subjects.append({'id': d['id'], 'title': text(d.get('title'), 220), 'examples': examples,
            'presented_records': len(shown), 'unavailable_records': len(ids) - len(shown),
            'omitted_examples': max(0, len(shown) - len(examples))})
    return {'subjects': subjects, 'subject_count': len(required),
        'omitted_subject_count': max(0, len(required) - len(subjects)),
        'review_mode': text(pack.get('review_mode'), 60)}


def model_context(record, dossiers):
    context = record.get('_activity_context') or input_context(record.get('pack'))
    subjects = []
    for subject in context['subjects']:
        d = dossiers.get(subject['id'])
        # Never attach another task/evidence/generation just because its title fits.
        if not d or d.get('task_id') != record.get('task_id') or d.get('generation', 0) != record.get('generation', 0):
            continue
        if record.get('evidence_id') and d.get('evidence_id') != record['evidence_id']:
            continue
        subjects.append(subject)
    return {**context, 'subjects': subjects,
        'omitted_subject_count': max(0, context['subject_count'] - len(subjects)),
        'input_id': record['id'], 'input_sha256': record.get('input_sha256'),
        'basis': 'saved_input', 'adopted_fact': False}


def failure_details(record, receipt=None):
    receipt = receipt or {}
    errors = receipt.get('validation_errors') or []
    codes = list(dict.fromkeys(e.get('code') for e in errors if isinstance(e, dict) and e.get('code')))
    category = receipt.get('failure_category') or record.get('failure_category')
    if codes or category in ('output_contract', 'citation_scope'):
        reasons = {
            'source_literal_mismatch': '답변의 인용·표현을 원문에서 확인하지 못했어요',
            'evidence_span_citation_scope': '인용한 원문 구간과 검토 범위가 맞지 않았어요',
            'dossier_focus_mismatch': '답변이 이번 단서의 검토 범위를 벗어났어요',
            'unknown_check_dossier': '후속 검사를 단서에 연결해야 하는데 다른 종류의 ID가 사용됐어요',
            'dossier_membership': '검토 대상 단서가 빠지거나 중복되거나 요청 범위와 달랐어요',
            'unknown_observation': '검토 답변이 이번 요청에 없는 원문 기록을 참조했어요',
            'invalid_check_assessment': '검사 결과의 연결 대상이 맞지 않거나 중복됐어요',
            'check_citation_scope': '인용한 기록이 해당 검사의 반환 범위를 벗어났어요',
        }
        # Public phrases are templates for known validation codes, not inferred diagnoses.
        literal = any(c in ('source_literal_mismatch', 'evidence_span_citation_scope') for c in codes)
        return {'phase': 'validation', 'title': '검토 답변의 원문 확인 실패' if literal else '검토 답변의 형식·근거 검증 실패',
            'reason': reasons.get(codes[0] if codes else None, '검토 답변이 근거·형식 검증을 통과하지 못했어요'),
            'impact': '검증되지 않은 부분은 판단에 반영할 수 없어요. 다른 부분이 별도로 채택됐는지는 해당 단서의 검토 결과를 확인해야 해요.',
            'subject_ids': list(dict.fromkeys(e.get('dossier_id') for e in errors if isinstance(e, dict) and e.get('dossier_id'))),
            'codes': codes}
    if category == 'input_projection':
        return {'phase': 'input', 'title': '검토 입력 준비 실패',
            'reason': '검토할 원문을 입력 범위에 맞게 준비하지 못했어요',
            'impact': '이번 입력으로는 판단을 갱신하지 못했어요. 실패 자체가 단서의 반박은 아니에요.', 'codes': codes}
    if record.get('kind') == 'investigation_job':
        failure = (record.get('result_scope') or {}).get('failure') or {}
        if failure.get('code') == 'path_not_resolved':
            return {'phase': 'tool', 'title': '파일 경로를 찾지 못했어요',
                'reason': '선택한 이미지와 파일시스템 범위에서 요청한 경로를 확인하지 못했어요',
                'impact': '이번 시도에서 내용을 읽지 못했어요. 과거에도 파일이 없었거나 삭제됐다는 뜻은 아니에요.',
                'codes': ['path_not_resolved']}
        return {'phase': 'tool', 'title': '자료 확인 작업 실패', 'reason': '요청한 범위의 자료 확인을 완료하지 못했어요',
            'impact': '이 검사에서 답을 얻지 못했어요. 기록이 없다는 뜻으로 판단하지 않아요.', 'codes': codes}
    return {'phase': 'request', 'title': 'AI 검토 요청 실패',
        'reason': '이 요청은 정상적인 검토 결과를 받지 못했어요. 상세 원인은 작업 기록에서 확인할 수 있어요',
        'impact': '이 요청의 새 판단은 제공되지 않았어요. 다른 작업의 진행 여부와는 구분해요.', 'codes': codes}


def tool_result_summary(job):
    """Typed retained tool-result metadata only; never raw output or meaning.

    Search counts describe ONE returned scan page, not independent evidence,
    total matches, or incident absence. Missing fields remain None, not zero.
    """
    scope = job.get('result_scope')
    request = job.get('request') or {}
    status = job.get('result_status')
    terminal = job.get('status') in ('received', 'ingested', 'failed', 'rejected')
    known = status in ('covered', 'covered_zero', 'partial', 'failed', 'unsupported')
    result = {'basis': 'retained_tool_result_scope', 'status': status if terminal and known else 'unknown',
        'complete': None, 'truncated': None, 'matches': None, 'returned': None,
        'matches_scope': None, 'omitted_matches': None, 'has_more': None,
        'remaining_matches_unknown': None, 'omitted_records': None,
        'unknown_time_excluded': None,
        'failure_code': None, 'retryable': None, 'metadata_available': False,
        'interpretation': 'tool_result_only_not_hypothesis_judgment'}
    if (not terminal or not known or not isinstance(scope, dict)
            or scope.get('status') != status
            or scope.get('tool') not in (None, request.get('tool'))):
        return result
    complete = scope.get('complete')
    if (type(complete) is not bool or
            complete != (status in ('covered', 'covered_zero'))):
        result['status'] = 'unknown'
        return result
    result.update(metadata_available=True, complete=complete)
    result['truncated'] = scope.get('truncated') if type(scope.get('truncated')) is bool else None

    def count(key):
        value = scope.get(key)
        return value if type(value) is int and value >= 0 else None

    result['omitted_records'] = count('omitted_records')
    if request.get('tool') == 'search' and scope.get('tool') == 'search':
        # This is the retained retrieval producer's explicit page-count basis.
        if scope.get('matches_scope') == 'this page scan only; not total matches':
            matches, returned, omitted = count('matches'), count('returned'), count('omitted_matches')
            if (matches is None or returned is None or matches >= returned) and (
                    matches is None or returned is None or omitted is None or omitted == matches - returned):
                result.update(matches=matches, returned=returned,
                    omitted_matches=omitted, matches_scope='this_search_page')
            result['unknown_time_excluded'] = count('unknown_time_excluded')
        for key in ('has_more', 'remaining_matches_unknown'):
            result[key] = scope.get(key) if type(scope.get(key)) is bool else None
    failure = scope.get('failure') or {}
    if (status == 'failed' and isinstance(failure, dict)
            and failure.get('code') in ('path_not_resolved', 'transient_read_error')):
        result['failure_code'] = failure['code']
        result['retryable'] = failure.get('retryable') if type(failure.get('retryable')) is bool else None
    return result


RECOVERY_VERSION = 'observer-explicit-review-correction-1'
RECOVERY_TARGET_LIMIT = 32


def review_recovery_manifest(record, digest):
    """Retain correction linkage and an exact saved review scope, not prose.

    The old producer's input digest uses JSON's default separators. Retrying
    may update the question working-state inventory as OTHER jobs finish; that
    inventory does not identify this input's assessment targets. Everything
    else in the pack, including its explicit executed checks and objections,
    and the exact evidence presentation remain bound here. Missing legacy
    scope cannot establish recovery.
    """
    pack = record.get('pack')
    if record.get('kind') != 'review_input' or not isinstance(pack, dict):
        return None
    supplied = record.get('input_sha256')
    if (not isinstance(supplied, str) or not re.fullmatch(r'[a-f0-9]{64}', supplied)
            or hashlib.sha256(json.dumps(pack, sort_keys=True, ensure_ascii=False).encode()).hexdigest() != supplied):
        return None
    required = pack.get('required_dossiers')
    allowed = record.get('dossier_allowed_ids')
    presentation = record.get('evidence_presentation')
    if (not isinstance(required, list) or not 0 < len(required) <= RECOVERY_TARGET_LIMIT
            or not isinstance(allowed, dict) or not isinstance(presentation, dict)
            or not presentation.get('version') or not isinstance(presentation.get('spans'), list)
            or not isinstance(pack.get('review_mode'), str) or not pack['review_mode']
            or type(pack.get('final_pass')) is not bool):
        return None
    targets = [d.get('id') for d in required if isinstance(d, dict)]
    if (len(targets) != len(required) or any(not isinstance(i, str) or not i for i in targets)
            or len(set(targets)) != len(targets) or set(allowed) != set(targets)
            or any(not isinstance(ids, list) or not ids or any(not isinstance(i, str) or not i for i in ids)
                   for ids in allowed.values())):
        return None
    if any(not isinstance(d.get('observation_ids'), list)
            or any(not isinstance(i, str) or not i for i in d['observation_ids'])
            or set(d['observation_ids']) != set(allowed[d['id']]) for d in required):
        return None
    included = record.get('included_ids')
    if (not isinstance(included, list) or not included or any(not isinstance(i, str) or not i for i in included)
            or set(included) != {i for ids in allowed.values() for i in ids}):
        return None
    scope_pack = {k: v for k, v in pack.items() if k != 'validation_feedback'}
    context = scope_pack.get('question_context')
    if isinstance(context, dict) and isinstance(context.get('questions'), list):
        scope_pack['question_context'] = {**context, 'questions': [
            {k: v for k, v in q.items() if k != 'working_state'} if isinstance(q, dict) else q
            for q in context['questions']]}
    feedback = pack.get('validation_feedback') or {}
    diagnostic_id = feedback.get('diagnostic_id') if isinstance(feedback, dict) else None
    if diagnostic_id is not None and (not isinstance(diagnostic_id, str) or not diagnostic_id):
        return None
    return {'version': RECOVERY_VERSION,
        'diagnostic_id': diagnostic_id,
        'target_ids': targets, 'scope_digest': digest({'pack': scope_pack,
            'dossier_allowed_ids': allowed, 'included_ids': record.get('included_ids'),
            'evidence_presentation': presentation, 'review_stream': record.get('review_stream')})}


def assessment_receipts(dossier):
    """Only explicit per-dossier adopted assessment receipts; no title match."""
    return list(dict.fromkeys(r.get('receipt_id') for r in dossier.get('assessment_history', [])
        if isinstance(r, dict) and isinstance(r.get('receipt_id'), str)
        and isinstance(r.get('finding'), dict) and r['finding'].get('dossier_id') == dossier.get('id')))


def resolve_review_failures(activities, inputs, receipts, diagnostics, dossiers, tasks, case_id, digest):
    """Fail-closed recovery for frozen producers without lifecycle events.

    A successful receipt alone does not prove adoption, and partial/page
    receipts never clear a whole-input warning. Recovery refers to validation
    of the SAME assessment scope, not final publication or question closure.
    """
    lookup = {r['id']: r for r in inputs}
    diagnostic_lookup = {r['id']: r for r in diagnostics}
    receipts_by_input = {}
    for receipt in receipts:
        receipts_by_input.setdefault(receipt.get('input_record_id'), []).append(receipt)
    activity_lookup = {a['id']: a for a in activities}
    keys = ('case_id', 'task_id', 'batch_id', 'generation', 'round', 'contract_version', 'prompt_version')

    def scoped_pair(inp, receipt):
        task = tasks.get(inp.get('task_id'))
        evidence = receipt.get('evidence_id')
        return bool(task and inp.get('case_id') == case_id and task.get('case_id') == case_id
            and all(inp.get(k) is not None and inp.get(k) == receipt.get(k) for k in keys)
            and isinstance(evidence, str) and evidence and task.get('evidence_id') == evidence
            and inp.get('evidence_id', evidence) == evidence
            and type(inp.get('generation')) is int and inp['generation'] == task.get('retry_generation', 0)
            and type(inp.get('round')) is int and type(inp.get('attempt')) is int
            and all(type(receipt.get(k)) is int for k in ('generation', 'round', 'attempt'))
            and inp['attempt'] >= 1 and inp['attempt'] == receipt.get('attempt')
            and receipt.get('input_record_id') == inp['id']
            and receipt.get('input_sha256') == inp.get('input_sha256'))

    def manifest(inp):
        result = inp.get('_review_recovery') or review_recovery_manifest(inp, digest)
        return result if (isinstance(result, dict) and result.get('version') == RECOVERY_VERSION
            and isinstance(result.get('scope_digest'), str)
            and re.fullmatch(r'[a-f0-9]{64}', result['scope_digest'])
            and isinstance(result.get('target_ids'), list)
            and 0 < len(result['target_ids']) <= RECOVERY_TARGET_LIMIT
            and all(isinstance(i, str) and i for i in result['target_ids'])
            and (result.get('diagnostic_id') is None or isinstance(result.get('diagnostic_id'), str))
            and len(set(result['target_ids'])) == len(result['target_ids'])) else None

    for inp in inputs:
        current = activity_lookup.get(inp['id'])
        context = manifest(inp)
        if (not current or current.get('state') not in ('received', 'succeeded')
                or inp.get('kind') != 'review_input' or not context or not context.get('diagnostic_id')):
            continue
        diagnostic = diagnostic_lookup.get(context['diagnostic_id'])
        prior_input = lookup.get((diagnostic or {}).get('input_record_id'))
        prior = activity_lookup.get((prior_input or {}).get('id'))
        old_context = manifest(prior_input) if prior_input else None
        if (not diagnostic or not prior or prior.get('state') != 'failed' or not prior.get('failure')
                or not old_context or context['scope_digest'] != old_context['scope_digest']
                or context['target_ids'] != old_context['target_ids']
                or any(inp.get(k) != prior_input.get(k) for k in keys)
                or type(inp.get('attempt')) is not int or type(prior_input.get('attempt')) is not int
                or inp['attempt'] != prior_input['attempt'] + 1
                or diagnostic.get('kind') != 'review_diagnostic'
                or any(diagnostic.get(k) != prior_input.get(k) for k in keys + ('attempt', 'input_sha256'))
                or inp.get('review_stream') or prior_input.get('review_stream')):
            continue
        old_receipts = [r for r in receipts_by_input.get(prior_input['id'], [])
            if r.get('receipt_type') == 'dossier_model_error' and r.get('error')
            and r.get('diagnostic_id') == diagnostic['id'] and scoped_pair(prior_input, r)]
        if len(old_receipts) != 1:
            continue
        failed = old_receipts[0]
        if (failed.get('failure_category') not in ('output_contract', 'citation_scope')
                or diagnostic.get('failure_category') != failed.get('failure_category')):
            continue
        accepted = [r for r in receipts_by_input.get(inp['id'], [])
            if r.get('receipt_type') == 'dossier_model' and not r.get('error')
            and not r.get('failure_category') and not r.get('validation_errors') and scoped_pair(inp, r)
            and r.get('evidence_id') == failed.get('evidence_id')]
        if len(accepted) != 1:
            continue
        success = accepted[0]
        for identity in context['target_ids']:
            dossier = dossiers.get(identity)
            if (not dossier or dossier.get('case_id') != case_id
                    or dossier.get('task_id') != inp.get('task_id')
                    or dossier.get('evidence_id') != success.get('evidence_id')
                    or type(dossier.get('generation', 0)) is not int
                    or dossier.get('generation', 0) != inp.get('generation')
                    or success['id'] not in (dossier.get('_assessment_receipts') or assessment_receipts(dossier))):
                break
        else:
            prior['failure_impact'] = 'resolved'
            prior['resolved_by_input_id'] = inp['id']
            prior['resolved_by_receipt_id'] = success['id']
            prior['resolved_diagnostic_id'] = diagnostic['id']
            prior['recovery_scope'] = 'same_scope_validated_assessments'
            prior['failure']['impact'] = '같은 단서와 원문 범위로 다시 검토한 결과가 검증·채택됐어요. 이전 실패는 이력에 남기며 최종 판단 완료를 뜻하지 않아요.'


def lifecycle_activities(events, inputs, dossiers, scoped, digest):
    """Project observed callbacks, never fabricate dispatch/generation from age.

    A retry can resolve an old warning only through its exact retry chain and
    logical owner scope. Similar titles or a later unrelated success cannot.
    Owner refs describe the retained attempt's input versions, not today's
    adopted judgment versions. Original prompts never enter this projection.
    """
    inputs = {r['id']: r for r in inputs}
    groups = {}
    for event in events:
        if event.get('lifecycle_version') != 'model-request-lifecycle-1' or not scoped(event):
            continue
        groups.setdefault(event.get('attempt_id'), []).append(event)
    result = []
    for identity, rows in groups.items():
        rows.sort(key=lambda r: r.get('seq', 0))
        first, latest = rows[0], rows[-1]
        if (not identity or [r.get('seq') for r in rows] != list(range(1, len(rows) + 1))
                or len({r.get('event_id') for r in rows}) != len(rows)
                or any(any(r.get(k) != first.get(k) for k in ('task_id', 'generation', 'evidence_id',
                    'input_record_id', 'input_ref', 'reservation_id', 'owner_refs', 'logical_work_id')) for r in rows)):
            continue  # incomplete/mixed telemetry cannot claim the current stage
        inp = inputs.get(first.get('input_record_id'))
        if inp:
            original = {k: v for k, v in inp.items() if not k.startswith('_')}
            ref = first.get('input_ref') or {}
            if (ref.get('id') != inp['id'] or ref.get('kind') != inp['kind']
                    or ref.get('version') != (inp.get('_source_version') or digest(original))
                    or inp.get('task_id') != first.get('task_id')
                    or inp.get('generation', 0) != first.get('generation', 0)):
                continue
        elif first.get('input_record_id'):
            continue
        phase = latest['phase']
        if phase == 'ended':
            phase = next((r['phase'] for r in reversed(rows[:-1])
                          if r['phase'] in ('accepted', 'partial_accepted', 'page_accepted', 'failed', 'rejected')), 'ended')
        states = {'input_registered': 'input_registered', 'input_preparing': 'input_registered',
            'input_ready': 'input_registered', 'availability_check': 'waiting',
            'availability_verified': 'waiting', 'resource_queued': 'waiting',
            'resource_acquired': 'waiting', 'dispatch_attempted': 'waiting',
            'response_waiting': 'waiting', 'delivery_unknown': 'waiting',
            'response_received': 'received', 'validating': 'validating',
            'validated_output': 'adopting', 'accepted': 'succeeded',
            'partial_accepted': 'partial', 'page_accepted': 'partial',
            'failed': 'failed', 'rejected': 'failed', 'ended': 'received'}
        state = states.get(phase, 'unknown')
        event = next((r for r in reversed(rows) if r['phase'] == phase), latest)
        dispatched = next((r for r in rows if r['phase'] == 'dispatch_attempted'), None)
        # Stage timers do not purport to measure accelerator generation time.
        timer = event if state in ('validating', 'adopting') else dispatched or first
        accepted, rejected = [], []
        for r in rows:
            accepted.extend(r.get('accepted_refs') or [])
            rejected.extend(r.get('rejected_refs') or [])
        details = None
        if state == 'failed':
            details = failure_details({**first, 'failure_category': event.get('metadata', {}).get('failure_category')})
            details['subject_ids'] = [r['id'] for r in rejected or first.get('affected_refs', []) if r.get('kind') == 'dossier']
        result.append({'id': identity, 'kind': 'model', 'title': 'AI 검토 요청', 'state': state,
            'phase': phase, 'delivery_state': latest.get('delivery_state'),
            'at': latest.get('observed_at') or latest.get('created_at'),
            'completed_at': (latest.get('observed_at') or latest.get('created_at'))
                if state in ('succeeded', 'partial', 'failed') or phase == 'ended' else None,
            'timer_at': timer.get('observed_at') if state in ('waiting', 'input_registered', 'validating', 'adopting') else None,
            'timer_origin': 'stage' if state in ('validating', 'adopting') else 'dispatch' if dispatched else 'input_registered',
            'task_id': first.get('task_id'), 'generation': first.get('generation'), 'evidence_id': first.get('evidence_id'),
            'input_record_id': first.get('input_record_id'), 'reservation_id': first.get('reservation_id'),
            'logical_work_id': first.get('logical_work_id'), 'owner_refs': first.get('owner_refs', []),
            'retry_of_attempt_id': first.get('retry_of_attempt_id'),
            'context': model_context(inp, dossiers) if inp else {'subjects': [], 'basis': 'manifest_only'},
            'target': (inp or {}).get('activity_target') or '검토 대상은 요청 상세 참조',
            'failure': details, 'failure_impact': 'unresolved' if details else None,
            'accepted_refs': accepted, 'rejected_refs': rejected, 'result_adopted': bool(accepted),
            'error': event.get('metadata', {}).get('failure_category') if details else None})
    lookup = {a['id']: a for a in result}
    for a in result:
        if a['state'] not in ('succeeded', 'partial'):
            continue
        prior = lookup.get(a.get('retry_of_attempt_id'))
        if (not prior or prior['state'] != 'failed' or any(a.get(k) != prior.get(k)
                for k in ('task_id', 'generation', 'evidence_id', 'logical_work_id'))):
            continue
        owned = {(r.get('kind'), r.get('id')) for r in prior['owner_refs']}
        recovered = {(r.get('kind'), r.get('id')) for r in a['accepted_refs']}
        if owned and owned <= recovered:
            prior['failure_impact'] = 'resolved'
            prior['resolved_by_attempt_id'] = a['id']
            prior['failure']['impact'] = '이 실패 뒤 같은 검토 대상의 재시도가 채택됐어요. 실패 기록은 이력에 남겨요.'
        elif owned & recovered:
            prior['failure_impact'] = 'partial'
            prior['failure']['impact'] = '같은 검토 대상 중 일부만 재시도로 채택됐어요. 나머지 영향은 아직 남아 있어요.'
    return result
