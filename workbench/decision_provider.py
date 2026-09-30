"""Optional decision provider shell. D1 has no live transport or policy hook."""
from typing import Protocol

from .decision_capabilities import runtime_support
from .decision_contracts import DecisionConnection, DecisionRequest, digest, unavailable
from .openjev_readout import ReadoutUnsupported, compile_choice, evaluate_fixture


class SharedResourceBroker(Protocol):
    """Future broker must serialize all processes/models and retain unknown jobs.

    An in-process semaphore for two model identities does not satisfy this
    interface's deployment contract. No DB transaction may be held while waiting.
    """
    process_shared: bool
    execution_slots: int
    retains_unknown_delivery: bool

    def lease(self, resource_group_id: str, attempt_id: str): ...


class UnsupportedResourceBroker:
    process_shared = False
    execution_slots = 0
    retains_unknown_delivery = False

    def lease(self, resource_group_id, attempt_id):
        raise ReadoutUnsupported('shared_resource_broker_not_verified')


class DecisionProvider:
    def __init__(self, connection=None):
        self.connection = DecisionConnection.model_validate(connection or {})

    def decide(self, request, profile, *, observation=None, broker=None):
        """Recordable unavailability, not a silent generative-model substitution."""
        request = DecisionRequest.model_validate(request)
        if self.connection.mode == 'disabled':
            return unavailable(request, 'decision_disabled', provenance='disabled')
        supported, reason = runtime_support(self.connection, request, profile, observation)
        if not supported:
            return unavailable(request, reason)
        # No certificate issuer, live HTTP client or routing capability is
        # shipped at D1. Keeping this last guard explicit prevents accidental
        # activation if a caller later changes a capability predicate alone.
        return unavailable(request, 'D1_live_transport_not_implemented')

    def decide_fixture(self, request, profile, tokenizer, response):
        """Explicit offline fixtures only. Never called by scheduler/UI polling."""
        request = DecisionRequest.model_validate(request)
        try:
            compiled = compile_choice(request, profile, tokenizer)
        except (ReadoutUnsupported, ValueError, TypeError, AttributeError) as ex:
            return unavailable(request, str(ex), provenance='fixture')
        return evaluate_fixture(request, compiled, response)


def applicable_advice(result, current_request, *, expected_cache_key=None):
    """Version gate for future callers; D1 cannot apply even fresh fixture advice."""
    current_request = DecisionRequest.model_validate(current_request)
    if result.request_id != current_request.request_id or result.request_digest != digest(current_request):
        return {'applicable': False, 'reason': 'late_or_changed_request', 'chosen_id': None}
    if result.model_identity != current_request.model_identity:
        return {'applicable': False, 'reason': 'changed_model_identity', 'chosen_id': None}
    if expected_cache_key is not None and result.cache_key != expected_cache_key:
        return {'applicable': False, 'reason': 'changed_compiled_input', 'chosen_id': None}
    return {'applicable': False, 'reason': 'D1_advisory_only', 'chosen_id': None}
