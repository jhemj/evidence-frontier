"""Read-only decision opinion display; never intrusion judgment or filtering authority."""
from collections.abc import Mapping
import re

from .decision_contracts import DecisionRequest, DecisionResult, digest


def project_decision(request, result, *, current_versions, data_mode,
                     current_envelope, expected_cache_key):
    """A distribution is usable for display only with all exact current refs.

    The caller supplies the authoritative envelope and kind/id -> version refs
    from ONE snapshot, plus that request's currently compiled cache key. No
    request-derived envelope or guessed cache key is a substitute for capture.
    Missing context remains unknown. D1 has no attested live score producer.
    """
    if data_mode not in ('example', 'replay', 'live'):
        raise ValueError('Explicit decision display mode required')
    # Revalidate serialized objects as well as dictionaries: frozen Pydantic
    # fields do not make nested score dictionaries immutable.
    request = DecisionRequest.model_validate(
        request.model_dump(mode='json') if isinstance(request, DecisionRequest) else request)
    result = DecisionResult.model_validate(
        result.model_dump(mode='json') if isinstance(result, DecisionResult) else result)
    envelope = current_envelope if isinstance(current_envelope, Mapping) else {}
    versions = current_versions if isinstance(current_versions, Mapping) else {}
    envelope_values = {
        'case_id': request.case_id, 'run_id': request.run_id,
        'snapshot_revision': request.snapshot_revision,
        'ledger_position': request.ledger_position,
    }
    envelope_bound = all(type(envelope.get(k)) is type(v) and envelope.get(k) == v
                         for k, v in envelope_values.items())
    refs = [request.question_ref, request.logical_work_ref, request.candidate_producer_ref,
            *request.counterevidence_refs, *request.required_review_refs, *request.omitted_refs,
            *(e.ref for e in request.evidence), *(r for c in request.candidates for r in c.refs)]
    missing = [r.model_dump() for r in refs if versions.get((r.kind, r.id)) != r.version]
    cache_bound = (isinstance(expected_cache_key, str)
                   and re.fullmatch(r'[a-f0-9]{64}', expected_cache_key) is not None
                   and result.cache_key == expected_cache_key)
    bound = (result.request_id == request.request_id and result.request_digest == digest(request)
             and result.model_identity == request.model_identity
             and result.candidate_order == tuple(c.id for c in request.candidates)
             and result.input_completeness == request.input_completeness)
    distribution_claimed = result.status in ('ok', 'abstained')
    # D1 can construct distributions only from explicitly offline fixtures.
    # An unverified runtime result cannot become live merely by changing a tag.
    provenance_bound = (not distribution_claimed or
        (data_mode != 'live' and result.provenance == 'fixture'
         and result.usage.measurement_origin == 'fixture'
         and result.score_basis in ('full_vocabulary_pre_sampler', 'candidate_normalized_pre_sampler')
         and bool(result.readout_profile_version)))
    if data_mode == 'live' and result.provenance == 'fixture':
        provenance_bound = False
    current = bound and envelope_bound and cache_bound and provenance_bound and not missing
    has_distribution = current and result.status in ('ok', 'abstained') and result.probabilities is not None
    return {
        'kind': 'decision_advisory', 'case_id': request.case_id, 'run_id': request.run_id,
        'snapshot_revision': request.snapshot_revision, 'request_id': request.request_id,
        'data_mode': data_mode, 'provenance': result.provenance,
        'status': result.status if current else 'stale',
        'title': '선택지 비교 · 시험용 예시' if result.provenance == 'fixture' else '선택지 비교 의견',
        'basis': '제시한 선택지 안에서의 조건부 선택 분포예요. 침해 확률이나 근거의 진실성 점수가 아니에요.',
        'reason': result.reason if current else '근거·선택지 버전 또는 출처를 확인하지 못해 이전 의견을 사용하지 않아요.',
        'input_completeness': request.input_completeness,
        'candidate_coverage': request.candidate_coverage,
        'missing_refs': missing,
        'selected_id': result.chosen_id if has_distribution else None,
        'candidates': [{'id': c.id, 'label': c.label, 'description': c.description,
                        'disposition': c.disposition,
                        'probability': result.probabilities[c.id] if has_distribution else None}
                       for c in request.candidates],
        'distribution_available': has_distribution,
        'policy_applied': False, 'can_skip_required_review': False,
        'can_discard_evidence': False, 'changes_intrusion_color': False,
        'display_only': True,
    }
