"""Controller-owned, content-free observations of one model request attempt.

These records are execution telemetry, not evidence or judgment revisions. The
Provider receives only ``emit`` and an opaque attempt ID; it never owns a Store.
No transport event is inferred when a legacy/mock Provider omits the callback.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import inspect
import json
import re
import threading
from uuid import uuid4

from .store import now

VERSION = 'model-request-lifecycle-1'
PHASES = frozenset(('input_registered', 'input_preparing', 'input_ready',
    'availability_check', 'availability_verified', 'resource_queued',
    'resource_acquired', 'dispatch_attempted', 'response_waiting',
    'delivery_unknown', 'response_received', 'validating', 'validated_output',
    'page_accepted', 'accepted', 'partial_accepted', 'failed', 'rejected', 'ended'))
DELIVERY = frozenset(('not_sent', 'attempted', 'unknown', 'response_received'))
_HASH = re.compile(r'^[a-f0-9]{64}$')
_CODE = re.compile(r'^[A-Za-z0-9_.:-]{1,96}$')
_NUMERIC = frozenset(('http_status', 'response_bytes', 'prompt_characters',
    'output_characters', 'message_count', 'observation_count', 'dossier_count',
    'check_count', 'elapsed_seconds'))
_USAGE = frozenset(('prompt_eval_count', 'eval_count', 'total_duration',
    'load_duration', 'prompt_eval_duration', 'eval_duration', 'prompt_tokens',
    'completion_tokens', 'total_tokens', 'cached_tokens', 'reasoning_tokens',
    'input_tokens', 'output_tokens', 'cached_input_tokens'))
_CODES = frozenset(('failure_category', 'failure_phase', 'validation_stage', 'outcome',
    'adoption_scope', 'model_slot', 'role', 'dispatch_boundary'))
_REF_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,180}$')
_BUDGET = frozenset(('prompt_characters', 'prompt_utf8_bytes', 'estimated_input_tokens',
    'request_characters', 'request_utf8_bytes',
    'context_tokens_requested', 'output_tokens_reserved', 'estimated_headroom',
    'exact_input_tokens', 'server_retained_full_input_verified', 'actual_output_tokens',
    'actual_headroom'))


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), default=str).encode()).hexdigest()


def reference(record):
    """An exact retained object/version, never a title or an inferred relation."""
    return {'kind': record['kind'], 'id': record['id'], 'version': _digest(record)}


def safe_metadata(values):
    """Allow numeric counters, digests and fixed machine codes, never prose."""
    safe = {}
    for key, value in values.items():
        if key in _NUMERIC and type(value) in (int, float) and value >= 0:
            safe[key] = value
        elif key in _CODES and isinstance(value, str) and _CODE.fullmatch(value):
            safe[key] = value
        elif key in ('request_sha256', 'response_sha256', 'transport_identity',
                     'model_sha256', 'input_sha256') and isinstance(value, str) and _HASH.fullmatch(value):
            safe[key] = value
        elif key=='request_attempted' and value is None:
            safe[key]=None
        elif key in ('request_attempted', 'retryable', 'identity_verified', 'process_started') and type(value) is bool:
            safe[key] = value
        elif key == 'usage' and isinstance(value, dict):
            # OpenAI's detail maps are counters, not text. Flatten only known
            # accounting keys; arbitrary adapter response metadata is excluded.
            safe[key] = {k: v for k, v in value.items()
                if k in _USAGE and type(v) in (int, float) and v >= 0}
            for detail in ('prompt_tokens_details', 'completion_tokens_details'):
                if isinstance(value.get(detail), dict):
                    safe[key].update({k: v for k, v in value[detail].items()
                        if k in _USAGE and type(v) in (int, float) and v >= 0})
        elif key == 'prompt_budget' and isinstance(value, dict):
            safe[key] = {k: v for k, v in value.items()
                if k in _BUDGET and (type(v) in (int, float, bool) or v is None)}
            if value.get('count_basis') in ('heuristic','exact_tokenizer'):
                safe[key]['count_basis']=value['count_basis']
            if value.get('count_scope')=='full_messages_with_embedded_schema_and_template_reserve':
                safe[key]['count_scope']=value['count_scope']
            for version in ('tokenizer_version','server_template_version'):
                if version in value and value[version] is None:safe[key][version]=None
            if isinstance(value.get('template_sha256'),str) and _HASH.fullmatch(value['template_sha256']):
                safe[key]['template_sha256']=value['template_sha256']
        elif key == 'input_manifest' and isinstance(value, dict):
            safe[key] = {'source_refs': [], 'owner_ids': [], 'test_ids': []}
            for ref in value.get('source_refs', []):
                if (isinstance(ref, dict) and ref.get('kind') == 'observation'
                    and isinstance(ref.get('id'), str) and _REF_ID.fullmatch(ref['id'])
                    and isinstance(ref.get('version'), str) and _HASH.fullmatch(ref['version'])):
                    safe[key]['source_refs'].append({k: ref[k] for k in ('kind', 'id', 'version')})
            for ids in ('owner_ids', 'test_ids'):
                safe[key][ids] = [x for x in value.get(ids, [])
                    if isinstance(x, str) and _REF_ID.fullmatch(x)]
    return safe


class RequestAttempt:
    def __init__(self, store, case_id, task, *, role, input_record=None,
                 owner_records=(), evidence_id=None, reservation_id=None,
                 logical_work_id=None):
        self.store = store
        self.case_id = case_id
        self.task_id = task['id']
        self.generation = task.get('retry_generation', 0)
        self.role = role
        self.id = 'MODEL_ATTEMPT-' + uuid4().hex
        self.input_record_id = input_record['id'] if input_record else None
        self.input_ref = reference(input_record) if input_record else None
        self.owner_refs = [reference(row) for row in owner_records]
        self.evidence_id = evidence_id or task.get('evidence_id')
        self.reservation_id = reservation_id
        self.logical_work_id = logical_work_id or (input_record or {}).get('input_sha256')
        self.delivery_state = 'not_sent'
        self.seq = 0
        self.ended = False
        self._lock = threading.RLock()
        # Attempts on a regenerated task are not retries of older generations.
        prior = [r for r in store.list('request_lifecycle', case_id)
            if r.get('task_id') == self.task_id and r.get('generation') == self.generation
            and r.get('role') == role
            and self.logical_work_id is not None and r.get('logical_work_id') == self.logical_work_id
            and {(x.get('kind'), x.get('id')) for x in r.get('owner_refs', [])}
                == {(x['kind'], x['id']) for x in self.owner_refs}]
        self.retry_of_attempt_id = prior[-1]['attempt_id'] if prior else None
        self.emit('input_registered', input_sha256=(input_record or {}).get('input_sha256'))

    def emit(self, phase, *, delivery_state=None, affected_refs=None,
             accepted_refs=None, rejected_refs=None, **metadata):
        if phase not in PHASES:
            raise ValueError('unsupported request lifecycle phase')
        with self._lock:
            if self.ended:
                raise ValueError('request attempt already ended')
            if delivery_state is not None:
                if delivery_state not in DELIVERY:
                    raise ValueError('unsupported request delivery state')
                # A fully received response must never regress to "not sent".
                if self.delivery_state == 'response_received' and delivery_state != 'response_received':
                    raise ValueError('request delivery state regressed')
                self.delivery_state = delivery_state
            self.seq += 1
            record = self.store.add('request_lifecycle', self.case_id,
                lifecycle_version=VERSION, event_id='MODEL_EVENT-' + uuid4().hex,
                attempt_id=self.id, seq=self.seq, observed_at=now(), phase=phase,
                delivery_state=self.delivery_state, task_id=self.task_id,
                generation=self.generation, role=self.role, evidence_id=self.evidence_id,
                input_record_id=self.input_record_id, input_ref=deepcopy(self.input_ref),
                reservation_id=self.reservation_id, owner_refs=deepcopy(self.owner_refs),
                logical_work_id=self.logical_work_id,
                retry_of_attempt_id=self.retry_of_attempt_id,
                affected_refs=deepcopy(self.owner_refs if affected_refs is None else affected_refs),
                accepted_refs=deepcopy(accepted_refs or []), rejected_refs=deepcopy(rejected_refs or []),
                metadata=safe_metadata(metadata))
            if phase == 'ended':
                self.ended = True
            return record

    def fail(self, error, *, rejected_refs=None, outcome='failed'):
        metadata = getattr(error, 'metadata', {})
        delivery = metadata.get('delivery_state')
        if delivery == 'unknown' and self.delivery_state != 'response_received':
            self.emit('delivery_unknown', delivery_state='unknown')
        # A trusted adapter may identify a discovery/authentication rejection
        # before API delivery even though its local process was already started.
        confirmed_not_sent=delivery=='not_sent' and metadata.get('request_attempted') is False and self.delivery_state!='response_received'
        self.emit('failed', rejected_refs=rejected_refs,
            delivery_state='not_sent' if confirmed_not_sent else
                'response_received' if delivery=='response_received' else None,
            failure_category=getattr(error, 'category', 'controller_validation'),
            failure_phase=metadata.get('phase'),
            request_attempted=metadata.get('request_attempted'), retryable=metadata.get('retryable'))
        self.finish(outcome)

    def consult(self, function, *args, **kwargs):
        """Pass telemetry only to an explicitly compatible call boundary.

        Older plugins and test doubles may not expose these keywords. Their
        return values do not manufacture Provider-side execution observations.
        Never catch TypeError and retry a potentially transmitted request.
        """
        try:
            parameters=inspect.signature(function).parameters
            supports=any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values()) or (
                'attempt' in parameters and 'emit' in parameters)
        except (TypeError,ValueError):
            supports=False
            parameters={}
        if ('compiled_request' in kwargs and 'compiled_request' not in parameters
            and not any(p.kind is inspect.Parameter.VAR_KEYWORD for p in parameters.values())):
            # A legacy double has no compiled transport boundary to bind. It
            # still executes once, without manufactured dispatch observations.
            kwargs.pop('compiled_request')
        if supports:kwargs.update(attempt=self.id,emit=self.emit)
        return function(*args,**kwargs)

    def accept(self, records, *, rejected_records=(), scope='review', phase='accepted'):
        self.emit(phase, accepted_refs=[reference(row) for row in records],
            rejected_refs=[reference(row) for row in rejected_records], adoption_scope=scope)
        self.finish('partial_accepted' if phase == 'partial_accepted' else
            'page_accepted' if phase == 'page_accepted' else 'accepted')

    def finish(self, outcome):
        self.emit('ended', outcome=outcome)

    @contextmanager
    def finalizing(self):
        """Close Controller adoption after its transaction commits or rolls back.

        Enter before the adoption transaction so exception telemetry survives
        its rollback. This observes failure without swallowing it or retrying.
        """
        try:
            yield
        except BaseException as error:
            if not self.ended:
                self.fail(error,outcome='controller_exception')
            raise
        finally:
            if not self.ended:
                self.finish('controller_exit_without_adoption')
