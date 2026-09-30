"""D1 zero-model fixtures: no installed capability, policy or judgment authority."""
from copy import deepcopy
import hashlib
import math

import pytest
from pydantic import ValidationError

from workbench.decision_contracts import (DecisionConnection, DecisionRequest, DecisionResult,
    ModelIdentity, ReadoutProfile, digest)
from workbench.decision_capabilities import (CapabilityObservation, REQUIRED_CHECKS,
    inspect_capabilities)
from workbench.decision_provider import DecisionProvider, UnsupportedResourceBroker, applicable_advice
from workbench.openjev_readout import (ReadoutUnsupported, choice_text, compile_choice,
    conditional_distribution, evaluate_fixture, ollama_payload)


def ref(kind='question', ident='q', version='v1'):
    return {'kind': kind, 'id': ident, 'version': version}


def identity():
    return ModelIdentity(model='fixture-openjev', weights_sha256='1'*64, gguf_sha256='2'*64,
        quantization='fixture-q4', ollama_version='fixture-version', runner_version='fixture-runner',
        tokenizer_sha256='3'*64, chat_template_sha256='4'*64,
        readout_profile_id='fixture-profile', calibration_profile_id='official-choice-reproduction-0.85')


def profile(**updates):
    return ReadoutProfile(id='fixture-profile', version='profile-v1',
        calibration_profile_id='official-choice-reproduction-0.85',
        score_basis='full_vocabulary_pre_sampler', **updates)


def request(**updates):
    body = dict(request_id='request-1', attempt_id='attempt-1', case_id='fixture-case', run_id='fixture-run',
        snapshot_revision='snapshot-1', ledger_position=1, policy_kind='review_priority', policy_version='policy-1',
        question_ref=ref(), logical_work_ref=ref('review', 'r'), candidate_producer_ref=ref('controller', 'c'),
        candidates=[{'id': 'review-result', 'label': '반환 결과 검토', 'description': '실행 가능한 결과를 검토',
                     'refs': [ref('test_intent', 't')]},
                    {'id': 'need-input', 'label': '자료 부족', 'description': '조건을 판별할 입력 부족',
                     'disposition': 'insufficient_evidence'}],
        evidence=[{'ref': ref('observation', 'o'), 'content_sha256': '5'*64,
                   'unit': 'byte', 'start': 4, 'end': 12}],
        counterevidence_refs=[ref('objection', 'obj')], required_review_refs=[ref('mandatory_review', 'm')],
        state='Fixture state only; no case source.', instructions='다음에 살펴볼 것은 무엇인가요?',
        input_completeness='complete_presented_scope', model_identity=identity(), resource_group_id='fixture-gpu')
    body.update(updates)
    return DecisionRequest.model_validate(body)


class FixtureTokenizer:
    """Explicitly synthetic identities/counts; never used as installed proof."""
    tokenizer_sha256 = '3'*64
    chat_template_sha256 = '4'*64
    calls = []

    def encode(self, text):
        if len(text) == 1 and text.isascii() and text.isalpha():
            return [ord(text)]
        return list(text.encode())

    def render(self, user_content, *, thinking):
        assert thinking is False
        return '<fixture-user>\n' + user_content + '\n<fixture-assistant-no-thinking>\n'


def compiled(value=None, p=None, tokenizer=None):
    return compile_choice(value or request(), p or profile(), tokenizer or FixtureTokenizer())


def response(c=None):
    c = c or compiled()
    return {'model': 'fixture-openjev', 'done': True, 'response': 'A', 'eval_count': 1,
            'prompt_eval_count': c.input_tokens, 'prompt_eval_cached_count': 0,
            'logprobs': [{'token': 'A', 'bytes': [65], 'logprob': -1.0,
                         'top_logprobs': [{'token': 'A', 'bytes': [65], 'logprob': -1.0},
                                          {'token': 'B', 'bytes': [66], 'logprob': -2.0}]}]}


def test_disabled_and_unverified_shadow_have_no_live_requests_or_distribution():
    req = request()
    result = DecisionProvider().decide(req, profile())
    assert result.reason == 'decision_disabled' and result.probabilities is None and result.chosen_id is None
    connection = DecisionConnection(mode='shadow', model_identity=identity(),
        resource_group_id='fixture-gpu', rights_status='explicitly_reviewed', capability_certificate_id='made-up')
    result = DecisionProvider(connection).decide(req, profile(), broker=UnsupportedResourceBroker())
    assert result.status == 'unsupported' and result.reason == 'readout_not_verified'
    assert not result.policy_applied and result.probabilities is None


def test_d1_cannot_enable_routing_or_accept_arbitrary_json_contract_fields():
    with pytest.raises(ValidationError):
        DecisionConnection(mode='routing')
    with pytest.raises(ValidationError):
        DecisionConnection(mode='shadow', skip_required_review=True)
    with pytest.raises(ValidationError):
        request(candidates=[{'id':'same','label':'A','description':''},
                            {'id':'same','label':'B','description':'','disposition':'abstain'}])
    with pytest.raises(ValidationError):
        request(candidates=[{'id':'a','label':'A','description':''}, {'id':'b','label':'B','description':''}])


def test_complete_choice_has_exact_order_pure_prompt_and_advisory_only_distribution():
    req = request();c = compiled(req);result = evaluate_fixture(req, c, response(c))
    assert result.status == 'ok' and result.chosen_id == 'review-result'
    assert c.candidate_order == ('review-result', 'need-input')
    assert c.prompt_sha256 == hashlib.sha256(c.rendered_prompt.encode()).hexdigest()
    assert '[A] review-result:' in choice_text(req) and '[B] need-input:' in choice_text(req)
    expected = 1 / (1 + math.exp(-1 / 0.85))
    assert result.probabilities['review-result'] == pytest.approx(expected)
    assert result.candidate_probability_mass == pytest.approx(math.exp(-1)+math.exp(-2))
    assert result.provenance == 'fixture' and result.usage.measurement_origin == 'fixture'
    assert result.order_stability is None and result.usage.total_duration_ns is None
    assert result.advisory_only and not result.policy_applied
    assert req.required_review_refs == request().required_review_refs


def test_unknown_candidate_is_abstention_not_a_failed_request_or_policy():
    req = request();c = compiled(req);r = response(c)
    r['response'] = 'B';r['logprobs'][0]['token'] = 'B';r['logprobs'][0]['bytes'] = [66]
    r['logprobs'][0]['logprob'] = -0.5
    r['logprobs'][0]['top_logprobs'][1]['logprob'] = -0.5
    result = evaluate_fixture(req,c,r)
    assert result.status == 'abstained' and result.chosen_id == 'need-input'
    assert result.probabilities is not None and not result.policy_applied


def test_missing_top_candidate_is_not_zero_or_partial_normalization():
    req = request();c = compiled(req);r = response(c)
    r['logprobs'][0]['top_logprobs'] = [r['logprobs'][0]['top_logprobs'][0]]
    result = evaluate_fixture(req,c,r)
    assert result.status == 'unsupported' and result.missing_candidate_ids == ('need-input',)
    assert result.probabilities is None and result.chosen_id is None
    assert result.raw_logprobs == {'review-result': -1.0}


@pytest.mark.parametrize('changed', [float('nan'), float('inf'), float('-inf'), True, 0.1, None, '0'])
def test_nonfinite_or_non_logprob_candidate_is_unsupported(changed):
    req=request();c=compiled(req);r=response(c)
    r['logprobs'][0]['top_logprobs'][1]['logprob']=changed
    result=evaluate_fixture(req,c,r)
    assert result.status=='unsupported' and result.probabilities is None


@pytest.mark.parametrize('change', ['whitespace','wrong_bytes','wrong_id','later_position','thinking',
                                   'not_done','count_two','boolean_count','conflicting','truncated_input','wrong_model','not_object'])
def test_response_contract_rejects_ambiguous_or_later_scores(change):
    req=request();c=compiled(req);r=response(c)
    top=r['logprobs'][0]['top_logprobs'][1]
    if change=='whitespace':top['token']=' B';top['bytes']=[32,66]
    if change=='wrong_bytes':top['bytes']=[32,66]
    if change=='wrong_id':top['token_id']=123456
    if change=='later_position':r['logprobs'].append(deepcopy(r['logprobs'][0]))
    if change=='thinking':r['thinking']='not allowed'
    if change=='not_done':r['done']=False
    if change=='count_two':r['eval_count']=2
    if change=='boolean_count':r['eval_count']=True
    if change=='conflicting':r['logprobs'][0]['top_logprobs'][0]['logprob']=-2
    if change=='truncated_input':r['prompt_eval_count']=c.input_tokens-1
    if change=='wrong_model':r['model']='different-model'
    if change=='not_object':r=None
    result=evaluate_fixture(req,c,r)
    assert result.status=='unsupported' and result.probabilities is None and result.chosen_id is None


def test_generated_letter_is_not_parsed_as_the_decision():
    req=request();c=compiled(req);r=response(c)
    r['response']='Z';r['logprobs'][0].update(token='Z',bytes=[90],logprob=-0.9)
    result=evaluate_fixture(req,c,r)
    assert result.chosen_id=='review-result' and not result.policy_applied
    assert result.chosen_id!='Z'


def test_unknown_score_semantics_or_conditional_scores_do_not_manufacture_mass():
    req=request();p=profile().model_copy(update={'score_basis':'unverified'});c=compiled(req,p)
    assert evaluate_fixture(req,c,response(c)).probabilities is None
    p=profile().model_copy(update={'score_basis':'candidate_normalized_pre_sampler'});c=compiled(req,p)
    r=response(c);r['logprobs'][0]['logprob']=math.log(.7)
    r['logprobs'][0]['top_logprobs'][0]['logprob']=math.log(.7)
    r['logprobs'][0]['top_logprobs'][1]['logprob']=math.log(.3)
    result=evaluate_fixture(req,c,r)
    assert result.probabilities is not None and result.candidate_probability_mass is None


def test_distribution_is_stable_at_extreme_finite_logprobs_and_not_uniform_missing():
    distribution=conditional_distribution({'a':-1e300,'b':-1e300},('a','b'),.85)
    assert distribution=={'a':.5,'b':.5}
    with pytest.raises(ReadoutUnsupported):conditional_distribution({'a':-1},('a','b'),.85)
    with pytest.raises(ReadoutUnsupported):conditional_distribution({'a':-1,'b':-2},('a','b'),0)


@pytest.mark.parametrize('mutation', ['order','description','evidence_version','evidence_range','counterevidence',
                                     'question_version','model_quantization','tokenizer','template','policy','profile_version','temperature','omission'])
def test_cache_key_binds_all_decision_dependencies(mutation):
    req=request();p=profile();original=compiled(req,p).cache_key;data=req.model_dump(mode='json')
    if mutation=='order':data['candidates'].reverse()
    if mutation=='description':data['candidates'][0]['description']+=' changed'
    if mutation=='evidence_version':data['evidence'][0]['ref']['version']='v2'
    if mutation=='evidence_range':data['evidence'][0]['end']=13
    if mutation=='counterevidence':data['counterevidence_refs'][0]['version']='v2'
    if mutation=='question_version':data['question_ref']['version']='v2'
    if mutation=='model_quantization':data['model_identity']['quantization']='fixture-q8'
    tokenizer=FixtureTokenizer()
    if mutation=='tokenizer':data['model_identity']['tokenizer_sha256']='6'*64;tokenizer.tokenizer_sha256='6'*64
    if mutation=='template':data['model_identity']['chat_template_sha256']='7'*64;tokenizer.chat_template_sha256='7'*64
    if mutation=='policy':data['policy_version']='policy-2'
    if mutation=='profile_version':p=p.model_copy(update={'version':'profile-v2'})
    if mutation=='temperature':p=p.model_copy(update={'readout_temperature':1.1})
    if mutation=='omission':data['omitted_refs']=[ref('observation','omitted')];data['input_completeness']='partial'
    newer=DecisionRequest.model_validate(data)
    assert compiled(newer,p,tokenizer).cache_key!=original


@pytest.mark.parametrize('mutation', ['candidate_order','question','range','run','attempt','request_id','model'])
def test_late_result_cannot_apply_to_changed_input(mutation):
    req=request();c=compiled(req);result=evaluate_fixture(req,c,response(c));data=req.model_dump(mode='json')
    if mutation=='candidate_order':data['candidates'].reverse()
    if mutation=='question':data['question_ref']['version']='v2'
    if mutation=='range':data['evidence'][0]['start']=5
    if mutation=='run':data['run_id']='next-run'
    if mutation=='attempt':data['attempt_id']='attempt-2'
    if mutation=='request_id':data['request_id']='request-2'
    if mutation=='model':data['model_identity']['weights_sha256']='8'*64
    gate=applicable_advice(result,DecisionRequest.model_validate(data))
    assert gate=={'applicable':False,'reason':'late_or_changed_request','chosen_id':None}


def test_even_current_fixture_never_authorizes_policy_or_skip_required_review():
    req=request();c=compiled(req);result=evaluate_fixture(req,c,response(c))
    assert applicable_advice(result,req,expected_cache_key=c.cache_key)['reason']=='D1_advisory_only'
    with pytest.raises(ValidationError):DecisionResult.model_validate({**result.model_dump(),'policy_applied':True})
    with pytest.raises(ValidationError):DecisionResult.model_validate({**result.model_dump(),'chosen_id':'outside'})
    with pytest.raises(ValidationError):DecisionResult.model_validate({**result.model_dump(),'missing_candidate_ids':['need-input']})
    with pytest.raises(ValidationError):DecisionResult.model_validate({**result.model_dump(),'probabilities':None})
    with pytest.raises(ValidationError):DecisionResult.model_validate({**result.model_dump(),'chosen_id':None})
    with pytest.raises(ValidationError):DecisionResult.model_validate({**result.model_dump(),'raw_logprobs':{'outside':-1}})


@pytest.mark.parametrize('origin',['documentation','fixture','installed_probe'])
def test_all_passed_fixture_or_unattested_probe_never_issues_certificate(origin):
    observation=CapabilityObservation(model_identity=identity(),readout_profile=profile(),origin=origin,
        checks={k:True for k in REQUIRED_CHECKS},probe_record_refs=('supplied-record',))
    report=inspect_capabilities(observation)
    assert not report.runtime_supported and report.capability_certificate_id is None
    assert not report.missing_checks and report.checks_passed==REQUIRED_CHECKS
    provider=DecisionProvider(DecisionConnection(mode='shadow',model_identity=identity(),resource_group_id='fixture-gpu',rights_status='explicitly_reviewed'))
    assert provider.decide(request(),profile(),observation=observation).status=='unsupported'


def test_unknown_and_failed_capability_checks_remain_explicit():
    report=inspect_capabilities(CapabilityObservation(model_identity=identity(),readout_profile=profile(),origin='fixture',
        checks={'complete_candidate_scores':False,'first_output_position':None}))
    assert 'complete_candidate_scores' in report.unsupported_checks
    assert 'first_output_position' in report.unknown_checks and report.capability_certificate_id is None
    with pytest.raises(ValidationError):CapabilityObservation(model_identity=identity(),readout_profile=profile(),origin='fixture',
        checks={'complete_candidate_scores':'true'})


def test_compile_requires_exact_single_unique_tokens_and_exact_input_budget():
    class Multi(FixtureTokenizer):
        def encode(self,text):return [1,2] if text=='A' else super().encode(text)
    class Duplicate(FixtureTokenizer):
        def encode(self,text):return [1] if text in ('A','B') else super().encode(text)
    with pytest.raises(ReadoutUnsupported,match='exactly_one'):compiled(tokenizer=Multi())
    with pytest.raises(ReadoutUnsupported,match='not_unique'):compiled(tokenizer=Duplicate())
    with pytest.raises(ReadoutUnsupported,match='context'):compiled(p=profile(context_tokens=8))
    with pytest.raises(ReadoutUnsupported,match='reservation'):compiled(request(input_token_reservation=1))
    tokenizer=FixtureTokenizer();tokenizer.tokenizer_sha256='f'*64
    with pytest.raises(ReadoutUnsupported,match='identity_mismatch'):compiled(tokenizer=tokenizer)


def test_payload_has_one_token_no_schema_mask_or_sampler_topk_and_no_network_client():
    req=request();c=compiled(req);payload=ollama_payload(req,c)
    assert payload['raw'] and payload['think'] is False and not payload['stream']
    assert payload['logprobs'] and payload['top_logprobs']==20 and payload['options']['num_predict']==1
    assert not set(payload)&{'format','tools','images','allowed_token_ids','logprob_token_ids'}
    assert 'top_k' not in payload['options']
    with pytest.raises(ReadoutUnsupported):UnsupportedResourceBroker().lease('fixture-gpu','attempt')


def test_candidate_limit_has_no_chunking_or_partial_choice_fallback():
    candidates=[{'id':str(i),'label':str(i),'description':'fixture'} for i in range(20)]
    candidates.append({'id':'unknown','label':'자료 부족','description':'fixture','disposition':'abstain'})
    result=DecisionProvider().decide_fixture(request(candidates=candidates),profile(),FixtureTokenizer(),{})
    assert result.status=='unsupported' and result.reason=='candidate_limit_no_split_fallback'
    assert result.probabilities is None and result.chosen_id is None


def test_full_vocabulary_mass_inconsistent_scores_are_rejected():
    req=request();c=compiled(req);r=response(c)
    r['logprobs'][0]['logprob']=-.1
    for top in r['logprobs'][0]['top_logprobs']:top['logprob']=-.1
    result=evaluate_fixture(req,c,r)
    assert result.status=='unsupported' and result.reason=='candidate_mass_exceeds_vocabulary_distribution'


def test_rights_unknown_decision_is_unavailable_without_stopping_generation_configuration():
    connection=DecisionConnection(mode='shadow',model_identity=identity(),resource_group_id='fixture-gpu')
    result=DecisionProvider(connection).decide(request(),profile())
    assert result.status=='unsupported' and result.reason=='rights_review_required'
    assert DecisionConnection().mode=='disabled'


def test_immutable_contract_keeps_every_required_review_ref_and_unknown_cost_null():
    req=request()
    with pytest.raises(ValidationError):req.policy_version='changed'
    assert req.required_review_refs and req.candidate_coverage=='not_guaranteed'
    result=DecisionProvider().decide(req,profile())
    assert result.usage.input_tokens is None and result.usage.output_tokens is None
    assert digest(req)==digest(request())
