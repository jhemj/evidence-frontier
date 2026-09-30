"""Pure text-choice compilation/readout for D1 contract fixtures.

Based on OpenJev's official text lane and SERVE.md choice T=0.85. No helper
import, tokenizer download, alternate runtime, generated-letter parsing, floor
scores, option splitting or forensic calibration claim.
"""
import math
import hashlib
from typing import Protocol

from .decision_contracts import (CandidateToken, CompiledDecision, DecisionResult,
    DecisionUsage, digest, unavailable)

LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz'


class TokenizerTemplate(Protocol):
    tokenizer_sha256: str
    chat_template_sha256: str

    def encode(self, text: str) -> list[int]: ...
    def render(self, user_content: str, *, thinking: bool) -> str: ...


class ReadoutUnsupported(ValueError):
    pass


def choice_text(request):
    """Official text layout; structured descriptions retain display names/IDs."""
    import json
    options = '\n'.join(f'[{LETTERS[i]}] {c.id}: ' + json.dumps(
        {'name': c.label, 'description': c.description}, ensure_ascii=False)
        for i, c in enumerate(request.candidates))
    return ('State:\n' + request.state + '\n\nQuestion: ' + request.instructions +
            '\nOptions:\n' + options + '\n\nAnswer with the letter of the best option only.')


def compile_choice(request, profile, tokenizer):
    """Exact injected renderer/counts only; no byte/character token guesses."""
    identity = request.model_identity
    if identity.readout_profile_id != profile.id or identity.calibration_profile_id != profile.calibration_profile_id:
        raise ReadoutUnsupported('profile_identity_mismatch')
    if (tokenizer.tokenizer_sha256 != identity.tokenizer_sha256 or
        tokenizer.chat_template_sha256 != identity.chat_template_sha256):
        raise ReadoutUnsupported('tokenizer_template_identity_mismatch')
    if len(request.candidates) > profile.max_candidates:
        raise ReadoutUnsupported('candidate_limit_no_split_fallback')
    tokens = []
    for i, candidate in enumerate(request.candidates):
        letter = LETTERS[i]
        ids = tokenizer.encode(letter)
        if len(ids) != 1 or type(ids[0]) is not int or ids[0] < 0:
            raise ReadoutUnsupported('candidate_not_exactly_one_token')
        tokens.append(CandidateToken(candidate_id=candidate.id, letter=letter,
            token_id=ids[0], token_bytes=tuple(letter.encode())))
    if len({t.token_id for t in tokens}) != len(tokens):
        raise ReadoutUnsupported('candidate_token_ids_not_unique')
    rendered = tokenizer.render(choice_text(request), thinking=False)
    if not isinstance(rendered, str) or not rendered:
        raise ReadoutUnsupported('complete_chat_template_not_rendered')
    encoded = tokenizer.encode(rendered)
    if not encoded or any(type(i) is not int or i < 0 for i in encoded):
        raise ReadoutUnsupported('exact_input_token_count_unavailable')
    count = len(encoded)
    if count + profile.output_tokens > profile.context_tokens:
        raise ReadoutUnsupported('complete_input_exceeds_context')
    if request.input_token_reservation is not None and count > request.input_token_reservation:
        raise ReadoutUnsupported('complete_input_exceeds_reservation')
    prompt_hash = hashlib.sha256(rendered.encode()).hexdigest()
    # Ordered candidates/refs, exact content/ranges/omissions, model/runtime,
    # tokenizer/template/readout/calibration and policy all enter the cache key.
    key = digest({'request': request.model_dump(mode='json'), 'profile': profile.model_dump(mode='json'), 'candidate_tokens':
        [t.model_dump(mode='json') for t in tokens], 'rendered_prompt_sha256': prompt_hash})
    return CompiledDecision(request_digest=digest(request), candidate_order=tuple(c.id for c in request.candidates),
        candidate_tokens=tuple(tokens), rendered_prompt=rendered, prompt_sha256=prompt_hash,
        input_tokens=count, cache_key=key, profile=profile)


def ollama_payload(request, compiled):
    """A future request shape only. No client or HTTP call exists in D1."""
    if digest(request) != compiled.request_digest:
        raise ReadoutUnsupported('compiled_request_changed')
    return {'model': request.model_identity.model, 'prompt': compiled.rendered_prompt,
        'raw': True, 'think': False, 'stream': False, 'logprobs': True, 'top_logprobs': 20,
        'options': {'temperature': 0, 'num_predict': 1, 'num_ctx': compiled.profile.context_tokens}}


def _score(row):
    value = row.get('logprob')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value > 0:
        raise ReadoutUnsupported('candidate_score_not_finite_logprob')
    return float(value)


def first_position_scores(response, compiled):
    """Only one complete output position; exact bytes, no whitespace repair."""
    if response.get('done') is not True or type(response.get('eval_count')) is not int or response.get('eval_count') != 1:
        raise ReadoutUnsupported('first_output_position_not_verified')
    positions = response.get('logprobs')
    if not isinstance(positions, list) or len(positions) != 1 or not isinstance(positions[0], dict):
        raise ReadoutUnsupported('first_output_position_not_verified')
    first = positions[0]
    if response.get('response') != first.get('token') or response.get('thinking'):
        raise ReadoutUnsupported('output_position_or_thinking_mismatch')
    top = first.get('top_logprobs')
    if not isinstance(top, list) or len(top) > 20 or any(not isinstance(r, dict) for r in top):
        raise ReadoutUnsupported('ollama_top_logprobs_contract_mismatch')
    candidates = {t.letter: t for t in compiled.candidate_tokens}
    found = {}
    for row in [first] + top:
        text = row.get('token')
        if text not in candidates:
            continue
        candidate = candidates[text]
        raw_bytes = row.get('bytes')
        if (not isinstance(raw_bytes, list) or any(type(b) is not int for b in raw_bytes)
            or tuple(raw_bytes) != candidate.token_bytes):
            raise ReadoutUnsupported('candidate_token_bytes_mismatch')
        # Ollama public responses omit token IDs. If present, require the same
        # verified tokenizer ID rather than accepting a textual look-alike.
        if 'token_id' in row and row['token_id'] != candidate.token_id:
            raise ReadoutUnsupported('candidate_token_id_mismatch')
        score = _score(row)
        if candidate.candidate_id in found and found[candidate.candidate_id] != score:
            raise ReadoutUnsupported('conflicting_candidate_scores')
        found[candidate.candidate_id] = score
    return found


def conditional_distribution(scores, candidate_order, temperature):
    if set(scores) != set(candidate_order) or not math.isfinite(temperature) or temperature <= 0:
        raise ReadoutUnsupported('complete_scores_and_positive_temperature_required')
    raw = [_score({'logprob': scores[i]}) for i in candidate_order]
    high = max(raw)
    # Subtract before division to remain finite even for a very small positive T.
    weights = [math.exp((v - high) / temperature) for v in raw]
    total = math.fsum(weights)
    return {i: w / total for i, w in zip(candidate_order, weights)}


def evaluate_fixture(request, compiled, response):
    """An explicitly synthetic advisory result, never an installed certificate."""
    if digest(request) != compiled.request_digest:
        return unavailable(request, 'compiled_request_changed', provenance='fixture')
    if not isinstance(response, dict):
        return unavailable(request, 'response_not_an_object', compiled=compiled, provenance='fixture')
    if compiled.profile.score_basis == 'unverified':
        return unavailable(request, 'score_semantics_unverified', compiled=compiled, provenance='fixture')
    if response.get('model') != request.model_identity.model:
        return unavailable(request, 'response_model_identity_mismatch', compiled=compiled, provenance='fixture')
    if response.get('prompt_eval_count') != compiled.input_tokens:
        return unavailable(request, 'complete_input_count_unverified', compiled=compiled, provenance='fixture')
    try:
        scores = first_position_scores(response, compiled)
        missing = tuple(i for i in compiled.candidate_order if i not in scores)
        if missing:
            return unavailable(request, 'candidate_logprobs_incomplete', compiled=compiled,
                provenance='fixture', missing=missing, raw_logprobs=scores or None)
        probabilities = conditional_distribution(scores, compiled.candidate_order, compiled.profile.readout_temperature)
        selected = max(compiled.candidate_order, key=probabilities.__getitem__)
        candidate = next(c for c in request.candidates if c.id == selected)
        mass = None
        if compiled.profile.score_basis == 'full_vocabulary_pre_sampler':
            mass = math.fsum(math.exp(v) for v in scores.values())
            if mass > 1 + 1e-10:
                raise ReadoutUnsupported('candidate_mass_exceeds_vocabulary_distribution')
            mass = min(1.0, mass)
        usage = DecisionUsage(input_tokens=response.get('prompt_eval_count'), output_tokens=response.get('eval_count'),
            cached_input_tokens=response.get('prompt_eval_cached_count'),
            total_duration_ns=response.get('total_duration'), load_duration_ns=response.get('load_duration'),
            prefill_duration_ns=response.get('prompt_eval_duration'), response_duration_ns=response.get('eval_duration'),
            measurement_origin='fixture')
        return DecisionResult(request_id=request.request_id, request_digest=compiled.request_digest,
            cache_key=compiled.cache_key, candidate_order=compiled.candidate_order, model_identity=request.model_identity,
            input_completeness=request.input_completeness, status='ok' if candidate.disposition == 'advisory' else 'abstained',
            chosen_id=selected, raw_logprobs=scores, probabilities=probabilities,
            first_position_verified=True, score_basis=compiled.profile.score_basis,
            candidate_probability_mass=mass, readout_profile_version=compiled.profile.version,
            reason='Conditional choice among supplied candidates only; not incident probability or evidence validity',
            provenance='fixture', usage=usage)
    except (ReadoutUnsupported, ValueError, TypeError) as ex:
        return unavailable(request, str(ex), compiled=compiled, provenance='fixture')
