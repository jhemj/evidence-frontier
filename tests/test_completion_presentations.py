from workbench.completion import presentations, project


class Store:
    def __init__(self, rows): self.rows = rows
    def list(self, kind, cid): return self.rows.get(kind, [])


def test_prepared_input_is_not_counted_without_successful_receipt():
    store = Store({'review_input': [{'id': 'in', 'task_id': 't', 'generation': 0,
                                    'included_ids': ['o'], 'pack': {}}],
                   'investigation_plan': [{'task_id': 't', 'generation': 0, 'valid_ids': ['o']}],
                   'synthesis_input': [{'id': 'syn', 'task_id': 't', 'generation': 0,
                                        'pack': {'observations': [{'id': 'o'}]}}],
                   'receipt': []})
    rows = presentations(store, 'c', {'t': {'retry_generation': 0}})
    assert all(r['transmitted'] is not False for r in rows)
    doc = {'observations': [{'id': 'o', 'type': 'linux_event', 'fields': {}}],
           'dossiers': [], 'coverage': [], 'check_ledger': {'not_executed': 0, 'unassessed_contracts': 0},
           'case_synthesis': [], 'investigation_frontier': {}}
    out = project(doc, rows)
    assert out['ai_prepared_observations'] == 1
    assert out['ai_transmitted_observations'] == 1  # investigator plan is a valid model result
    assert out['ai_valid_assessed_observations'] == 0  # planning is not source adjudication


def test_successful_receipt_counts_transmitted_and_marks_legacy_scope_unknown():
    store = Store({'review_input': [{'id': 'in', 'task_id': 't', 'generation': 0,
                                    'included_ids': ['o'], 'pack': {}}],
                   'investigation_plan': [], 'synthesis_input': [],
                   'receipt': [{'input_record_id': 'in', 'receipt_type': 'dossier_model'}]})
    rows = presentations(store, 'c', {'t': {'retry_generation': 0}})
    doc = {'observations': [{'id': 'o', 'type': 'linux_event', 'fields': {}}],
           'dossiers': [], 'coverage': [], 'check_ledger': {'not_executed': 0, 'unassessed_contracts': 0},
           'case_synthesis': [], 'investigation_frontier': {}}
    out = project(doc, rows)
    assert out['ai_transmitted_observations'] == 1
    assert out['ai_valid_assessed_observations'] == 1
    assert out['presentation_scope']['legacy_scope_unknown'] is True


def test_rejected_or_stale_receipt_is_not_valid_assessed_and_manifest_is_aggregated():
    manifest = {'version': 'evidence-presentation-1', 'spans': [
        {'span_id': 's1', 'observation_id': 'o', 'extent': 'partial_field', 'truncated': True},
        {'span_id': 's2', 'observation_id': 'o', 'extent': 'complete_field', 'truncated': False}]}
    store = Store({'review_input': [{'id': 'in', 'task_id': 't', 'generation': 0,
                                    'included_ids': ['o'], 'evidence_presentation': manifest, 'pack': {}}],
                   'investigation_plan': [], 'synthesis_input': [],
                   'receipt': [{'input_record_id': 'in', 'task_id': 'other',
                                'receipt_type': 'dossier_model'}]})
    rows = presentations(store, 'c', {'t': {'retry_generation': 0}})
    doc = {'observations': [{'id': 'o', 'type': 'linux_event', 'fields': {}}],
           'dossiers': [], 'coverage': [], 'check_ledger': {'not_executed': 0, 'unassessed_contracts': 0},
           'case_synthesis': [], 'investigation_frontier': {}}
    out = project(doc, rows)
    assert out['ai_transmitted_observations'] == 0
    assert out['ai_valid_assessed_observations'] == 0
    assert out['presentation_scope']['exact_span_ids'] == 2
    assert out['presentation_scope']['partial_or_truncated_spans'] == 1
    assert out['presentation_scope']['prepared_spans']==2
    assert out['presentation_scope']['valid_assessed_spans']==0
