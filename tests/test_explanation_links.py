from copy import deepcopy

import pytest

from workbench.explanation_links import available_claims, validate_proposals
from workbench.hypothesis_ledger import apply
from workbench.models import HypothesisAssessment
from workbench.store import Store


@pytest.fixture
def context(tmp_path):
    store = Store(tmp_path / 'case.db')
    case = store.add('case', '', name='fabricated relationship contract')
    evidence = store.add('evidence', case['id'], connected=True)
    task = store.add('task', case['id'], evidence_id=evidence['id'], retry_generation=0)
    observations = [store.add('observation', case['id'], evidence_id=evidence['id'],
        type='linux_command', fields={'command': value})
        for value in ('inspect --status fixture', 'inspect --result fixture')]
    finding = {'title': 'Fixture status command', 'card_summary': 'A status command is recorded; execution is unknown.',
        'judgment': '확인', 'reason': 'Recorded fixture text only.',
        'observation_ids': [observations[0]['id']], 'counterevidence_ids': [],
        'fact_assertions': [{'observation_id': observations[0]['id'], 'pointer': '/fields/command',
            'operator': 'equals', 'value': 'inspect --status fixture'}],
        'stages': [{'stage': 'invocation', 'judgment': '확인',
            'statement': 'The retained record contains a status query, not an execution result.',
            'observation_ids': [observations[0]['id']]}]}
    dossier = store.add('dossier', case['id'], task_id=task['id'], evidence_id=evidence['id'],
        generation=0, status='reviewed', observation_ids=[observations[0]['id']], finding=finding)
    valid_ids = [o['id'] for o in observations]
    manifest = available_claims(store, case['id'], task_id=task['id'],
        evidence_id=evidence['id'], generation=0, valid_ids=valid_ids)
    plan = store.add('investigation_plan', case['id'], task_id=task['id'], generation=0,
        accepted_claim_refs=manifest)
    assessment = {'action': 'create', 'title': 'Is the fixture query connected to work execution?',
        'judgment': '미확인', 'reasoning': 'The recorded query is a lead, not proof of success.',
        'supporting_evidence_ids': [observations[0]['id']]}
    proposal = {'relation': 'explains', **manifest[0],
        'rationale': 'This retained status query explains why execution remains a competing possibility.'}
    def submit(value, source=plan):
        return apply(store, case['id'], evidence['id'], value, valid_ids,
            task_id=task['id'], generation=0, source_plan_id=source['id'])
    return store, case, evidence, task, observations, dossier, manifest, plan, assessment, proposal, submit


def test_explicit_versioned_scope_is_required_and_recorded_separately(context):
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    hypothesis = submit({**assessment, 'explanation_links': [proposal]})
    relations = store.list('explanation_relation', case['id'])
    assert len(relations) == 1
    relation = relations[0]
    assert relation['hypothesis_id'] == hypothesis['id'] and relation['hypothesis_revision'] == 1
    assert relation['claim_ref'] == proposal['claim_ref']
    assert relation['target_scope'] == proposal['target_scope']
    assert 'explanation_links' not in hypothesis and 'claim_ref' not in hypothesis['revision_history'][0]
    assert available_claims(store, case['id'], task_id=task['id'], evidence_id=evidence['id'],
        generation=0, valid_ids=[o['id'] for o in obs]) == manifest
    assert submit({**assessment, 'explanation_links': [proposal]})['id'] == hypothesis['id']
    assert len(store.list('explanation_relation', case['id'])) == 1


def test_same_sources_without_explicit_selection_do_not_create_relation(context):
    store, case, *rest = context
    assessment, submit = context[-3], context[-1]
    assert submit(assessment)
    assert store.list('explanation_relation', case['id']) == []
    assert store.list('receipt', case['id']) == []


def test_same_explicit_relation_rephrasing_is_not_an_extra_link(context):
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    assert submit({**assessment, 'explanation_links': [proposal,
        {**proposal, 'rationale': 'The same relationship written a second time.'}]})
    assert len(store.list('explanation_relation', case['id'])) == 1


def test_explicit_link_still_requires_the_declared_hypothesis_source_scope(context):
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    assert submit({**assessment, 'supporting_evidence_ids': [obs[1]['id']], 'explanation_links': [proposal]})
    assert store.list('explanation_relation', case['id']) == []
    assert store.list('receipt', case['id'])[-1]['validation_errors'] == ['claim_outside_declared_hypothesis_sources']


@pytest.mark.parametrize('alter', ['proposition', 'observations', 'evidence', 'version', 'identity'])
def test_invalid_link_does_not_erase_valid_hypothesis(context, alter):
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    value = deepcopy(proposal)
    if alter == 'proposition': value['target_scope']['proposition'] = 'Successful execution was confirmed.'
    if alter == 'observations': value['target_scope']['observation_ids'] = [obs[1]['id']]
    if alter == 'evidence': value['target_scope']['evidence_id'] = 'EVIDENCE-foreign'
    if alter == 'version': value['claim_ref']['version'] = 'f' * 64
    if alter == 'identity': value['claim_ref']['id'] = 'DOSSIER-invented'
    assert submit({**assessment, 'explanation_links': [value]}) is not None
    assert store.list('explanation_relation', case['id']) == []
    receipt = store.list('receipt', case['id'])[-1]
    assert receipt['receipt_type'] == 'explanation_link_rejected'
    assert receipt['validation_errors'] == ['claim_not_presented_with_exact_scope']


@pytest.mark.parametrize('change', ['judgment', 'source', 'withdrawal'])
def test_retained_manifest_cannot_authorize_changed_claim(context, change, monkeypatch):
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    if change == 'judgment':
        store.update(dossier['id'], finding={**dossier['finding'], 'reason': 'A corrected interpretation.'})
    if change == 'source':
        # Simulate a corrected canonical source view without mutating original
        # evidence (Store correctly prohibits observation writes).
        original_get = store.get
        def corrected_get(identity, kind=None):
            row = original_get(identity, kind)
            return {**row, 'fields': {'command': 'a corrected original'}} if identity == obs[0]['id'] else row
        monkeypatch.setattr(store, 'get', corrected_get)
    if change == 'withdrawal':
        store.update(dossier['id'], status='retracted')
    assert submit({**assessment, 'explanation_links': [proposal]})
    assert store.list('explanation_relation', case['id']) == []
    assert store.list('receipt', case['id'])[-1]['validation_errors'] == ['claim_version_or_scope_changed']


def test_relation_for_old_revision_does_not_silently_carry_forward(context):
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    first = submit({**assessment, 'explanation_links': [proposal]})
    new_plan = store.add('investigation_plan', case['id'], task_id=task['id'], generation=0,
        accepted_claim_refs=manifest)
    second = submit({**assessment, 'action': 'hold', 'hypothesis_card_id': first['hypothesis_card_id'],
                     'explanation_links': []}, source=new_plan)
    assert second['revision'] == 2
    assert [r['hypothesis_revision'] for r in store.list('explanation_relation', case['id'])] == [1]


def test_manifest_excludes_other_scope_unpresented_and_candidate_claims(context):
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    assert available_claims(store, case['id'], task_id=task['id'], evidence_id=evidence['id'],
        generation=0, valid_ids=[obs[1]['id']]) == []
    foreign = store.add('evidence', case['id'], connected=True)
    assert available_claims(store, case['id'], task_id=task['id'], evidence_id=foreign['id'],
        generation=0, valid_ids=[o['id'] for o in obs]) == []
    assert available_claims(store, case['id'], task_id=task['id'], evidence_id=evidence['id'],
        generation=1, valid_ids=[o['id'] for o in obs]) == []
    store.update(dossier['id'], status='candidate')
    assert available_claims(store, case['id'], task_id=task['id'], evidence_id=evidence['id'],
        generation=0, valid_ids=[o['id'] for o in obs]) == []


def test_input_pack_manifest_and_canonical_stage_ref(context):
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    stage = next(m for m in manifest if m['claim_ref']['id'].endswith(':stage:0'))
    source = store.add('synthesis_input', case['id'], task_id=task['id'], generation=0,
        pack={'accepted_claim_refs': manifest})
    assert submit({**assessment, 'explanation_links': [{**proposal, **stage}]}, source=source)
    assert store.list('explanation_relation', case['id'])[0]['claim_ref'] == stage['claim_ref']


def test_explicit_empty_source_manifest_does_not_fall_back_to_other_metadata(context):
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    store.update(plan['id'], accepted_claim_refs=[], pack={'accepted_claim_refs': manifest})
    assert submit({**assessment, 'explanation_links': [proposal]})
    assert store.list('explanation_relation', case['id']) == []


def test_schema_optional_and_cannot_accept_foreign_relation(context):
    *_, assessment, proposal, submit = context
    assert HypothesisAssessment.model_validate(assessment).explanation_links == []
    parsed = HypothesisAssessment.model_validate({**assessment, 'explanation_links': [proposal]})
    assert parsed.explanation_links[0].claim_ref.version == proposal['claim_ref']['version']
    with pytest.raises(ValueError):
        HypothesisAssessment.model_validate({**assessment,
            'explanation_links': [{**proposal, 'relation': 'proves_intrusion'}]})


def test_model_transport_preserves_exact_manifest_and_proposal_refs(context):
    from workbench.model_references import ReferenceProjection
    manifest, proposal = context[6], context[-2]
    pack = {'accepted_claim_refs': manifest}
    projection = ReferenceProjection(pack)
    encoded = projection.encode({'hypotheses': [{'explanation_links': [proposal]}]})
    assert projection.decode(encoded) == {'hypotheses': [{'explanation_links': [proposal]}]}


def test_test_discriminator_is_separate_from_logical_dossier_ownership(context):
    from workbench.question_engine import reserve
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    request = {'tool': 'search', 'query': 'inert fixture', 'hypothesis_id': dossier['id'],
        'success_condition': 'A retained matching outcome can narrow execution.',
        'test_design': {'purpose': 'discriminate', 'immediate_observable': 'matching_records',
                        'discrimination_target': manifest[0]}}
    intent = reserve(store, case['id'], task, {'question_key': 'fixture', 'version': 1},
        request, evidence, 'fixture-run', accepted_claim_refs=manifest, source_record_id=plan['id'])
    assert intent['scope']['semantic_target_ref'] == manifest[0]['claim_ref']
    assert intent['scope']['semantic_target_scope'] == manifest[0]['target_scope']
    assert intent['scope']['logical_contract']['target_ref']['kind'] == 'dossier'
    assert intent['scope']['logical_contract']['target_ref']['version'] is None
    assert intent['admission']['eligible']


@pytest.mark.parametrize('reason', ['missing_source', 'unsupplied_manifest', 'wrong_version'])
def test_rejected_test_target_preserves_physical_request_and_has_no_semantic_link(context, reason):
    from workbench.question_engine import reserve
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    selected = deepcopy(manifest[0])
    if reason == 'wrong_version': selected['claim_ref']['version'] = 'f' * 64
    request = {'tool': 'search', 'query': 'inert fixture',
        'test_design': {'purpose': 'discriminate', 'immediate_observable': 'matching_records',
                        'discrimination_target': selected}}
    intent = reserve(store, case['id'], task, {'question_key': 'fixture', 'version': 1},
        request, evidence, 'fixture-run', accepted_claim_refs=[] if reason == 'unsupplied_manifest' else manifest,
        source_record_id=None if reason == 'missing_source' else plan['id'])
    assert intent['admission']['eligible']
    assert intent['scope']['request']['query'] == 'inert fixture'
    assert 'discrimination_target' not in intent['scope']['test_design']
    assert 'semantic_target_ref' not in intent['scope']
    assert store.list('receipt', case['id'])[-1]['receipt_type'] == 'test_discrimination_target_rejected'


def test_question_and_owner_alone_never_produce_test_discriminator(context):
    from workbench.question_engine import reserve
    store, case, evidence, task, obs, dossier, manifest, plan, assessment, proposal, submit = context
    request = {'tool': 'search', 'query': 'inert fixture', 'hypothesis_id': dossier['id']}
    intent = reserve(store, case['id'], task, {'question_key': 'fixture', 'version': 1},
        request, evidence, 'fixture-run', accepted_claim_refs=manifest, source_record_id=plan['id'])
    assert 'semantic_target_ref' not in intent['scope']
    assert 'discrimination_target' not in intent['scope']['test_design']
    assert store.list('receipt', case['id']) == []
