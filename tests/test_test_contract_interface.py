"""P0 model-facing purpose/outcome contract; anonymous fixtures, no model calls.

Retained failures motivate these shapes but no evidence or private output is
embedded here. Rejected replies are never normalized into accepted replies.
"""
from copy import deepcopy
from itertools import combinations
import json

import pytest
from pydantic import ValidationError

from workbench.models import (TestDesignV2 as DesignV2, TestOutcomeRules as Rules,
    TEST_OUTCOME_UNSUPPORTED as UNSUPPORTED, TEST_PURPOSE_OUTCOMES as PURPOSES,
    generation_schema)
from workbench.request_compiler import (compile_request, compile_spec, request_spec,
    test_contract_protocol as protocol, RequestBudgetError, REVIEW_QUESTIONS)
from workbench.review_diagnostics import repair_feedback
from workbench.provider import ModelOutputError


OUTCOMES=tuple(Rules.model_fields)
CONFIG={'protocol':'ollama','base_url':'http://127.0.0.1:11434','model':'interface-fixture'}


def ref(kind,ident):
    return {'kind':kind,'id':ident,'version':'a'*64}


def design(purpose='discover',supported=None):
    if supported is None:
        supported=PURPOSES[purpose] if purpose=='discover' else ('supports','inconclusive')
    rules={key:('Scoped fixture condition for '+key if key in supported else UNSUPPORTED)
        for key in OUTCOMES}
    return {'version':2,'purpose':purpose,'immediate_observable':'source_content',
        'owner_ref':ref('dossier','owner'),'question_ref':ref('case_question','question'),
        'target_ref':None if purpose=='discover' else ref('claim','target'),
        'target_scope':{'case_id':'case','task_id':'task','evidence_id':'evidence',
            'generation':0,'source_run':'source-run'},
        'target_proposition':'' if purpose=='discover' else 'A scoped fixture proposition',
        'required_inputs':[],'baseline_ref':None,'required_result_view':'body_excerpt',
        'outcome_rules':rules,'allowed_outcomes':[key for key in OUTCOMES if key in supported],
        'design_timing':'before_result','uses_existing_result':False,'lineage':None,
        'expected_update':'Resolve a scoped collection question','reopen_on':'Changed source scope'}


def pack():
    return {'test_contract_policy':'purpose-outcomes-v2',
        'observations':[{'id':'OBSERVATION-0123456789ab','type':'fixture',
            'fields':{'excerpt':'retained original; contrary context','path':'/fixture/record'}}],
        'allowed_observation_ids':['OBSERVATION-0123456789ab']}


def test_schema_and_protocol_publish_the_same_exact_crossing():
    schema=generation_schema(DesignV2)
    assert schema['$defs']['TestOutcomeRules']['required']==list(OUTCOMES)
    props=schema['$defs']['TestOutcomeRules']['properties']
    assert UNSUPPORTED in schema['$defs']['TestOutcomeRules']['description']
    assert UNSUPPORTED in props['inconclusive']['description']
    assert 'Discover' in props['inconclusive']['description']
    assert 'case_question' in schema['properties']['question_ref']['description']
    assert 'all non-sentinel' in schema['$defs']['TestOutcomeRules']['description']
    text=protocol()
    for outcomes in PURPOSES.values():
        for outcome in outcomes:assert outcome in text
    assert 'supports, refutes AND inconclusive must ALL equal' in text
    assert 'One-sided tests are allowed' in text
    assert 'question_ref.kind MUST be case_question' in text
    assert 'uses_existing_result=false' in text and 'lineage=null' in text
    assert 'uses_existing_result=true' in text and 'parent_contract_id/result_revision' in text
    assert 'Metadata/hash is not a source body' in text


@pytest.mark.parametrize('role',['investigator','judgment','synthesis'])
def test_actual_compiled_messages_schema_and_budget_include_contract(role,monkeypatch):
    source=pack();before=deepcopy(source);cfg=deepcopy(CONFIG)
    monkeypatch.setattr('socket.getaddrinfo',lambda *a,**k:pytest.fail('compiler called network'))
    monkeypatch.setattr('httpx.Client',lambda *a,**k:pytest.fail('compiler constructed transport'))
    compiled=compile_spec(request_spec(cfg,'fixture question',role),source)
    assert source==before and cfg==CONFIG
    assert protocol() in compiled.messages[0]['content']
    assert compiled.output_schema['$defs']['TestDesignV2']['description']
    assert compiled.payload['messages']==compiled.messages
    assert compiled.payload['format']==compiled.output_schema
    assert json.dumps(compiled.output_schema) in compiled.messages[0]['content']
    assert compiled.budget['request_utf8_bytes']==len(compiled.payload_json.encode())
    assert compiled.budget['count_basis']=='heuristic'
    assert compiled.budget['output_tokens_reserved']==4000
    assert compiled.budget['context_tokens_requested']==32768


def test_legacy_compilation_and_schema_do_not_get_v2_rules():
    source=pack();source.pop('test_contract_policy')
    compiled=compile_request(CONFIG,'question',source,'judgment')
    assert protocol() not in compiled.messages[0]['content']
    assert 'TestDesignV2' not in compiled.output_schema['$defs']


def test_added_guidance_is_budgeted_and_cannot_hide_source_or_increase_limits():
    cfg={**CONFIG,'num_ctx':8192,'num_predict':4000}
    source=pack();before=deepcopy(source)
    compiled=compile_request(cfg,REVIEW_QUESTIONS['judgment'],source,'judgment')
    assert compiled.budget['context_tokens_requested']==8192
    assert compiled.budget['output_tokens_reserved']==4000
    assert compiled.budget['estimated_headroom']<0
    with pytest.raises(RequestBudgetError):compiled.assert_fits()
    assert source==before
    assert 'retained original; contrary context' in compiled.messages[1]['content']


def test_old_validator_acceptance_is_preserved_for_every_purpose_and_rule_subset():
    # Exhaust the pre-existing crossing predicate, not only selected happy paths.
    for purpose in PURPOSES:
        for length in range(len(OUTCOMES)+1):
            for keys in combinations(OUTCOMES,length):
                supported=set(keys);value=design(purpose,keys)
                expected=bool(supported) and supported<=PURPOSES[purpose]
                if purpose!='discover':expected=expected and bool(supported&{'supports','refutes'})
                try:accepted=DesignV2.model_validate(value)
                except ValidationError:assert not expected,(purpose,keys)
                else:
                    assert expected,(purpose,keys)
                    assert accepted.model_dump()==value


@pytest.mark.parametrize('unsupported_side',['supports','refutes','inconclusive'])
@pytest.mark.parametrize('substitute',['해당하지않음','unsupported',' unsupported_by_this_test',
    'unsupported_by_this_test ','','No applicable rule'])
def test_discovery_never_accepts_translated_or_padded_sentinel(unsupported_side,substitute):
    value=design();before=deepcopy(value)
    value['outcome_rules'][unsupported_side]=substitute;rejected=deepcopy(value)
    with pytest.raises(ValidationError):DesignV2.model_validate(value)
    assert value==rejected and before['outcome_rules'][unsupported_side]==UNSUPPORTED


@pytest.mark.parametrize('natural_no_match',[False,True])
def test_retained_failure_shapes_remain_rejected_and_only_explicit_synthetic_redesign_passes(natural_no_match):
    original=design(supported=('found','partial','unavailable'))
    original['outcome_rules']['inconclusive']='The returned excerpt cannot decide the whole incident'
    if natural_no_match:original['outcome_rules']['no_match_in_scope']='No match was returned'
    unchanged=deepcopy(original)
    with pytest.raises(ValidationError,match='allowed_outcomes must exactly match'):
        DesignV2.model_validate(original)
    assert original==unchanged
    # A separate, deliberately authored fixture. Production never rewrites a reply.
    redesigned=design(supported=('found','partial','unavailable'))
    assert DesignV2.model_validate(redesigned).model_dump()==redesigned
    assert original==unchanged


def test_mismatch_feedback_is_exact_actionable_and_preserves_rejection():
    value=design(supported=('found','partial','unavailable'))
    value['outcome_rules']['inconclusive']='Insufficient source body'
    with pytest.raises(ValidationError) as caught:DesignV2.model_validate(value)
    error=ModelOutputError(str(caught.value),'private rejected fixture','output_schema',{})
    feedback=repair_feedback(error,[],'diagnostic')
    detail=feedback['errors'][0]['detail']
    assert UNSUPPORTED in detail
    assert 'non-sentinel keys=' in detail and 'allowed_outcomes=' in detail
    assert "purpose='discover'" in detail and 'permits only' in detail
    assert len(json.dumps(feedback,ensure_ascii=False,separators=(',',':')))<=4096
    assert 'Rejected output is not evidence' in feedback['instruction']


def test_adding_inconclusive_to_discovery_allowlist_does_not_bypass_purpose():
    value=design();value['outcome_rules']['inconclusive']='Still cannot decide'
    value['allowed_outcomes'].append('inconclusive')
    with pytest.raises(ValidationError,match='Discovery collects observations'):
        DesignV2.model_validate(value)


@pytest.mark.parametrize('purpose',['discriminate','verify_reliability'])
@pytest.mark.parametrize('side',['supports','refutes'])
def test_one_sided_conditions_are_accepted_without_fabricating_the_opposite(purpose,side):
    value=design(purpose,(side,'inconclusive'))
    assert DesignV2.model_validate(value).model_dump()==value
    opposite='refutes' if side=='supports' else 'supports'
    assert value['outcome_rules'][opposite]==UNSUPPORTED
    assert opposite not in value['allowed_outcomes']
    value['outcome_rules'][side]=UNSUPPORTED;value['allowed_outcomes'].remove(side)
    with pytest.raises(ValidationError,match='At least one discriminating side'):
        DesignV2.model_validate(value)


def test_question_ref_is_not_interchangeable_with_owner_ref():
    value=design();value['question_ref']=deepcopy(value['owner_ref'])
    with pytest.raises(ValidationError,match='question_ref must reference a question'):
        DesignV2.model_validate(value)


@pytest.mark.parametrize('timing,reuse,lineage',[
    ('before_result',True,None),('before_result',False,{'parent_contract_id':'b'*64,'result_revision':'c'*64}),
    ('after_result',False,None),('after_result',True,None)])
def test_before_after_lineage_requirements_are_not_relaxed(timing,reuse,lineage):
    value=design();value.update(design_timing=timing,uses_existing_result=reuse,lineage=lineage)
    with pytest.raises(ValidationError):DesignV2.model_validate(value)


def test_explicit_after_result_lineage_is_preserved_not_invented():
    value=design();value.update(design_timing='after_result',uses_existing_result=True,
        lineage={'parent_contract_id':'b'*64,'result_revision':'c'*64})
    assert DesignV2.model_validate(value).model_dump()==value


def test_unsupported_duplicate_and_missing_fields_are_still_invalid():
    value=design();value['allowed_outcomes'].append('found')
    with pytest.raises(ValidationError,match='Duplicate allowed outcome'):DesignV2.model_validate(value)
    value=design();value['outcome_rules'].pop('inconclusive')
    with pytest.raises(ValidationError):DesignV2.model_validate(value)
