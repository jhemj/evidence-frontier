"""Presentation of explicit, source-bound intrusion assessments. Never a risk score.

Narrow confirmed facts, detection counts and relevance values cannot vote for
intrusion. Existing records without the separate contract remain unassessed.
"""
from .models import IncidentAssessment

LEVELS = ('undetermined', 'suspected', 'probable', 'confirmed')


def assessment_errors(output, observations=None):
    assessment = output.get('incident_assessment')
    if assessment is None:
        return []
    try:
        a = IncidentAssessment.model_validate(assessment)
    except ValueError:
        return [{'code': 'incident_assessment_schema'}]
    support, refute = set(a.supporting_evidence_ids), set(a.refuting_evidence_ids)
    issues = []
    if (not support.issubset(output.get('supporting_evidence_ids', []))
            or not refute.issubset(output.get('refuting_evidence_ids', [])) or support & refute):
        issues.append({'code': 'incident_assessment_citation_scope'})
    if a.verdict in LEVELS[1:] and not support or a.verdict == 'refuted' and not refute:
        issues.append({'code': 'incident_assessment_positive_basis_required'})
    findings = output.get('findings', [])
    if a.verdict == 'confirmed' and (not findings or any(
            f.get('judgment') != '확인' or f.get('open_objections') or f.get('basis') == 'absence'
            for f in findings)):
        issues.append({'code': 'incident_confirmation_not_resolved'})
    if a.verdict=='confirmed' and observations is not None:
        from .evidence_semantics import STATIC_CONTENT_TYPES
        sources=[observations[i] for i in support if i in observations]
        if not sources or all(o.get('type') in STATIC_CONTENT_TYPES for o in sources):
            issues.append({'code':'incident_confirmation_static_only',
                'detail':'Confirmed configuration/static content alone cannot confirm an intrusion. Preserve the narrow fact and assess authorization/behaviour separately.'})
    return issues


def project(document):
    active = {e['id'] for e in document.get('evidence', []) if e.get('connected', True)}
    tasks = {t['id']: t for t in document.get('task', []) if not t.get('superseded')}
    observations = {o['id']: o for o in document.get('observation', document.get('observations', []))
                    if o.get('evidence_id') in active}
    latest = {}
    for row in document.get('case_synthesis', []):
        task = tasks.get(row.get('task_id'))
        if (not task or row.get('evidence_id') not in active
                or row.get('generation', 0) != task.get('retry_generation', 0)):
            continue
        latest[(row['task_id'], row.get('hypothesis_id'))] = row
    assessed, pending = [], 0
    for row in latest.values():
        a = row.get('incident_assessment')
        output = {**row, 'findings': [row.get('finding', {})]}
        refs = set((a or {}).get('supporting_evidence_ids', []) + (a or {}).get('refuting_evidence_ids', []))
        sources_current = False
        if row.get('source_ids') is not None and row.get('source_revision'):
            from .case_memory import _object
            from .case_synthesis import source_revision
            source_ids = set(row['source_ids'])
            origins = {_object(observations[i]) for i in source_ids if i in observations} - {None}
            sources = [o for o in observations.values() if o['id'] in source_ids or _object(o) in origins]
            sources_current = source_ids.issubset(observations) and source_revision(sources) == row['source_revision']
        if row.get('review_revision'):
            from .case_synthesis import review_revision
            dossiers=[d for d in document.get('dossier',document.get('dossiers',[])) if d.get('task_id')==row['task_id']
                and d.get('generation',0)==row.get('generation',0)]
            sources_current = sources_current and review_revision(dossiers,row.get('source_ids',[]))==row['review_revision']
        # Open objections, follow-up work or invalidated sources cannot retain
        # an earlier positive verdict. A later held row replaces its predecessor.
        if (not a or row.get('status') != 'reviewed' or row.get('followup_dossier_id')
                or row.get('finding', {}).get('open_objections')
                or not sources_current or not refs.issubset(observations) or assessment_errors(output,observations)):
            pending += 1
            continue
        assessed.append({**a, 'id': row['id'], 'hypothesis_id': row.get('hypothesis_id'),
                         'updated_at': row.get('created_at'), 'question': row.get('question', '')})
    positives = [a for a in assessed if a['verdict'] in LEVELS[1:]]
    leading = max(positives, key=lambda a: LEVELS.index(a['verdict']), default=None)
    counts = {key: sum(a['verdict'] == key for a in assessed) for key in (*LEVELS, 'refuted')}
    return {'version': 'incident-assessment-1', 'verdict': leading['verdict'] if leading else 'undetermined',
            'leading': leading, 'assessments': assessed, 'counts': counts, 'pending': pending,
            'metric_kind': 'explicit_scoped_assessment_not_probability',
            'limitation': '질문별 침해 판단입니다. 단서 확정·관련성·개수는 침해 확률이 아닙니다. 가설 반박은 전체 정상 판정이 아닙니다.'}
