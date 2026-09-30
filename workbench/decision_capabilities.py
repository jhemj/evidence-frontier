"""Passive D1 capability checks. No probe execution or certificate issuance.

Documentation support and synthetic score fixtures are not installation proof.
Future attested probes must bind weights/quantization, template/tokenizer, runner,
score semantics and process-shared resource ownership before any live request.
"""
from typing import Literal

from pydantic import Field, StrictBool

from .decision_contracts import Contract, ModelIdentity, ReadoutProfile, digest

OFFICIAL_HELPER_SOURCE = 'https://huggingface.co/openjev/openjev/commit/95f6b055ff2ec5388b102c74a61f66f56f16381b'
OFFICIAL_SERVING_SOURCE = 'https://huggingface.co/openjev/openjev/raw/main/serve/SERVE.md'
OLLAMA_READOUT_SOURCE = 'https://docs.ollama.com/api/generate'
OLLAMA_API_SOURCE = 'https://github.com/ollama/ollama/blob/main/api/types.go'

REQUIRED_CHECKS = (
    'local_weights_identity', 'exact_single_candidate_tokens', 'unique_candidate_token_ids',
    'chat_template_generation_prefix', 'thinking_disabled', 'first_output_position',
    'exact_token_bytes_mapping', 'complete_candidate_scores', 'pre_sampler_score_semantics',
    'complete_input_no_truncation', 'runtime_quantization_reproducibility',
    'shared_resource_serialization', 'unknown_delivery_resource_retention', 'rights_review',
)


class CapabilityObservation(Contract):
    model_identity: ModelIdentity
    readout_profile: ReadoutProfile
    origin: Literal['documentation', 'fixture', 'installed_probe']
    checks: dict[str, StrictBool | None] = Field(default_factory=dict)
    probe_record_refs: tuple[str, ...] = ()


class CapabilityReport(Contract):
    profile_fingerprint: str
    origin: str
    checks_passed: tuple[str, ...]
    missing_checks: tuple[str, ...]
    unknown_checks: tuple[str, ...]
    unsupported_checks: tuple[str, ...]
    capability_certificate_id: None = None
    runtime_supported: Literal[False] = False
    reason: str


def inspect_capabilities(observation):
    """Inspect supplied observations, never manufacture an installed certificate."""
    value = CapabilityObservation.model_validate(observation)
    unsupported = tuple(k for k in REQUIRED_CHECKS if value.checks.get(k) is False)
    unknown = tuple(k for k in REQUIRED_CHECKS if value.checks.get(k) is None)
    missing = tuple(k for k in REQUIRED_CHECKS if k not in value.checks)
    passed = tuple(k for k in REQUIRED_CHECKS if value.checks.get(k) is True)
    reason = ('fixture_is_not_runtime_certificate' if value.origin == 'fixture' else
              'documentation_is_not_runtime_certificate' if value.origin == 'documentation' else
              'installed_probe_attestation_not_implemented')
    return CapabilityReport(profile_fingerprint=digest(value), origin=value.origin,
        checks_passed=passed, missing_checks=missing, unknown_checks=unknown,
        unsupported_checks=unsupported, reason=reason)


def runtime_support(connection, request, profile, observation=None):
    """D1 is deliberately fail-closed even if a caller supplies a certificate ID."""
    if connection.model_identity != request.model_identity:
        return False, 'decision_model_identity_unverified'
    if request.model_identity.readout_profile_id != profile.id:
        return False, 'readout_profile_mismatch'
    if request.model_identity.calibration_profile_id != profile.calibration_profile_id:
        return False, 'calibration_profile_mismatch'
    if connection.resource_group_id != request.resource_group_id:
        return False, 'shared_resource_group_unverified'
    if connection.rights_status != 'explicitly_reviewed':
        return False, 'rights_review_required'
    if observation is not None:
        report = inspect_capabilities(observation)
        return False, report.reason
    return False, 'readout_not_verified'
