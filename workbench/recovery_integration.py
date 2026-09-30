"""Opt-in local admission hooks for the minimal RecoveryRouter.

The hook does not execute a tool/model, replace a contract, or infer adoption.
It adds exact repair feedback to the already blocked intent. Subsequent ordinary
planning/admission may redesign a test. All original obligations stay open.
Retained body repair only copies exact current observations into an existing
pack; the usual compiler, budget, allowlist and validator still apply.
"""
from contextlib import nullcontext
from copy import deepcopy
import hashlib
import json

from .recovery_router import (ExactRef, FailureEpisode, LoopMaterial, OriginalObligation,
    RecoveryBudget, RecoveryRouter, ScopeFence, digest)
from .request_lifecycle import reference
from .retrieval import fingerprint_scope
from .test_contract_v2 import POLICY as VALIDATOR_VERSION, UNSUPPORTED, VIEWS, object_ref, object_version, presented_view

POLICY = 'bounded-v1'
VERSION = 'admission-recovery-1'
# Local action reservations only; these are caps, not additional model credits.
# Every future actual request must also pass the unchanged lifetime budgets.
LIMITS = RecoveryBudget(max_actions=2, max_model_requests=1,
    max_total_tokens=32768, max_wall_seconds=300)


def enabled(task):
    return task.get('recovery_policy', 'disabled') == POLICY


def _exact(value):
    return ExactRef(**{k: value[k] for k in ('kind', 'id', 'version')})


def _body(row, view):
    fields = row.get('fields') or {}
    value = fields.get('excerpt')
    if not isinstance(value, str) or not value:
        return None
    if view == 'full_body' and (fields.get('source_complete') is not True
            or fields.get('excerpt_truncated') or fields.get('excerpt_spans') or row.get('source_span')):
        return None
    return value


def _original_body_needs(store, cid, evidence, design, context):
    needs = []
    for required in design.get('required_inputs', []):
        if required.get('required_view') not in ('body_excerpt', 'full_body'):
            continue
        offered = next((o for o in context.get('objects', []) if o.get('ref') == required.get('ref')), None)
        if not offered or VIEWS.get(offered.get('view'), -1) >= VIEWS[required['required_view']]:
            continue
        try:
            row = store.get(required['ref']['id'], 'observation')
        except (ValueError, TypeError, KeyError):
            return []
        if (row.get('case_id') != cid or row.get('evidence_id') != evidence['id']
                or object_ref(row) != required['ref']):
            return []
        value = _body(row, required['required_view'])
        if value is None:
            return []  # Missing acquired material is not a presentation repair.
        needs.append({'ref': required['ref'], 'required_view': required['required_view'],
            'payload_sha256': hashlib.sha256(value.encode()).hexdigest()})
    return needs


def _presentation(source):
    from .review_stream import resolved
    pack = source.get('pack') or {}
    shown = resolved(pack)
    return {'sources': [{'id': row['id'], 'fields_sha256': digest(row.get('fields') or {}),
        'source_span_sha256': digest(row.get('source_span')),
        'view': presented_view(row)} for row in shown.get('observations', [])]}


def admission_failure(store, cid, task, evidence, source_run, request, intent, source):
    """Only typed, positive failures enter the three-branch router.

    Caller invokes after normal V2 admission. Missing/stale refs, capability,
    schema, locator and service failures continue their existing typed paths.
    Local admission has not dispatched this tool; it is not a model transport
    observation. No request delivery is inferred from a Codex adapter process.
    """
    if not enabled(task) or intent.get('admission', {}).get('eligible') is not False:
        return None
    try:
        current_task = store.get(task['id'], 'task')
        current_evidence = store.get(evidence['id'], 'evidence')
    except (ValueError, KeyError, TypeError):
        return None
    if (current_task.get('case_id') != cid or current_evidence.get('case_id') != cid
            or current_task.get('retry_generation', 0) != task.get('retry_generation', 0)
            or current_task.get('status') not in (None, 'queued', 'running')
            or not current_evidence.get('connected', True)
            or current_evidence.get('signature') != evidence.get('signature')):
        return None
    admission = intent['admission']
    reason, design = admission.get('reason'), request.get('test_design') or {}
    context = (source or {}).get('test_contract_context', ((source or {}).get('pack') or {}).get('test_contract_context'))
    if (not source or not context or source.get('case_id') != cid
            or source.get('task_id') != task['id'] or source.get('generation', 0) != task.get('retry_generation', 0)
            or context.get('scope') != {'case_id': cid, 'task_id': task['id'], 'evidence_id': evidence['id'],
                'generation': task.get('retry_generation', 0), 'source_run': source_run}):
        return None
    offered = {digest(o['ref']): o for o in context.get('objects', []) if isinstance(o.get('ref'), dict)}
    refs = [design.get('owner_ref'), design.get('question_ref')]
    if design.get('target_ref') is not None:refs.append(design['target_ref'])
    if not all(isinstance(r, dict) and digest(r) in offered for r in refs):
        return None
    if (design.get('target_scope') != context['scope']
            or (design.get('question_ref') or {}).get('id') != request.get('question_id')
            or (request.get('hypothesis_id') and design['owner_ref']['id'] != request['hypothesis_id'])
            or design.get('purpose') in ('discriminate', 'verify_reliability') and (
                design.get('target_ref') is None or not design.get('target_proposition'))):
        return None
    for r in refs:
        try: current = store.get(r['id'], r['kind'])
        except (ValueError, KeyError, TypeError):return None
        if current.get('case_id') != cid or object_ref(current) != r:return None
    rules = design.get('outcome_rules') or {}
    empty = (reason == 'invalid_test_design'
        and design.get('purpose') in ('discriminate', 'verify_reliability')
        and rules.get('supports') in ('', UNSUPPORTED)
        and rules.get('refutes') in ('', UNSUPPORTED))
    needs = _original_body_needs(store, cid, evidence, design, context) if reason == 'presentation_requirement_unmet' else []
    if not empty and not needs:
        return None
    category = 'empty_discriminating_condition' if empty else 'required_body_not_presented'
    physical = fingerprint_scope(request)
    stable = {'owner_id': design['owner_ref']['id'], 'question_id': design['question_ref']['id'],
        'target_id': (design.get('target_ref') or {}).get('id'), 'purpose': design.get('purpose'),
        'physical': physical, 'body_needs': [{'id': r['ref']['id'], 'view': r['required_view']} for r in needs]}
    obligation = OriginalObligation('OBLIGATION-' + digest(stable), {
        'goal': 'valid_original_test_admission', 'question_ref': design['question_ref'],
        'target_ref': design.get('target_ref'), 'required_body_refs': needs,
        'original_source_record_ref': reference(source)}, required_purpose=design.get('purpose'))
    material = LoopMaterial(obligation, physical, _presentation(source),
        'retained-source-resolution-1', 'tool-scope-v2', VALIDATOR_VERSION, VERSION,
        preconditions={'original_failure': category, 'required_body_refs': needs,
            'allowed_outcomes': design.get('allowed_outcomes'), 'outcome_rules': rules})
    fence = ScopeFence(cid, task['id'], task.get('retry_generation', 0), evidence['id'],
        source_run, _exact(reference(evidence)), _exact(design['owner_ref']))
    episode = FailureEpisode(fence, 'LOCAL_ADMISSION-' + intent['id'], obligation.id, obligation.sha256,
        'tool_admission', category, 'not_sent', 'local-tool-admission', material, _exact(reference(intent)))
    with store.lock, (nullcontext() if store.db.in_transaction else store.tx()):
        prior = [r['recovery'] for r in store.list('receipt', cid)
                 if r.get('receipt_type') == 'recovery_router']
        # Full original/source refs remain immutable in this local context;
        # cost/content-free routing metadata references it rather than prose.
        context_receipt = store.add('receipt', cid, receipt_type='recovery_obligation',
            task_id=task['id'], evidence_id=evidence['id'], generation=task.get('retry_generation', 0),
            source_run=source_run, original_intent_id=intent['id'], source_record_ref=reference(source),
            obligation_id=obligation.id, obligation_digest=obligation.sha256,
            owner_ref=design['owner_ref'], original_design=deepcopy(design), required_body_refs=needs,
            target_coordinate=physical, recovery_version=VERSION,
            scope='Unresolved original obligation; new contract or view repair alone is not recovered.')
        r = RecoveryRouter(enabled=True, budget=LIMITS, receipts=prior)
        result = r.route(episode, fence=fence)
        receipt = store.add('receipt', cid, receipt_type='recovery_router', task_id=task['id'],
            evidence_id=evidence['id'], generation=task.get('retry_generation', 0),
            obligation_context_id=context_receipt['id'], original_intent_id=intent['id'], recovery=result)
        return {'version': VERSION, 'receipt_id': receipt['id'], 'context_id': context_receipt['id'],
            'status': result['status'], 'action': result['action'], 'retry_permitted': False,
            'original_obligation_id': obligation.id, 'original_obligation_resolved': False,
            'instruction': ('Redesign purpose and an actually available one-sided discriminator. '
                'Unsupported sides must remain unsupported; a separate discovery contract does not resolve the original question.'
                if empty else 'Retained body is available but was not actually presented. '
                'Complete this input view before retrying; hashes or filenames are not body evidence.')}


def supply_pending_body_views(pack, store, cid, task, evidence, source_run):
    """Copy only exact retained body into an already offered observation.

    Invoke before normal fit/rebind. This neither forces budget acceptance nor
    grants full-body status, and does not append hidden/new source membership.
    An exact post-fit view check remains necessary before any model request.
    """
    if not enabled(task):
        return pack
    rows = {o['id']: o for o in pack.get('observations', [])}
    for receipt in store.list('receipt', cid):
        if (receipt.get('receipt_type') != 'recovery_obligation'
                or receipt.get('task_id') != task['id'] or receipt.get('evidence_id') != evidence['id']
                or receipt.get('generation', 0) != task.get('retry_generation', 0)
                or receipt.get('source_run') != source_run):
            continue
        for need in receipt.get('required_body_refs', []):
            ref = need['ref']
            if ref['id'] not in rows:
                continue
            try: canonical = store.get(ref['id'], 'observation')
            except ValueError:continue
            if (canonical.get('case_id') != cid or canonical.get('evidence_id') != evidence['id']
                    or object_version(canonical) != ref['version']):
                continue
            value = _body(canonical, need['required_view'])
            if value is None or hashlib.sha256(value.encode()).hexdigest() != need['payload_sha256']:
                continue
            shown = rows[ref['id']]
            # Do not overwrite paged coordinates or transform a span into a
            # whole field. The existing page scheduler owns those boundaries.
            if shown.get('source_span') or (shown.get('fields') or {}).get('excerpt_spans'):
                continue
            shown.setdefault('fields', {})['excerpt'] = value
    return pack


def rejected_schema_hints(store, cid, task, evidence, source_run, input_record, error, diagnostic_id):
    """Preserve exact repair hints when V2 validation rejects before reserve.

    The rejected JSON is never adopted, dispatched, or used as evidence. A
    one-sided *shape probe* only establishes that the blank discriminator is
    the isolated defect; its invented condition is never persisted/returned.
    Normal admission with that probe must still pass source, locator and
    capability gates before the original invalid request gets a blocked intent.
    No alternate request is sent and the existing repair-attempt limit applies.
    """
    if (not enabled(task) or task.get('test_contract_policy') != 'purpose-outcomes-v2'
            or getattr(error, 'category', None) != 'output_schema'):
        return []
    raw = getattr(error, 'raw_output', None)
    if not isinstance(raw, str) or len(raw) > 100000:
        return []
    def exact_object(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Ambiguous duplicate JSON field')
            value[key] = item
        return value
    try:
        rejected = json.loads(raw, object_pairs_hook=exact_object)
        source = store.get(input_record['id'], 'review_input')
        diagnostic = store.get(diagnostic_id, 'review_diagnostic')
        current_task = store.get(task['id'], 'task')
        current_evidence = store.get(evidence['id'], 'evidence')
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return []
    if (not isinstance(rejected, dict) or not isinstance(rejected.get('next_checks'), list)
            or not 0 < len(rejected['next_checks']) <= 4
            or source != input_record or source.get('case_id') != cid
            or source.get('task_id') != task['id'] or source.get('evidence_id', evidence['id']) != evidence['id']
            or source.get('generation', 0) != task.get('retry_generation', 0)
            or diagnostic.get('case_id') != cid or diagnostic.get('task_id') != task['id']
            or diagnostic.get('generation', 0) != task.get('retry_generation', 0)
            or diagnostic.get('input_record_id') != source['id']
            or diagnostic.get('raw_output') != raw
            or current_task.get('case_id') != cid or current_task.get('evidence_id') != evidence['id']
            or current_evidence.get('case_id') != cid
            or current_task.get('retry_generation', 0) != task.get('retry_generation', 0)
            or current_task.get('status') not in (None, 'queued', 'running')
            or not current_evidence.get('connected', True)
            or current_evidence.get('signature') != evidence.get('signature')):
        return []
    context = (source.get('pack') or {}).get('test_contract_context')
    scope = {'case_id': cid, 'task_id': task['id'], 'evidence_id': evidence['id'],
        'generation': task.get('retry_generation', 0), 'source_run': source_run}
    if not context or context.get('scope') != scope:
        return []
    from .models import JudgmentCheckV2
    from .test_admission import assess
    from .test_contract_v2 import current_manifest
    from .locator_admission import trusted_source_locators
    from . import question_engine
    available = [o['id'] for o in store.list('observation', cid)
                 if o.get('evidence_id') == evidence['id']]
    locators = trusted_source_locators(store, cid, evidence['id'], source_run)
    hints = []
    for original in rejected['next_checks']:
        if not isinstance(original, dict):
            continue
        design = original.get('test_design')
        rules = (design or {}).get('outcome_rules') if isinstance(design, dict) else None
        if (not isinstance(rules, dict) or design.get('purpose') not in ('discriminate', 'verify_reliability')
                or rules.get('supports') not in ('', UNSUPPORTED)
                or rules.get('refutes') not in ('', UNSUPPORTED)):
            continue
        probe = deepcopy(original)
        # Repair these two blank slots only to validate the rest of this one
        # rejected object's shape. The probe is not a candidate test design.
        probe_rules = probe['test_design']['outcome_rules']
        probe_rules.update(supports='Controller shape-only probe, never executed or adopted.', refutes=UNSUPPORTED)
        allowed = probe['test_design'].get('allowed_outcomes')
        if not isinstance(allowed, list) or any(not isinstance(o, str) for o in allowed) or len(set(allowed)) != len(allowed):
            continue
        probe['test_design']['allowed_outcomes'] = [o for o in allowed if o not in ('supports', 'refutes')] + ['supports']
        try:
            JudgmentCheckV2.model_validate(probe)
            feasible = assess(probe, store.get(cid).get('target_os', 'linux'), available,
                source_locators=locators, policy='purpose-outcomes-v2', context=context,
                current_context=current_manifest(store, cid, context))
        except (ValueError, KeyError, TypeError):
            continue
        if not feasible.get('eligible'):
            continue  # Other typed failures must follow their existing path.
        question_ref = design.get('question_ref') or {}
        try:
            question = store.get(question_ref['id'], 'case_question')
        except (ValueError, KeyError, TypeError):
            continue
        intent = question_engine.reserve(store, cid, task, question, original, evidence,
            source_run, source_record_id=source['id'])
        recovery = intent.get('admission', {}).get('recovery')
        if (intent.get('status') != 'blocked' or intent.get('admission', {}).get('eligible') is not False
                or not recovery):
            continue
        hints.append({'code': 'empty_discriminating_condition', 'test_intent_id': intent['id'],
            'recovery_receipt_id': recovery['receipt_id'], 'status': recovery['status'],
            'original_obligation_id': recovery['original_obligation_id'],
            'original_obligation_resolved': False, 'instruction': recovery['instruction']})
    return hints


def bind_recovery_feedback(feedback, hints):
    """Add bounded non-evidentiary feedback inside the existing reservation."""
    if not hints:
        return feedback
    from .review_diagnostics import FEEDBACK_LIMIT
    result = deepcopy(feedback)
    result['recovery_hints'] = []
    result['recovery_hints_omitted'] = len(hints)
    if len(json.dumps(result, ensure_ascii=False, separators=(',', ':'))) > FEEDBACK_LIMIT:
        return feedback  # The immutable diagnostic still retains repair refs.
    for hint in hints:
        candidate = deepcopy(result)
        candidate['recovery_hints'].append(hint)
        candidate['recovery_hints_omitted'] -= 1
        if len(json.dumps(candidate, ensure_ascii=False, separators=(',', ':'))) <= FEEDBACK_LIMIT:
            result = candidate
    return result
