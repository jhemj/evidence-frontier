"""Describe recorded work, not a second AI judgment or a new investigation plan."""
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
        return {'phase': 'tool', 'title': '자료 확인 작업 실패', 'reason': '요청한 범위의 자료 확인을 완료하지 못했어요',
            'impact': '이 검사에서 답을 얻지 못했어요. 기록이 없다는 뜻으로 판단하지 않아요.', 'codes': codes}
    return {'phase': 'request', 'title': 'AI 검토 요청 실패',
        'reason': '이 요청은 정상적인 검토 결과를 받지 못했어요. 상세 원인은 작업 기록에서 확인할 수 있어요',
        'impact': '이 요청의 새 판단은 제공되지 않았어요. 다른 작업의 진행 여부와는 구분해요.', 'codes': codes}
