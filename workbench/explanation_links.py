"""Explicit display relations, separate from judgment/version dependencies.

Sharing a source never creates an ``explains`` relation. A model may select a
retained, adopted claim from the exact input manifest; acceptance rechecks its
canonical version and scope. This records a proposed explanation connection,
not another fact judgment, test request or intrusion assessment.
"""
from copy import deepcopy

from .judgment_snapshot import claim_version, current_synthesis
from .models import ExplanationDiscriminator, ExplanationLinkProposal
from .presentation_claims import bind

VERSION = 'explicit-explains-1'


def available_claims(store, case_id, *, task_id, evidence_id, generation,
                     valid_ids, maximum=4):
    """Bounded input allowlist of existing adopted claims, not source matches.

    The allowlist is not an automatic relationship. Only an explicit selection
    in an accepted hypothesis assessment can create a relation. No candidate,
    stale synthesis, foreign task/generation or unpresented source is eligible.
    """
    try:
        task = store.get(task_id, 'task')
        evidence = store.get(evidence_id, 'evidence')
    except ValueError:
        return []
    if (not task or not evidence or task.get('case_id') != case_id
            or evidence.get('case_id') != case_id or task.get('superseded')
            or task.get('evidence_id') != evidence_id
            or task.get('retry_generation', 0) != generation
            or not evidence.get('connected', True)):
        return []
    # Read the bounded presented IDs, not every original body in the case.
    # Extra canonical dependencies needed by a stage version are loaded below
    # for hashing only and are never promoted into the presented allowlist.
    observations = {}
    for identity in set(valid_ids):
        try:
            row = store.get(identity, 'observation')
        except ValueError:
            continue
        if row.get('case_id') == case_id and row.get('evidence_id') == evidence_id:
            observations[identity] = row
    allowed = set(observations)
    dossiers = store.list('dossier', case_id)
    rows = (store.list('claim', case_id) + dossiers
            + current_synthesis(store.list('case_synthesis', case_id), dossiers=dossiers))
    result = []
    for row in sorted(rows, key=lambda r: r['id']):
        if (row.get('task_id') != task_id or row.get('evidence_id') != evidence_id
                or row.get('generation', 0) != generation or row.get('superseded')
                or row.get('status') != ('approved' if row['kind'] == 'claim' else 'reviewed')):
            continue
        finding = row.get('finding') or row
        canonical_ids = set(finding.get('observation_ids', []) + finding.get('counterevidence_ids', []))
        canonical_ids.update(i for stage in finding.get('stages', []) for i in stage.get('observation_ids', []))
        canonical_ids.update(a.get('observation_id') for a in finding.get('fact_assertions', []))
        for identity in canonical_ids - set(observations):
            try:
                original = store.get(identity, 'observation')
            except (ValueError, TypeError):
                continue
            if original.get('case_id') == case_id and original.get('evidence_id') == evidence_id:
                observations[identity] = original
        variants = [(row['id'], finding, None)]
        variants.extend((row['id'] + ':stage:' + str(i), stage, i)
                        for i, stage in enumerate(finding.get('stages', [])))
        for identity, selected, index in variants:
            ids = list(dict.fromkeys(selected.get('observation_ids', [])
                                    + selected.get('counterevidence_ids', [])))
            if not ids or not set(ids) <= allowed:
                continue
            # Match the observer's selected literal binding, including the
            # stage's own source range. An unbound legacy title stays unlinked.
            facts = [a for a in finding.get('fact_assertions', [])
                     if a.get('observation_id') in selected.get('observation_ids', [])]
            try:
                display = bind({**finding, 'observation_ids': selected.get('observation_ids', []),
                                'fact_assertions': facts}, observations)
            except (ValueError, KeyError, TypeError):
                continue
            if display['display_binding']['status'] != 'bound':
                continue
            proposition = (selected.get('statement') or selected.get('text')
                           or selected.get('card_summary') or '').strip()
            if not proposition or len(proposition) > 2000:
                continue
            result.append({'claim_ref': {'kind': 'claim', 'id': identity,
                'version': claim_version(row, finding, observations, index)},
                'target_scope': {'task_id': task_id, 'evidence_id': evidence_id,
                    'generation': generation, 'observation_ids': ids,
                    'proposition': proposition}})
            if maximum is not None and len(result) >= maximum:
                return result
    return result


def validate_proposals(store, case_id, proposals, manifest, *, task_id,
                       evidence_id, generation, valid_ids, hypothesis_source_ids=None):
    """Return independent accepted links and rejection reasons, fail closed.

    A malformed relationship does not erase an otherwise valid hypothesis.
    It is omitted from the display relation ledger with an explicit receipt.
    """
    if not isinstance(proposals, list) or not isinstance(manifest, list):
        return [], ['invalid_explanation_manifest'] if proposals else []
    if not proposals:
        return [], []
    # The current check must not inherit the input's four-entry display bound:
    # the input is the authorization allowlist, current retained claims are the
    # freshness gate. Their sort order may have changed since input preparation.
    current = available_claims(store, case_id, task_id=task_id,
        evidence_id=evidence_id, generation=generation, valid_ids=valid_ids,
        maximum=None)
    accepted, rejected = [], []
    for raw in proposals[:4]:
        try:
            proposal = ExplanationLinkProposal.model_validate(raw).model_dump()
        except (ValueError, TypeError):
            rejected.append('invalid_explanation_link')
            continue
        selected = {k: proposal[k] for k in ('claim_ref', 'target_scope')}
        if selected not in manifest:
            rejected.append('claim_not_presented_with_exact_scope')
            continue
        if selected not in current:
            rejected.append('claim_version_or_scope_changed')
            continue
        if (hypothesis_source_ids is not None
                and not set(proposal['target_scope']['observation_ids']) <= set(hypothesis_source_ids)):
            rejected.append('claim_outside_declared_hypothesis_sources')
            continue
        if not any(all(link[k] == proposal[k] for k in ('claim_ref', 'target_scope')) for link in accepted):
            accepted.append(deepcopy(proposal))
    if len(proposals) > 4:
        rejected.append('explanation_link_limit')
    return accepted, rejected


def record(store, case_id, hypothesis, links, *, source_plan_id):
    """Write relations after object adoption, without mutating claim versions."""
    rows = []
    for link in links:
        rows.append(store.add('explanation_relation', case_id, contract=VERSION,
            task_id=hypothesis['task_id'], evidence_id=hypothesis['evidence_id'],
            generation=hypothesis.get('generation', 0),
            hypothesis_id=hypothesis['id'], hypothesis_revision=hypothesis['revision'],
            source_plan_id=source_plan_id, **deepcopy(link)))
    return rows


def validate_discriminator(store, case_id, proposal, manifest, *, task_id,
                           evidence_id, generation, valid_ids):
    """Validate an explicitly supplied test target; ownership is not a target."""
    if proposal is None:
        return None, []
    try:
        target = ExplanationDiscriminator.model_validate(proposal).model_dump()
    except (ValueError, TypeError):
        return None, ['invalid_discrimination_target']
    # Reuse the same authorization/freshness gate. This internal rationale is
    # not a model statement and is not persisted as an explanation relation.
    links, errors = validate_proposals(store, case_id,
        [{**target, 'relation': 'explains', 'rationale': 'Explicit copied test target.'}], manifest,
        task_id=task_id, evidence_id=evidence_id, generation=generation, valid_ids=valid_ids)
    return target if links else None, errors
