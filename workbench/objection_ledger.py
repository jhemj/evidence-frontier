"""Durable, source-bound objections independent of model summary selection.

An objection is an assessment, not new evidence. Validated contrary citations
open it. Silence in a later response never resolves it. Resolution requires an
explicit, source-backed disposition in a final (not intermediate) review.
These structural checks do not certify the model's semantic interpretation.
"""
from copy import deepcopy
import hashlib
import json


def current(store, cid, task, dossier_ids, *, only_open=True):
    owners = set(dossier_ids)
    return [r for r in store.list('objection', cid)
            if r['task_id'] == task['id'] and r['generation'] == task.get('retry_generation', 0)
            and r['dossier_id'] in owners and (not only_open or r['status'] == 'open')]


def view(rows):
    return [{k: deepcopy(r[k]) for k in ('id', 'dossier_id', 'statement',
             'observation_ids', 'span_ids', 'status', 'assessment_receipt_id')} for r in rows]


def capture(store, cid, task, output, receipt_id, presentation):
    """Caller holds the validated-attempt transaction; never capture failures."""
    candidates = [(f['dossier_id'], f.get('title', ''), f.get('counterevidence_ids', []))
                  for f in output.get('findings', [])]
    candidates += [(a.get('dossier_id'), a['reason'], a.get('observation_ids', []))
                   for a in output.get('check_assessments', []) if a['outcome'] == 'refutes']
    if len(output.get('findings',[]))==1 and output.get('refuting_evidence_ids'):
        finding=output['findings'][0]
        candidates.append((finding['dossier_id'],finding.get('reason',finding.get('title','')),
                           output['refuting_evidence_ids']))
    known = {r['fingerprint']: r for r in store.list('objection', cid)}
    result = []
    for did, statement, refs in candidates:
        refs = sorted(set(refs))
        if not did or not refs:
            continue
        spans = sorted(s['span_id'] for s in presentation['spans'] if s['observation_id'] in refs)
        key = hashlib.sha256(json.dumps([task['id'], task.get('retry_generation', 0),
                                       did, statement, refs, spans], ensure_ascii=False).encode()).hexdigest()
        row = known.get(key)
        if row is None:
            row = store.add('objection', cid, task_id=task['id'], generation=task.get('retry_generation', 0),
                dossier_id=did, fingerprint=key, statement=statement, observation_ids=refs,
                span_ids=spans, status='open', assessment_receipt_id=receipt_id)
            known[key] = row
        result.append(row['id'])
    return list(dict.fromkeys(result))


def errors(output, obligations, allowed_by_dossier, *, intermediate=False):
    known = {o['id']: o for o in obligations}
    seen = set(); issues = []
    for answer in output.get('objection_assessments', []):
        ident = answer['objection_id']; objection = known.get(ident)
        if objection is None or ident in seen:
            issues.append({'code': 'unknown_or_duplicate_objection', 'objection_id': ident})
            continue
        seen.add(ident)
        allowed = set(allowed_by_dossier.get(objection['dossier_id'], []))
        cited = set(answer['observation_ids'])
        if cited - allowed:
            issues.append({'code': 'objection_resolution_citation_scope', 'objection_id': ident})
        if answer['outcome'] == 'resolved':
            if intermediate:
                issues.append({'code': 'intermediate_cannot_resolve_objection', 'objection_id': ident})
            if not cited or answer.get('basis', 'positive_evidence') != 'positive_evidence':
                issues.append({'code': 'objection_resolution_requires_positive_source', 'objection_id': ident})
            if not set(objection['observation_ids']).issubset(allowed):
                issues.append({'code': 'objection_source_not_represented', 'objection_id': ident})
    return issues


def resolve(store, output, receipt_id):
    """Only after errors() and the final stale-scope guard have passed."""
    for answer in output.get('objection_assessments', []):
        row = store.get(answer['objection_id'])
        revision = store.add('objection_decision', row['case_id'],
            assessment_receipt_id=receipt_id, previous_status=row['status'],
            original_assessment_receipt_id=row['assessment_receipt_id'],
            original_observation_ids=deepcopy(row['observation_ids']),
            original_span_ids=deepcopy(row['span_ids']),original_statement=row['statement'],
            **deepcopy(answer))
        if answer['outcome'] == 'resolved':
            store.update(row['id'], status='resolved', decision_revision=revision['id'],
                         resolution_receipt_id=receipt_id)


def qualify(output, obligations):
    """Preserve narrow facts but never publish an open dispute as settled."""
    result = deepcopy(output)
    for finding in result.get('findings', []):
        opened = [o for o in obligations if o['dossier_id'] == finding['dossier_id']]
        finding['open_objections'] = view(opened)
        finding['publication_status'] = 'qualified_open_objections' if opened else 'scoped_assessment'
        finding['publication_limit'] = (
            f'미해결 반론 {len(opened)}건. 확인된 좁은 사실과 별개로 이 해석은 해결된 결론이 아닙니다.'
            if opened else '')
    return result
