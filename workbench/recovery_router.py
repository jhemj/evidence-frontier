"""Bounded, Controller-owned recovery recommendations; disabled by default.

This is not an executor, new model, scheduler, or an automatic retry wrapper.
No Store, Provider, clock, filesystem or network is owned here. Callers must
retain/export receipts, restore them after reconnect, and supply Controller-
verified observations. A model cannot issue execution/adoption attestations.

An episode fences one failed request exactly. Its *budget* survives a changed
attempt, input, generation, validator or repair policy for the same original
obligation/object. A new contract or collection is not recovery of that goal.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re

from .request_lifecycle import DELIVERY

VERSION = 'recovery-router-1'
SUPPORTED = frozenset(('empty_discriminating_condition',
    'required_body_not_presented', 'unchanged_invalid_input'))
_HASH = re.compile(r'^[a-f0-9]{64}$')
_CODE = re.compile(r'^[A-Za-z0-9_.:-]{1,180}$')
_HARNESS_VOLATILE = frozenset(('nonce', 'clock', 'pid'))


def digest(value):
    """Hash JSON exactly; do not silently stringify unsupported objects."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _code(value, name):
    if not isinstance(value, str) or not _CODE.fullmatch(value):
        raise ValueError('invalid ' + name)


def _hash(value, name):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError('invalid ' + name)


def canonical_loop_material(value):
    """Only this explicitly harness-owned namespace has volatile exclusions.

    Evidence fields named nonce/clock/pid/time, unknown harness fields, source
    bytes and semantic requirements remain part of the key. There is no broad
    recursive removal of timestamps, IDs, tokens or field names.
    """
    result = deepcopy(value)
    harness = result.get('harness')
    if isinstance(harness, dict):
        result['harness'] = {k: v for k, v in harness.items()
                             if k not in _HARNESS_VOLATILE}
        if not result['harness']:
            result.pop('harness')
    return result


@dataclass(frozen=True)
class ExactRef:
    kind: str
    id: str
    version: str

    def __post_init__(self):
        _code(self.kind, 'reference kind')
        _code(self.id, 'reference id')
        _hash(self.version, 'reference version')


@dataclass(frozen=True)
class ScopeFence:
    case_id: str
    task_id: str
    generation: int
    evidence_id: str
    source_run: str
    source_ref: ExactRef
    owner_ref: ExactRef
    result_ref: ExactRef | None = None

    def __post_init__(self):
        for name in ('case_id', 'task_id', 'evidence_id'):
            _code(getattr(self, name), name)
        if (not isinstance(self.source_run, str) or not self.source_run
                or len(self.source_run) > 180 or any(c in self.source_run for c in ('\x00', '\n', '\r'))):
            raise ValueError('invalid source_run')
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError('invalid generation')
        if not isinstance(self.source_ref, ExactRef) or not isinstance(self.owner_ref, ExactRef):
            raise ValueError('exact source and request owner required')
        if self.result_ref is not None and not isinstance(self.result_ref, ExactRef):
            raise ValueError('invalid exact result reference')


@dataclass(frozen=True)
class OriginalObligation:
    id: str
    # Canonical original goal/constraints, issued by the Controller, not a
    # replacement contract. Its digest must not change during recovery.
    required: dict
    required_purpose: str | None = None
    _sha256: str = field(init=False, repr=False)
    _required_json: str = field(init=False, repr=False)

    def __post_init__(self):
        _code(self.id, 'stable obligation id')
        if not isinstance(self.required, dict) or not self.required:
            raise ValueError('original obligation must not be empty')
        if self.required_purpose not in (None, 'discover', 'discriminate', 'verify_reliability'):
            raise ValueError('invalid original purpose')
        object.__setattr__(self, 'required', deepcopy(self.required))
        object.__setattr__(self, '_required_json', json.dumps(self.required, sort_keys=True,
            ensure_ascii=False, separators=(',', ':'), allow_nan=False))
        object.__setattr__(self, '_sha256', digest({'required': self.required,
            'required_purpose': self.required_purpose}))

    @property
    def sha256(self):
        return self._sha256

    @property
    def canonical_required(self):
        return json.loads(self._required_json)


@dataclass(frozen=True)
class LoopMaterial:
    obligation: OriginalObligation
    target_coordinate: dict
    input_presentation: dict
    resolver_version: str
    parser_version: str
    validator_version: str
    repair_policy_version: str
    preconditions: dict = field(default_factory=dict)
    harness: dict = field(default_factory=dict)
    _canonical_json: str = field(init=False, repr=False)

    def __post_init__(self):
        for name in ('resolver_version', 'parser_version', 'validator_version', 'repair_policy_version'):
            _code(getattr(self, name), name)
        for name in ('target_coordinate', 'input_presentation', 'preconditions', 'harness'):
            if not isinstance(getattr(self, name), dict):
                raise ValueError('invalid ' + name)
        if not self.target_coordinate or not self.input_presentation:
            raise ValueError('target and actual input presentation required')
        value = canonical_loop_material({'obligation_sha256': self.obligation.sha256,
            'target_coordinate': self.target_coordinate,
            'input_presentation': self.input_presentation,
            'versions': {k: getattr(self, k) for k in ('resolver_version', 'parser_version',
                'validator_version', 'repair_policy_version')},
            'preconditions': self.preconditions, 'harness': self.harness})
        # A caller mutation after registration cannot alter the captured key.
        object.__setattr__(self, '_canonical_json', json.dumps(value, sort_keys=True,
            ensure_ascii=False, separators=(',', ':'), allow_nan=False))

    @property
    def canonical(self):
        return json.loads(self._canonical_json)

    @property
    def loop_key(self):
        return digest(self.canonical)

    @property
    def input_digest(self):
        return digest(self.canonical['input_presentation'])


@dataclass(frozen=True)
class FailureEpisode:
    fence: ScopeFence
    request_attempt_id: str
    obligation_id: str
    obligation_digest: str
    phase: str
    category: str
    delivery: str
    transport_resource: str
    before: LoopMaterial
    failure_ref: ExactRef

    def __post_init__(self):
        for name in ('request_attempt_id', 'obligation_id', 'phase', 'category', 'transport_resource'):
            _code(getattr(self, name), name)
        _hash(self.obligation_digest, 'obligation digest')
        if self.delivery not in DELIVERY:
            raise ValueError('invalid delivery certainty')
        if (self.obligation_id != self.before.obligation.id
                or self.obligation_digest != self.before.obligation.sha256):
            raise ValueError('original obligation binding mismatch')

    @property
    def budget_key(self):
        # Generation/version/attempt/contract re-encoding cannot reset limits.
        # Exact current versions remain in the fence and per-attempt loop key.
        f = self.fence
        return digest({'case_id': f.case_id, 'evidence_id': f.evidence_id,
            'source_run': f.source_run, 'source_identity': [f.source_ref.kind, f.source_ref.id],
            'owner_identity': [f.owner_ref.kind, f.owner_ref.id],
            'obligation_id': self.obligation_id, 'target_coordinate': self.before.canonical['target_coordinate']})

    @property
    def id(self):
        return 'FAILURE_EPISODE-' + digest({'fence': asdict(self.fence),
            'request_attempt_id': self.request_attempt_id, 'failure_ref': asdict(self.failure_ref),
            'obligation_id': self.obligation_id, 'phase': self.phase, 'category': self.category})


@dataclass(frozen=True)
class ExecutionObservation:
    """Controller-issued actual execution reconciliation, never adapter entry."""
    fence: ScopeFence
    request_attempt_id: str
    receipt_ref: ExactRef
    terminal: bool
    execution_known: bool
    observed_result_ref: ExactRef | None = None

    def matches(self, episode):
        return (self.fence == episode.fence
            and self.request_attempt_id == episode.request_attempt_id
            and self.terminal is True and self.execution_known is True
            and self.observed_result_ref == episode.fence.result_ref)


@dataclass(frozen=True)
class CostReceipt:
    """Observed costs; None is unknown, not zero or a made-up estimate."""
    receipt_ref: ExactRef
    model_requests: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    wall_seconds: float | None = None
    resource_wait_seconds: float | None = None
    retry_count: int | None = None

    def __post_init__(self):
        if not isinstance(self.receipt_ref, ExactRef):
            raise ValueError('exact cost receipt required')
        for name in ('model_requests', 'input_tokens', 'output_tokens', 'retry_count'):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError('invalid observed cost')
        for name in ('wall_seconds', 'resource_wait_seconds'):
            value = getattr(self, name)
            if value is not None and (type(value) not in (int, float)
                    or not math.isfinite(value) or value < 0):
                raise ValueError('invalid observed duration')


@dataclass(frozen=True)
class RecoveryBudget:
    """Explicit isolated-test limits; no implicit production budget changes."""
    max_actions: int
    max_model_requests: int
    max_total_tokens: int
    max_wall_seconds: float
    max_unchanged_actions: int = 1

    def __post_init__(self):
        for name in ('max_actions', 'max_model_requests', 'max_total_tokens', 'max_unchanged_actions'):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError('invalid finite recovery budget')
        if (type(self.max_wall_seconds) not in (int, float)
                or not math.isfinite(self.max_wall_seconds) or self.max_wall_seconds < 0):
            raise ValueError('invalid finite duration budget')


# Explicit meaningful Controller projections. Titles, IDs, revision counters,
# confidence prose and request counts are NOT a progress axis. Evidence time,
# PID and nonce inside a coordinate/coverage remain meaningful input data.
_PROGRESS_FIELDS = {
    'collection': ('target_coordinate', 'content_sha256', 'coverage_scope'),
    'input': ('target_coordinate', 'presented_content_sha256', 'view', 'span', 'complete'),
    'accepted_assessment': ('target_coordinate', 'outcome', 'support_content_digests',
        'counterevidence_content_digests', 'remaining_obligation_digests'),
    'objection': ('target_coordinate', 'contradiction_content_sha256', 'status', 'scope'),
    'question': ('answer_status', 'answer_scope', 'adopted_claim_semantic_keys',
        'remaining_obligation_digests', 'next_test_semantic_keys'),
}


def progress_fingerprints(snapshot):
    """No semantic inference from arbitrary prose or model statements.

    Assessment/objection/question rows count only when Controller-adopted.
    A field supplied with no meaningful payload does not create progress.
    Callers retain the underlying exact version refs separately for audit.
    """
    result = {}
    for axis, fields in _PROGRESS_FIELDS.items():
        rows = []
        for row in snapshot.get(axis, ()):
            if not isinstance(row, dict):
                raise ValueError('invalid progress projection')
            if axis in ('accepted_assessment', 'objection', 'question') and row.get('accepted') is not True:
                continue
            projected = {k: deepcopy(row[k]) for k in fields if k in row}
            if projected:
                rows.append(projected)
        unique = {digest(row): row for row in rows}
        result[axis] = digest([unique[k] for k in sorted(unique)])
    return result


def progress_changes(before, after):
    a, b = progress_fingerprints(before), progress_fingerprints(after)
    return {axis: a[axis] != b[axis] for axis in _PROGRESS_FIELDS}


@dataclass(frozen=True)
class RequiredBody:
    source_ref: ExactRef
    required_view: str
    payload_sha256: str
    span: tuple[int, int] | None = None

    def __post_init__(self):
        if self.required_view not in ('body_excerpt', 'full_body'):
            raise ValueError('body obligation required')
        _hash(self.payload_sha256, 'required body payload')
        if not isinstance(self.source_ref, ExactRef):
            raise ValueError('exact body source required')
        if self.span is not None and (not isinstance(self.span, tuple) or len(self.span) != 2
                or any(type(n) is not int for n in self.span)
                or self.span[0] < 0 or self.span[1] <= self.span[0]):
            raise ValueError('invalid exact body span')


@dataclass(frozen=True)
class PresentedBody:
    """Extract from actual compiled input, not from a model-owned manifest."""
    source_ref: ExactRef
    view: str
    body: str | None = field(repr=False)
    span: tuple[int, int] | None = None
    full_body_complete: bool = False


def required_bodies_present(required, presented):
    if not required:
        return False  # Empty/new contract cannot erase the original body need.
    for need in required:
        matched = False
        for body in presented:
            if (body.source_ref != need.source_ref or not isinstance(body.body, str)
                    or not body.body or body.view not in ('body_excerpt', 'full_body')):
                continue
            if need.required_view == 'full_body' and (body.view != 'full_body' or body.full_body_complete is not True):
                continue
            if need.span != body.span:
                continue
            if hashlib.sha256(body.body.encode()).hexdigest() == need.payload_sha256:
                matched = True
                break
        if not matched:
            return False
    return True


def design_condition_satisfied(design, obligation, fence):
    """V2 permits one sided tests, but cannot downgrade the original purpose."""
    from .models import TestDesignV2
    try:
        actual = TestDesignV2.model_validate(design)
    except (ValueError, TypeError):
        return False
    expected_scope = {k: getattr(fence, k) for k in (
        'case_id', 'task_id', 'generation', 'evidence_id', 'source_run')}
    required = obligation.canonical_required
    return (obligation.required_purpose is not None
        and actual.purpose == obligation.required_purpose
        and actual.target_scope.model_dump() == expected_scope
        and actual.owner_ref.model_dump() == asdict(fence.owner_ref)
        and ('target_ref' not in required
            or (actual.target_ref.model_dump() if actual.target_ref else None)
                == required['target_ref'])
        and ('question_ref' not in required
            or actual.question_ref.model_dump() == required['question_ref']))


@dataclass(frozen=True)
class ResolutionProof:
    """Issued after trusted validation AND Controller adoption have committed."""
    fence: ScopeFence
    validation_ref: ExactRef
    accepted_refs: tuple[ExactRef, ...]
    rejected_refs: tuple[ExactRef, ...]
    obligation_digest: str
    input_digest: str
    validator_version: str
    original_condition_before: bool
    original_condition_after: bool
    resolved_obligation_ids: tuple[str, ...]
    remaining_obligation_ids: tuple[str, ...] = ()

    def __post_init__(self):
        if not isinstance(self.fence, ScopeFence) or not isinstance(self.validation_ref, ExactRef):
            raise ValueError('exact validation fence and receipt required')
        for name in ('accepted_refs', 'rejected_refs', 'resolved_obligation_ids', 'remaining_obligation_ids'):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if any(not isinstance(r, ExactRef) for r in self.accepted_refs + self.rejected_refs):
            raise ValueError('exact adoption references required')
        _hash(self.obligation_digest, 'validated obligation')
        _hash(self.input_digest, 'validated input')
        _code(self.validator_version, 'validator version')
        if type(self.original_condition_before) is not bool or type(self.original_condition_after) is not bool:
            raise ValueError('condition observations must be explicit booleans')
        for oid in self.resolved_obligation_ids + self.remaining_obligation_ids:
            _code(oid, 'obligation reference')


class RecoveryRouter:
    def __init__(self, *, enabled=False, budget=None, receipts=(), emit=None):
        if enabled and not isinstance(budget, RecoveryBudget):
            raise ValueError('enabled recovery requires explicit finite limits')
        self.enabled = enabled is True
        self.budget = budget
        self.emit = emit
        self.receipts = deepcopy(list(receipts))
        previous = None
        for sequence, row in enumerate(self.receipts, 1):
            if row.get('version') != VERSION or row.get('receipt_sha256') != digest(
                    {k: v for k, v in row.items() if k != 'receipt_sha256'}) or (
                    row.get('sequence') != sequence or row.get('parent_receipt_sha256') != previous):
                raise ValueError('invalid restored recovery receipt')
            previous = row['receipt_sha256']

    def _record(self, episode, material, status, action, reason, *, counted=False,
                retry_permitted=False, proof=None, changes=None, cost=None):
        previous = self.receipts[-1]['receipt_sha256'] if self.receipts else None
        row = {'version': VERSION, 'sequence': len(self.receipts) + 1,
            'parent_receipt_sha256': previous, 'episode_id': episode.id,
            'budget_key': episode.budget_key, 'fence': asdict(episode.fence),
            'request_attempt_id': episode.request_attempt_id,
            'failure_ref': asdict(episode.failure_ref), 'phase': episode.phase,
            'category': episode.category, 'delivery': episode.delivery,
            'transport_resource': episode.transport_resource,
            'obligation_id': episode.obligation_id, 'obligation_sha256': episode.obligation_digest,
            'before_loop_key': episode.before.loop_key, 'after_loop_key': material.loop_key,
            'before_input_digest': episode.before.input_digest, 'after_input_digest': material.input_digest,
            'target_coordinate_sha256': digest(material.canonical['target_coordinate']),
            'preconditions_sha256': digest(material.canonical['preconditions']),
            'versions': material.canonical['versions'], 'status': status, 'action': action,
            'reason': reason, 'action_counted': counted, 'retry_permitted': retry_permitted,
            'executes_request': False, 'progress_axes': changes or {k: False for k in _PROGRESS_FIELDS},
            'cost': asdict(cost) if cost else None,
            'changed_dimensions': [k for k in ('input_presentation', 'preconditions', 'versions')
                if episode.before.canonical[k] != material.canonical[k]],
            'resolved_obligation_ids': list(proof.resolved_obligation_ids)
                if proof and status == 'resolved_for_scope' else [],
            'remaining_obligation_ids': list(proof.remaining_obligation_ids)
                if proof and status == 'resolved_for_scope' else list(dict.fromkeys(
                    [episode.obligation_id] + (list(proof.remaining_obligation_ids) if proof else []))),
            'validation_ref': asdict(proof.validation_ref) if proof else None,
            'accepted_refs': [asdict(r) for r in proof.accepted_refs] if proof else [],
            'rejected_refs': [asdict(r) for r in proof.rejected_refs] if proof else []}
        row['receipt_sha256'] = digest(row)
        self.receipts.append(row)
        if self.emit:
            self.emit(deepcopy(row))
        return deepcopy(row)

    def _spent(self, episode):
        rows = [r for r in self.receipts if r['budget_key'] == episode.budget_key]
        costs = {}
        for row in rows:
            cost = row.get('cost')
            if cost:
                identity = digest(cost['receipt_ref'])
                if identity in costs and costs[identity] != cost:
                    raise ValueError('contradictory cost receipt reuse')
                costs[identity] = cost
        unknown = any(any(c[k] is None for k in ('model_requests', 'input_tokens',
            'output_tokens', 'wall_seconds')) for c in costs.values())
        return {'actions': sum(r['action_counted'] for r in rows),
            'unchanged_actions': sum(r['action_counted'] and r['before_loop_key'] == r['after_loop_key'] for r in rows),
            'model_requests': sum(c['model_requests'] or 0 for c in costs.values()),
            'total_tokens': sum((c['input_tokens'] or 0) + (c['output_tokens'] or 0) for c in costs.values()),
            'wall_seconds': sum(c['wall_seconds'] or 0 for c in costs.values()),
            'cost_unknown': unknown, 'terminal': any(r['status'] == 'terminated' or (
                r['status'] == 'resolved_for_scope' and r['episode_id'] == episode.id) for r in rows)}

    def route(self, episode, *, fence, material=None, execution=None, cost=None,
              before_progress=None, after_progress=None, design=None,
              required_bodies=(), presented_bodies=()):
        """Propose local repair only. No alternate retry of arbitrary failures."""
        material = material or episode.before
        if not self.enabled:
            return {'version': VERSION, 'status': 'disabled', 'executes_request': False,
                    'retry_permitted': False}
        if fence != episode.fence:
            return self._record(episode, material, 'scope_rejected', 'none', 'stale_exact_scope')
        if (material.obligation.id != episode.obligation_id
                or material.obligation.sha256 != episode.obligation_digest
                or material.canonical['target_coordinate'] != episode.before.canonical['target_coordinate']):
            return self._record(episode, material, 'scope_rejected', 'none', 'original_obligation_or_target_changed')
        if episode.category not in SUPPORTED:
            return self._record(episode, material, 'deferred', 'existing_typed_path', 'not_a_router_branch', cost=cost)
        if episode.delivery != 'not_sent' and (not isinstance(execution, ExecutionObservation) or not execution.matches(episode)):
            return self._record(episode, material, 'reconciliation_required', 'reconcile_execution',
                'actual_execution_not_reconciled', cost=cost)
        if cost:
            self._record(episode, material, 'cost_observed', 'none', 'actual_cost_receipt', cost=cost)
        spent = self._spent(episode)
        b = self.budget
        exhausted = (spent['terminal'] or spent['actions'] >= b.max_actions
            or spent['model_requests'] >= b.max_model_requests
            or spent['total_tokens'] >= b.max_total_tokens
            or spent['wall_seconds'] >= b.max_wall_seconds)
        if exhausted:
            return self._record(episode, material, 'terminated', 'preserve_gap', 'finite_recovery_budget_exhausted')
        unchanged = material.loop_key == episode.before.loop_key
        if episode.category == 'unchanged_invalid_input' or (
                unchanged and spent['unchanged_actions'] >= b.max_unchanged_actions):
            status = 'terminated' if spent['unchanged_actions'] else 'isolated'
            return self._record(episode, material, status, 'isolate_identical_loop',
                'no_changed_observable_or_precondition', counted=True)
        action = ('redesign_purpose_and_one_sided_conditions'
            if episode.category == 'empty_discriminating_condition' else 'complete_required_input_view')
        changes = progress_changes(before_progress or {}, after_progress or {})
        changed_requirement = (design_condition_satisfied(design, episode.before.obligation, fence)
            if episode.category == 'empty_discriminating_condition' else
            changes['input'] and required_bodies_present(required_bodies, presented_bodies))
        # IDs/rephrasing/versions alone cannot authorize a retry. A true flag
        # is only eligible for existing admission, never execution authority.
        return self._record(episode, material, 'repair_proposed', action,
            'original_obligation_still_requires_validated_adoption', counted=True,
            retry_permitted=not unchanged and changed_requirement and not spent['cost_unknown'],
            changes=changes)

    def verify(self, episode, *, fence, material, proof, execution=None, before_progress=None,
               after_progress=None, design=None, required_bodies=(), presented_bodies=()):
        if not self.enabled:
            return {'version': VERSION, 'status': 'disabled', 'executes_request': False,
                    'retry_permitted': False}
        if episode.delivery != 'not_sent' and (not isinstance(execution, ExecutionObservation)
                or not execution.matches(episode)):
            return self._record(episode, material, 'reconciliation_required', 'reconcile_execution',
                'actual_execution_not_reconciled')
        bound = (fence == episode.fence and isinstance(proof, ResolutionProof)
            and proof.fence == episode.fence and proof.obligation_digest == episode.obligation_digest
            and material.obligation.id == episode.obligation_id
            and material.obligation.sha256 == episode.obligation_digest
            and material.canonical['target_coordinate'] == episode.before.canonical['target_coordinate']
            and proof.input_digest == material.input_digest
            and proof.validator_version == material.validator_version
            and proof.original_condition_before is False and proof.original_condition_after is True
            and episode.obligation_id in proof.resolved_obligation_ids
            and episode.obligation_id not in proof.remaining_obligation_ids
            and bool(proof.accepted_refs) and not set(proof.accepted_refs) & set(proof.rejected_refs))
        if not bound:
            return self._record(episode, material, 'unresolved', 'preserve_gap',
                'validation_or_adoption_binding_missing', proof=proof if isinstance(proof, ResolutionProof) else None)
        condition = (design_condition_satisfied(design, episode.before.obligation, fence)
            if episode.category == 'empty_discriminating_condition' else
            required_bodies_present(required_bodies, presented_bodies)
            if episode.category == 'required_body_not_presented' else
            material.loop_key != episode.before.loop_key
            if episode.category == 'unchanged_invalid_input' else False)
        changes = progress_changes(before_progress or {}, after_progress or {})
        adoption_bound = any(row.get('accepted') is True
            and row.get('target_coordinate') == material.canonical['target_coordinate']
            and row.get('ref') in [asdict(r) for r in proof.accepted_refs]
            for row in (after_progress or {}).get('accepted_assessment', ()))
        if (not condition or not changes['accepted_assessment'] or not adoption_bound
                or material.loop_key == episode.before.loop_key):
            return self._record(episode, material, 'unresolved', 'preserve_gap',
                'original_condition_not_resolved_by_accepted_assessment', changes=changes, proof=proof)
        return self._record(episode, material, 'resolved_for_scope', 'validated_adoption',
            'original_obligation_condition_resolved', proof=proof, changes=changes)
