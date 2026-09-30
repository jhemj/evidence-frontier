"""B1 pure compiler and native/wrapper boundary contracts; all models mocked."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
import hashlib
import json

import httpx
import pytest

from workbench.prompt_budget import measured
from workbench.provider import Provider
from workbench.request_compiler import (compile_request, compile_spec, compile_codex_wrapper,
    request_spec, RequestBudgetError, REVIEW_QUESTIONS)
from workbench.review_context import fit_metadata_only, model_view_size, serialize
from workbench import review_stream


CONFIG={'protocol':'ollama','base_url':'http://127.0.0.1:11434','model':'compiler-fixture'}


@pytest.fixture(autouse=True)
def isolated_model_configuration(monkeypatch):
    for name in ('FRONTIER_MODEL_DIGEST','MODEL_RELAY_URL','MODEL_API_KEY','MODEL_SECONDARY_API_KEY'):
        monkeypatch.delenv(name,raising=False)


def sources():
    return {'observations':[{'id':'OBSERVATION-0123456789ab',
        'timestamp':None,'fields':{'path':'/fixture/source','excerpt':'exact source; contrary context',
            'source_sha256':'a'*64,'partition_offset':0,'inode':12}}],
        'allowed_observation_ids':['OBSERVATION-0123456789ab'],
        'required_dossiers':[{'id':'DOSSIER-0123456789ab','title':'Bounded fixture question',
            'observation_ids':['OBSERVATION-0123456789ab']}],
        'allowed_observation_ids_by_dossier':{'DOSSIER-0123456789ab':['OBSERVATION-0123456789ab']},
        'executed_checks':[]}


def native_provider(handler,config=None,role='analyst'):
    instance=Provider(config or CONFIG)
    instance.select_role(role)
    instance.client.close()
    instance.client=httpx.Client(transport=httpx.MockTransport(handler))
    return instance


def answer(request):
    return httpx.Response(200,json={'done_reason':'stop','message':{
        'content':json.dumps({'summary':'source-bound mock answer','claims':[]})},
        'prompt_eval_count':90,'eval_count':12})


def stream_fixture(pack=None,config=None):
    canonical=deepcopy(pack or sources())
    return {'id':'stream','task_id':'task','batch_id':'batch','canonical':canonical,
        'canonical_sha256':review_stream.fingerprint(canonical),'page_count':1,
        'maximum':36000,'request_spec':request_spec(config or CONFIG,REVIEW_QUESTIONS['judgment'],'judgment'),
        'open_objections':[]}


def test_compile_is_side_effect_free_and_native_transport_uses_identical_bytes(monkeypatch):
    pack=sources();config=deepcopy(CONFIG);before=deepcopy(pack)
    # Compiling must not instantiate a client, resolve an endpoint or use Store.
    with monkeypatch.context() as pure:
        pure.setattr('socket.getaddrinfo',lambda *a,**k:pytest.fail('compiler contacted an endpoint'))
        pure.setattr('httpx.Client',lambda *a,**k:pytest.fail('compiler created a client'))
        compiled=compile_request(config,'fixture question',pack)
    assert pack==before and config==CONFIG
    seen=[]
    def transport(request):
        seen.append(request.content)
        assert request.headers['Content-Type']=='application/json'
        assert request.url.path==compiled.path
        return answer(request)
    output,receipt=native_provider(transport).generate('fixture question',pack,compiled_request=compiled)
    assert seen==[compiled.payload_json.encode()]
    assert receipt['compiled_request']==compiled.identity
    assert receipt['compiled_request']['request_body_sha256']==hashlib.sha256(seen[0]).hexdigest()
    assert receipt['prompt_budget']['request_utf8_bytes']==len(seen[0])
    assert output['summary']=='source-bound mock answer'
    assert pack==before


def test_compiled_snapshot_and_reference_binding_are_immutable():
    pack=sources();compiled=compile_request(CONFIG,'question',pack)
    payload=compiled.payload;payload['messages'][1]['content']='changed'
    schema=compiled.output_schema;schema.clear()
    receipt=compiled.references.receipt();receipt['bindings'].clear()
    assert compiled.payload['messages'][1]['content']!='changed' and compiled.output_schema
    assert compiled.references.receipt()['bindings']
    with pytest.raises(FrozenInstanceError):compiled.payload_json='replacement'
    with pytest.raises(FrozenInstanceError):compiled.references.decode_map={}
    with pytest.raises(TypeError):compiled.references.decode_map['R1']='other-source'
    handle=compiled.references.encode_map[pack['observations'][0]['id']]
    assert compiled.references.decode({'observation_ids':[handle]})['observation_ids']==pack['allowed_observation_ids']


@pytest.mark.parametrize('changed',['question','source','role','model','budget'])
def test_stale_compilation_rejects_before_model_request(changed):
    pack=sources();config=deepcopy(CONFIG);question='question';role='analyst'
    compiled=compile_request(config,question,pack,role)
    if changed=='question':question='different question'
    elif changed=='source':pack['observations'][0]['fields']['excerpt']+=' newly returned result'
    elif changed=='role':role='falsifier'
    elif changed=='model':config['model']='other-fixture'
    else:config['assistant_num_predict']=2600
    phases=[]
    instance=native_provider(lambda request:pytest.fail('stale compiler caused transport'),config)
    with pytest.raises(ValueError,match='does not match'):
        instance.generate(question,pack,role=role,compiled_request=compiled,
            emit=lambda phase,**meta:phases.append(phase))
    assert not {'availability_check','dispatch_attempted','response_waiting'} & set(phases)
    assert instance._request_attempted is False


def test_transport_envelope_mismatch_is_not_a_dispatch_attempt():
    compiled=compile_request(CONFIG,'question',sources());phases=[]
    instance=native_provider(lambda request:pytest.fail('mismatch caused transport'))
    instance._active_compiled=compiled;instance._request_attempted=False
    instance.bind_lifecycle('attempt',lambda phase,**meta:phases.append(phase))
    payload=compiled.payload;payload['messages'][1]['content']+='changed'
    with pytest.raises(ValueError,match='transport envelope differs'):
        instance.response(compiled.path,payload)
    assert phases==[] and instance._request_attempted is False
    instance.client.close()


def test_complete_template_schema_and_output_reserve_can_block_a_small_pack():
    pack=sources();before=deepcopy(pack)
    low={**CONFIG,'num_ctx':8192,'num_predict':4000}
    compiled=compile_request(low,REVIEW_QUESTIONS['judgment'],pack,'judgment')
    assert model_view_size(pack)<36000
    assert compiled.budget['estimated_headroom']<0
    # All original source fields remain even when the full envelope cannot fit.
    with pytest.raises(RequestBudgetError) as caught:
        fit_metadata_only(pack,36000,request_spec=request_spec(low,REVIEW_QUESTIONS['judgment'],'judgment'))
    assert review_stream.resolved(pack)['observations']==before['observations']
    assert caught.value.metadata['delivery_state']=='not_sent'
    assert caught.value.metadata['request_attempted'] is False
    assert 'exact source; contrary context' not in serialize(caught.value.metadata)
    phases=[]
    instance=native_provider(lambda request:pytest.fail('over-budget request dispatched'),low)
    with pytest.raises(RequestBudgetError):
        instance.generate(REVIEW_QUESTIONS['judgment'],before,role='judgment',
            emit=lambda phase,**meta:phases.append(phase))
    assert not {'availability_check','dispatch_attempted','response_waiting'} & set(phases)


def test_lossless_fit_returns_the_same_full_envelope_used_by_provider():
    pack=sources();before=deepcopy(pack);spec=request_spec(CONFIG,'question','analyst')
    fitted=fit_metadata_only(pack,36000,request_spec=spec)
    assert fitted.payload_json==compile_spec(spec,pack).payload_json
    assert fitted.matches(CONFIG,'question',pack,'analyst')
    assert review_stream.resolved(pack)['observations']==before['observations']
    _,receipt=native_provider(answer).generate('question',pack,compiled_request=fitted)
    assert receipt['compiled_request']==fitted.identity


def test_lifecycle_budget_keeps_accounting_basis_but_not_arbitrary_prose():
    from workbench.request_lifecycle import safe_metadata
    compiled=compile_request(CONFIG,'question',sources())
    budget=compiled.budget
    budget['tokenizer_version']='private arbitrary string'
    budget['estimate_basis']='private prose should not reach activity telemetry'
    safe=safe_metadata({'prompt_budget':budget})['prompt_budget']
    assert safe['count_basis']=='heuristic'
    assert safe['request_utf8_bytes']==compiled.identity['request_utf8_bytes']
    assert safe['template_sha256']==compiled.template_sha256
    assert safe['server_template_version'] is None
    assert 'private' not in serialize(safe) and 'tokenizer_version' not in safe


@pytest.mark.parametrize('phase',['source_page','focus_page','synthesis'])
def test_working_final_schema_and_messages_are_in_the_same_budget(phase):
    pack=sources();pack['review_stream']={'phase':phase}
    compiled=compile_request(CONFIG,REVIEW_QUESTIONS['judgment'],pack,'judgment')
    assert compiled.working==(phase!='synthesis')
    assert json.dumps(compiled.output_schema) in compiled.messages[0]['content']
    assert compiled.payload['messages']==compiled.messages
    assert compiled.payload['format']==compiled.output_schema
    budget=compiled.budget
    assert budget['prompt_characters']==sum(len(message['content']) for message in compiled.messages)
    assert budget['output_tokens_reserved']==compiled.payload['options']['num_predict']
    assert budget['estimated_headroom']==budget['context_tokens_requested']-budget['output_tokens_reserved']-budget['estimated_input_tokens']
    assert budget['count_basis']=='heuristic' and budget['tokenizer_version'] is None
    assert budget['server_template_version'] is None and not budget['server_retained_full_input_verified']
    observed=measured(budget,{'prompt_eval_count':100,'eval_count':10})
    assert observed['exact_input_tokens']==100 and not observed['server_retained_full_input_verified']


def test_role_route_and_protocol_are_bound_without_fallback():
    config={**CONFIG,'secondary':{'protocol':'openai_compatible','base_url':'http://127.0.0.1:8080',
        'model':'secondary-fixture'},
        'role_routes':{'falsifier':'secondary'}}
    compiled=compile_request(config,'question',sources(),'falsifier')
    assert compiled.model=='secondary-fixture' and compiled.path=='/chat/completions'
    assert compiled.payload['response_format']=={'type':'json_object'}
    assert compiled.payload['max_tokens']==compiled.budget['output_tokens_reserved']
    assert compiled.matches(config,'question',sources(),'falsifier')
    def transport(request):
        assert request.content==compiled.payload_json.encode()
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{
            'content':json.dumps({'alternatives':[],'contradicting_observation_ids':[],'missing_checks':[]})}}]})
    _,receipt=native_provider(transport,config,role='falsifier').generate('question',sources(),role='falsifier',compiled_request=compiled)
    assert receipt['model_slot']=='secondary' and receipt['model']=='secondary-fixture'


def test_luna_cli_wrapper_bytes_are_exact_but_not_native_budget_proof():
    from scripts.luna_test_provider import _strict_schema
    compiled=compile_request(CONFIG,'question',sources())
    wrapper=compile_codex_wrapper(compiled.messages,compiled.output_schema,schema_transform=_strict_schema)
    assert wrapper.request_json==json.dumps({'messages':compiled.messages},ensure_ascii=False,sort_keys=True,separators=(',',':'))
    assert wrapper.schema_json==json.dumps(_strict_schema(compiled.output_schema),ensure_ascii=False,sort_keys=True,separators=(',',':'))
    assert wrapper.identity['input_utf8_bytes']==len(wrapper.request_json.encode())
    assert wrapper.identity['input_sha256']==hashlib.sha256(wrapper.request_json.encode()).hexdigest()
    assert wrapper.identity['input_sha256']!=compiled.identity['request_body_sha256']
    assert 'remote template' in wrapper.identity['input_envelope_basis']
    assert 'exact_input_tokens' not in wrapper.identity


def test_smaller_source_can_still_have_a_larger_whole_request():
    before=sources();before['observations'][0]['fields']['excerpt']='original context '*150
    after=deepcopy(before);after['observations'][0]['fields']['excerpt']='exact source'
    after['page_review_notes']=[{'summary':'new derived working explanation '*500}]
    spec=request_spec(CONFIG,REVIEW_QUESTIONS['judgment'],'judgment')
    old=review_stream.comparison_cost(before,request_spec=spec)
    new=review_stream.comparison_cost(after,request_spec=spec)
    assert len(after['observations'][0]['fields']['excerpt'])<len(before['observations'][0]['fields']['excerpt'])
    assert new['request_budget']['request_utf8_bytes']>old['request_budget']['request_utf8_bytes']
    assert new['request_budget']['estimated_input_tokens']>old['request_budget']['estimated_input_tokens']
    assert new['compiled_request']==compile_spec(spec,after).identity
    assert 'original context' not in serialize(old) and 'new derived working' not in serialize(new)


def test_mandatory_obligation_floor_cannot_create_more_focus_pages():
    pack=sources();pack['observations'][0]['fields']['excerpt']='full contrary source '*5000
    pack['executed_checks']=[{'id':'check','observation_ids':pack['allowed_observation_ids'],
        'contracts':[{'contract_id':'contract','success_condition':'preserve exact predicate',
            'refutation_condition':'preserve contrary outcome','inconclusive_condition':'preserve gap'}]}]
    stream=stream_fixture(pack)
    stream['open_objections']=[{'id':'objection','observation_ids':pack['allowed_observation_ids'],
        'span_ids':[],'reason':'material contrary original remains unresolved'}]
    before=deepcopy(stream)
    class NoWrites:
        def tx(self):pytest.fail('an irreducible floor scheduled another focus iteration')
    pages=[{'id':'page','included_ids':pack['allowed_observation_ids'],'pack':pack,'receipt_id':'receipt',
        'output':{'findings':[{'observation_ids':pack['allowed_observation_ids']}],'check_assessments':[]}}]
    with pytest.raises(RequestBudgetError):review_stream._schedule_comparisons(NoWrites(),'fixture',stream,pages)
    assert stream==before


def test_contract_only_irreducible_floor_blocks_without_dropping_conditions():
    pack=sources();pack['executed_checks']=[{'id':'check','observation_ids':[],
        'contracts':[{'contract_id':'contract','success_condition':'mandatory discriminating condition '*4000}]}]
    stream=stream_fixture(pack);before=deepcopy(stream)
    with pytest.raises(RequestBudgetError):review_stream.mandatory_floor(stream)
    assert stream==before


def test_focus_rejection_compares_whole_request_not_only_smaller_excerpt():
    before=sources();before['observations'][0]['fields']['excerpt']='original source context '*100
    stream=stream_fixture(before,{**CONFIG,'num_ctx':65536})
    old={'id':'old','included_ids':before['allowed_observation_ids'],'pack':before,'receipt_id':'receipt',
        'output':{'summary':'old concise note','findings':[{'observation_ids':before['allowed_observation_ids']}],
            'check_assessments':[]}}
    page={'id':'focus','focus_of':'old','focus_budget_chars':100}
    selected=deepcopy(before);selected['observations'][0]['fields']['excerpt']='exact source'
    output={'summary':'new derived explanation '*900,
        'findings':[{'observation_ids':before['allowed_observation_ids']}],'check_assessments':[]}
    class Memory:
        def get(self,key):return deepcopy({'stream':stream,'focus':page,'old':old}[key])
    errors=review_stream.focus_budget_errors(Memory(),{'phase':'focus_page','stream_id':'stream','page_id':'focus'},
        before,output,selected)
    assert len(errors)==1 and errors[0]['code']=='focus_selection_over_budget'
    assert errors[0]['comparison_cost_after']['request_budget']['request_utf8_bytes']>errors[0]['comparison_cost_before']['request_budget']['request_utf8_bytes']
    assert 'UTF-8 bytes' in errors[0]['detail']
    assert stream['canonical']==before


def test_mandatory_floor_preserves_contracts_and_exact_pinned_spans():
    from workbench.review_focus import project
    pack=sources();pack['observations'][0]['fields']['excerpt']='unselected large context '*5000+' decisive contrary line'
    row=pack['observations'][0]
    selected=project(pack,{'findings':[{'observation_ids':[row['id']]}],
        'excerpt_selections':[{'observation_id':row['id'],'quote':'decisive contrary line'}]})
    _,span=next(review_stream.span_rows(selected['observations'][0]))
    # The floor must use the exact recorded fragment, not fabricate a summary
    # and not accidentally pull the entire unrelated retained field back in.
    stream=stream_fixture(pack)
    stream['source_span_catalog']={span['source_span']['span_id']:span}
    stream['open_objections']=[{'id':'objection','observation_ids':[row['id']],
        'span_ids':[span['source_span']['span_id']],'reason':'exact contrary fragment'}]
    stream['canonical']['executed_checks']=[{'id':'check','observation_ids':[row['id']],
        'contracts':[{'contract_id':'contract','success_condition':'exact predicate retained'}]}]
    before=deepcopy(stream)
    floor=review_stream.mandatory_floor(stream)
    assert floor['request_budget']['estimated_headroom']>=0 and stream==before
    material=review_stream.resolved(review_stream.reduction_pack(stream,[],final=True))
    assert material['observations'][0]['fields']['excerpt']=='decisive contrary line'
    assert material['executed_checks'][0]['contracts']==stream['canonical']['executed_checks'][0]['contracts']
    assert material['open_objections']==stream['open_objections']
