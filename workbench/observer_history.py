"""Public, retained hypothesis explanations; not a new reasoning/model layer.

Historical entries describe what the ledger said then. They do not certify
source freshness, successful tests, or today's judgment. No source is mutated.
"""
from .observer_view import digest


def explanation_entries(hypothesis):
    entries = []
    seen = set()
    for revision in hypothesis.get('revision_history', []):
        number = revision.get('revision')
        if not isinstance(number, int) or isinstance(number, bool) or number < 1 or number in seen:
            raise ValueError('Invalid explanation revision')
        # The same hypothesis can be reconsidered by another task. Retain that
        # historical task/generation, never bind it to today's executing job.
        # Case and evidence ownership, unlike working context, must still match.
        if any(revision.get(k, hypothesis.get(k)) != hypothesis.get(k)
               for k in ('case_id', 'evidence_id')):
            raise ValueError('Cross-scope explanation revision')
        seen.add(number)
        scenario = revision.get('scenario_assessment') or {}
        entry = {
            'hypothesis_id': hypothesis['id'], 'revision': number, 'at': revision.get('at'),
            'title': revision.get('title'), 'statement': revision.get('card_summary'),
            'reason': scenario.get('ranking_reason') or revision.get('reasoning') or '',
            'scope': scenario.get('comparison_question'),
            'alternative': scenario.get('alternative_explanation'),
            'next_candidate': scenario.get('next_check'),
            'judgment': revision.get('judgment'), 'lifecycle': revision.get('lifecycle'),
            'change_reason': revision.get('change_reason'),
            'observation_ids': list(dict.fromkeys(revision.get('observation_ids', []))),
            'task_id': revision.get('task_id', hypothesis.get('task_id')),
            'evidence_id': revision.get('evidence_id', hypothesis.get('evidence_id')),
            'generation': revision.get('generation', hypothesis.get('generation', 0)),
            'historical': True, 'source_freshness': 'not_certified_at_historical_revision',
        }
        entry['version'] = digest(entry)
        entries.append(entry)
    return sorted(entries, key=lambda e: e['revision'])


def explanation_manifest(hypothesis):
    entries = explanation_entries(hypothesis)
    return {'version': digest(entries), 'count': len(entries),
            'entries': [{k: entry[k] for k in ('revision', 'at', 'version')} for entry in entries]}
