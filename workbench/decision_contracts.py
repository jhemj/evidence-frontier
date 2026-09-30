"""D1-only typed advisory contracts, separate from generation and case judgment.

No setting migration, scheduler integration, model loading or policy authority.
Unknown measurements stay null; a fixture never certifies an installed runner.
"""
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator


def digest(value):
    if isinstance(value, BaseModel):
        value = value.model_dump(mode='json')
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(',', ':')).encode()).hexdigest()


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)


class DecisionRef(Contract):
    kind: str = Field(min_length=1)
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)


class EvidenceSlice(Contract):
    ref: DecisionRef
    content_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    unit: Literal['byte', 'character', 'record']
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)

    @model_validator(mode='after')
    def positive_span(self):
        if self.end <= self.start:
            raise ValueError('Presented evidence must retain its exact nonempty range')
        return self


class DecisionCandidate(Contract):
    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    description: str
    refs: tuple[DecisionRef, ...] = ()
    disposition: Literal['advisory', 'insufficient_evidence', 'alternatives_missing', 'abstain'] = 'advisory'


class ModelIdentity(Contract):
    model: str = Field(min_length=1)
    weights_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    gguf_sha256: str | None = Field(default=None, pattern=r'^[a-f0-9]{64}$')
    quantization: str = Field(min_length=1)
    ollama_version: str = Field(min_length=1)
    runner_version: str = Field(min_length=1)
    tokenizer_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    chat_template_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    readout_profile_id: str = Field(min_length=1)
    calibration_profile_id: str = Field(min_length=1)


class DecisionConnection(Contract):
    """D1 cannot enable routing or alter the existing generation configuration."""
    mode: Literal['disabled', 'shadow'] = 'disabled'
    model_identity: ModelIdentity | None = None
    capability_certificate_id: str | None = None
    resource_group_id: str | None = None
    resource_parallelism: Literal[1] = 1
    rights_status: Literal['unverified', 'explicitly_reviewed'] = 'unverified'


class ReadoutProfile(Contract):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    calibration_profile_id: str = Field(min_length=1)
    # Official SERVE.md's choice setting, not a fitted forensic/GGUF setting.
    readout_temperature: FiniteFloat = Field(default=0.85, gt=0)
    max_candidates: int = Field(default=20, ge=2, le=20, strict=True)
    context_tokens: int = Field(default=16384, ge=2, strict=True)
    output_tokens: Literal[1] = 1
    prompt_layout: Literal['openjev-text-choice-v1'] = 'openjev-text-choice-v1'
    score_basis: Literal['unverified', 'full_vocabulary_pre_sampler', 'candidate_normalized_pre_sampler'] = 'unverified'


class DecisionRequest(Contract):
    schema_version: Literal['decision-choice-v1'] = 'decision-choice-v1'
    request_id: str = Field(min_length=1)
    attempt_id: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    snapshot_revision: str = Field(min_length=1)
    ledger_position: int = Field(ge=0, strict=True)
    policy_kind: Literal['review_priority', 'review_route', 'evidence_fit', 'presentation_category']
    policy_version: str = Field(min_length=1)
    question_ref: DecisionRef
    logical_work_ref: DecisionRef
    candidate_producer_ref: DecisionRef
    candidate_coverage: Literal['not_guaranteed'] = 'not_guaranteed'
    candidates: tuple[DecisionCandidate, ...] = Field(min_length=2, max_length=52)
    evidence: tuple[EvidenceSlice, ...] = ()
    counterevidence_refs: tuple[DecisionRef, ...] = ()
    required_review_refs: tuple[DecisionRef, ...] = ()
    omitted_refs: tuple[DecisionRef, ...] = ()
    input_completeness: Literal['complete_presented_scope', 'partial', 'unknown'] = 'unknown'
    state: str
    instructions: str = Field(min_length=1)
    model_identity: ModelIdentity
    resource_group_id: str = Field(min_length=1)
    input_token_reservation: int | None = Field(default=None, ge=1, strict=True)
    output_token_reservation: Literal[1] = 1

    @model_validator(mode='after')
    def exact_candidates(self):
        if len({c.id for c in self.candidates}) != len(self.candidates):
            raise ValueError('Candidate stable IDs must be unique')
        if not any(c.disposition != 'advisory' for c in self.candidates):
            raise ValueError('An explicit insufficient/alternatives-missing/abstain choice is required')
        return self


class CandidateToken(Contract):
    candidate_id: str
    letter: str
    token_id: int = Field(ge=0, strict=True)
    token_bytes: tuple[int, ...]


class CompiledDecision(Contract):
    request_digest: str
    candidate_order: tuple[str, ...]
    candidate_tokens: tuple[CandidateToken, ...]
    rendered_prompt: str
    prompt_sha256: str
    input_tokens: int = Field(ge=1, strict=True)
    cache_key: str
    profile: ReadoutProfile


class DecisionUsage(Contract):
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    cached_input_tokens: int | None = Field(default=None, ge=0, strict=True)
    total_duration_ns: int | None = Field(default=None, ge=0, strict=True)
    load_duration_ns: int | None = Field(default=None, ge=0, strict=True)
    prefill_duration_ns: int | None = Field(default=None, ge=0, strict=True)
    response_duration_ns: int | None = Field(default=None, ge=0, strict=True)
    measurement_origin: Literal['not_measured', 'fixture', 'installed_probe'] = 'not_measured'


class DecisionResult(Contract):
    schema_version: Literal['decision-choice-v1'] = 'decision-choice-v1'
    request_id: str
    request_digest: str
    cache_key: str | None = None
    status: Literal['ok', 'abstained', 'unsupported', 'failed']
    chosen_id: str | None = None
    raw_logprobs: dict[str, FiniteFloat] | None = None
    probabilities: dict[str, FiniteFloat] | None = None
    missing_candidate_ids: tuple[str, ...] = ()
    score_basis: str | None = None
    first_position_verified: bool = False
    candidate_probability_mass: FiniteFloat | None = Field(default=None, ge=0, le=1)
    order_stability: FiniteFloat | None = None
    candidate_order: tuple[str, ...]
    model_identity: ModelIdentity
    readout_profile_version: str | None = None
    input_completeness: str
    reason: str
    provenance: Literal['disabled', 'fixture', 'unverified_runtime']
    advisory_only: Literal[True] = True
    policy_applied: Literal[False] = False
    policy_rejection_reason: str = 'D1 has no scheduling or judgment authority'
    usage: DecisionUsage = Field(default_factory=DecisionUsage)

    @model_validator(mode='after')
    def no_partial_distribution(self):
        if len(set(self.candidate_order)) != len(self.candidate_order):
            raise ValueError('Result must preserve unique candidate stable IDs')
        if not set(self.missing_candidate_ids) <= set(self.candidate_order):
            raise ValueError('Missing scores must identify supplied candidates')
        if self.raw_logprobs is not None:
            if not set(self.raw_logprobs) <= set(self.candidate_order) or any(v > 0 for v in self.raw_logprobs.values()):
                raise ValueError('Raw logprobs must retain supplied candidate IDs and logprob semantics')
        if self.status in ('unsupported', 'failed') and (self.chosen_id is not None or self.probabilities is not None):
            raise ValueError('Unsupported/failed decisions have null choice and distribution')
        if self.status in ('ok', 'abstained') and (self.chosen_id is None or self.probabilities is None):
            raise ValueError('An evaluated choice requires a complete distribution and exact chosen ID')
        if self.probabilities is not None:
            if (self.missing_candidate_ids or not self.first_position_verified
                or set(self.probabilities) != set(self.candidate_order)
                or not self.raw_logprobs or set(self.raw_logprobs) != set(self.candidate_order)):
                raise ValueError('Only complete first-position candidate scores define a distribution')
            if any(p < 0 or p > 1 for p in self.probabilities.values()) or abs(sum(self.probabilities.values()) - 1) > 1e-10:
                raise ValueError('Invalid conditional candidate distribution')
        if self.chosen_id is not None and self.chosen_id not in self.candidate_order:
            raise ValueError('A selected stable ID must belong to the exact candidate set')
        return self


def unavailable(request, reason, *, status='unsupported', provenance='unverified_runtime',
                compiled=None, missing=(), raw_logprobs=None):
    return DecisionResult(request_id=request.request_id, request_digest=digest(request),
        candidate_order=tuple(c.id for c in request.candidates), model_identity=request.model_identity,
        input_completeness=request.input_completeness, status=status, reason=reason,
        provenance=provenance, cache_key=compiled.cache_key if compiled else None,
        readout_profile_version=compiled.profile.version if compiled else None,
        missing_candidate_ids=tuple(missing), raw_logprobs=raw_logprobs)
