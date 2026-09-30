"""Read-only reuse of explicit, scoped intrusion judgments; never a new verdict."""
from .incident_status import assessment_errors
from .models import IncidentAssessment


def current_assessments(rows, tasks, active, observations, dossiers, *, complete_sources=False):
    latest = {}
    for row in rows:
        task = tasks.get(row.get('task_id'))
        if (task and row.get('evidence_id') in active
                and row.get('generation', 0) == task.get('retry_generation', 0)):
            latest[(row['task_id'], row.get('hypothesis_id'))] = row
    accepted, pending = [], 0
    canonical = {i: {k: v for k, v in o.items() if k not in
        ('_source_version', '_excerpt_characters', '_excerpt_partial')} for i, o in observations.items()}
    for row in latest.values():
        a = row.get('incident_assessment')
        source_current = row.get('_incident_source_current')
        if complete_sources and '_incident_source_current' not in row:
            from .case_memory import _object
            from .case_synthesis import source_revision
            ids = set(row.get('source_ids') or [])
            origins = {_object(canonical[i]) for i in ids if i in canonical} - {None}
            sources = [o for i, o in canonical.items() if i in ids or _object(o) in origins]
            source_current = bool(ids and ids <= canonical.keys()
                                  and source_revision(sources) == row.get('source_revision'))
        if row.get('review_revision'):
            from .case_synthesis import review_revision
            ds = [d for d in dossiers if d.get('task_id') == row['task_id']
                  and d.get('generation', 0) == row.get('generation', 0)]
            source_current = source_current is True and review_revision(ds, row.get('source_ids', [])) == row['review_revision']
        refs = set((a or {}).get('supporting_evidence_ids', []) + (a or {}).get('refuting_evidence_ids', []))
        if (not a or row.get('status') != 'reviewed' or row.get('followup_dossier_id')
                or row.get('finding', {}).get('open_objections') or source_current is not True
                or not refs <= canonical.keys()
                or assessment_errors({**row, 'findings': [row.get('finding', {})]}, canonical)):
            pending += 1
            continue
        accepted.append({'id': row['id'], 'assessment': IncidentAssessment.model_validate(a).model_dump(),
                         'created_at': row.get('created_at'), 'source_ids': sorted(refs)})
    return accepted, pending
