import json

import httpx
import pytest

from workbench.models import review_output_schema, WorkingReview, WorkingSynthesis, JudgmentReport, FactAssertion
from workbench.provider import Provider
from workbench.semantic_contract import assertion_errors


def test_working_schema_is_distinct_from_reader_card_and_final_unchanged():
    pack={'review_stream':{'phase':'focus_page'}}
    assert review_output_schema('judgment',pack) is WorkingReview
    assert review_output_schema('synthesis',pack) is WorkingSynthesis
    assert review_output_schema('judgment',{'review_stream':{'phase':'synthesis'}}) is JudgmentReport
    fields=WorkingReview.model_json_schema()['$defs']['WorkingFinding']['properties']
    assert not {'card_summary','incident_relevance','change_reason','timeline_role'} & set(fields)
    assert {'fact_assertions','counterevidence_ids','stages','basis'}<=set(fields)
    assert 'source_selections' in WorkingReview.model_json_schema()['required']
    required=WorkingReview.model_json_schema()['$defs']['WorkingCheckAssessment']['required']
    assert {'dossier_id','contract_id'}<=set(required)


def test_working_provider_omits_final_card_directives_but_keeps_source_guards():
    p=Provider({'protocol':'ollama','base_url':'http://127.0.0.1:11434','model':'fixture'})
    p.client.close()
    def respond(request):
        data=json.loads(request.content);system=data['messages'][0]['content']
        assert isinstance(data['format'],dict)
        assert data['format']['properties']['source_selections']['maxItems']==0
        assert 'unpublished source working note' in system
        assert 'Write card_summary as 2-3' not in system
        assert 'Source-page judgments cannot resolve objections' in system
        assert 'absence' in system and 'temporal' in system.lower()
        return httpx.Response(200,json={'done_reason':'stop','message':{'content':json.dumps({
            'summary':'Source-bound note','findings':[{'dossier_id':'d','title':'Fact','judgment':'미확인',
                'reason':'Only the provided scope.'}],'source_selections':[]})}})
    p.client=httpx.Client(transport=httpx.MockTransport(respond))
    out,_=p.generate('review',{'target_os':'linux','observations':[],'review_stream':{'phase':'source_page'}},role='judgment')
    assert 'card_summary' not in out['findings'][0]
    assert out['source_selections']==[]


def test_json_compatibility_is_explicit_and_schema_rejection_never_downgrades():
    from test_investigator_strategy import provider
    p,requests=provider({'structured_output':'json'})
    _,receipt=p.generate('fixture',{})
    assert requests[0][1]['format']=='json'
    assert receipt['generation_settings']['output_format']=='json'
    p,requests=provider();p.client.close();calls=[]
    def reject(request):
        calls.append(json.loads(request.content))
        return httpx.Response(400,json={'error':'schema not supported'})
    p.client=httpx.Client(transport=httpx.MockTransport(reject))
    with pytest.raises(ValueError,match='schema not supported'):p.generate('fixture',{})
    assert len(calls)==1 and isinstance(calls[0]['format'],dict)


def test_exact_finite_source_number_is_accepted_without_rounding_or_time_inference():
    fact=FactAssertion.model_validate({'observation_id':'o','pointer':'/fields/mtime','value':1779777805.558033}).model_dump()
    finding={'observation_ids':['o'],'fact_assertions':[fact]}
    sources={'o':{'fields':{'mtime':1779777805.558033}}}
    assert not assertion_errors(finding,sources,{'o'})
    fact['value']=1779777805.558034
    assert assertion_errors(finding,sources,{'o'})
    for value in (float('nan'),float('inf')):
        with pytest.raises(ValueError):FactAssertion(observation_id='o',pointer='/fields/mtime',value=value)


def test_review_cardinality_follows_current_dossiers_not_a_hypothesis_count():
    pack={'review_stream':{'phase':'source_page'},'required_dossiers':[{'id':'d'}]}
    schema=review_output_schema('judgment',pack)
    prop=schema.model_json_schema()['properties']['findings']
    assert prop['minItems']==prop['maxItems']==1
    finding={'dossier_id':'d','title':'Recorded metadata','judgment':'미확인','reason':'Bounded.'}
    value={'summary':'Working note','findings':[finding],'source_selections':[]}
    schema.model_validate(value)
    with pytest.raises(ValueError):schema.model_validate({**value,'findings':[finding,finding]})
    pack['required_dossiers']=[{'id':str(i)} for i in range(11)]
    assert review_output_schema('judgment',pack).model_json_schema()['properties']['findings']['maxItems']==11


def test_source_actions_follow_available_source_fields_not_source_type_names():
    pack={'review_stream':{'phase':'source_page'},'required_dossiers':[{'id':'d'}],
        'observations':[{'id':'o','fields':{'size':12,'path':'/any'}}]}
    schema=review_output_schema('judgment',pack).model_json_schema()
    assert schema['properties']['source_selections']['maxItems']==0
    pack['observations'][0]['fields']['excerpt']='any actual text'
    schema=review_output_schema('judgment',pack).model_json_schema()
    assert schema['properties']['source_selections'].get('maxItems')!=0


def test_source_selection_has_one_mode_and_no_legacy_parallel_arrays():
    definitions=WorkingReview.model_json_schema()['$defs']
    # Grammar decoders can use schema property order: decide mode before shared
    # fields instead of accidentally choosing a branch from a key-order prefix.
    for name in ('SourceExcerptChoice','SourceMetadataChoice'):
        assert next(iter(definitions[name]['properties']))=='mode'
    value={'summary':'Working note','findings':[{'dossier_id':'d','title':'Metadata','judgment':'미확인','reason':'Limited.'}],
        'source_selections':[{'observation_id':'o','mode':'metadata_only','reason':'No content claim.'}]}
    WorkingReview.model_validate(value)
    with pytest.raises(ValueError,match='one source mode'):
        WorkingReview.model_validate({**value,'source_selections':value['source_selections']*2})
    with pytest.raises(ValueError):WorkingReview.model_validate({**value,'excerpt_selections':[]})


def test_generation_requires_explicit_fields_without_changing_legacy_receipts():
    from workbench.models import generation_schema
    original=JudgmentReport.model_json_schema()
    schema=generation_schema(JudgmentReport,final_review=True)
    finding=schema['$defs']['JudgmentFinding']
    assert set(finding['required'])==set(finding['properties'])
    assert {'dossier_id','observation_ids','stages','alternatives','remaining_checks'}<=set(finding['required'])
    check=schema['$defs']['CheckAssessment']
    assert {'dossier_id','contract_id','observation_ids'}<=set(check['required'])
    assert schema['properties']['excerpt_selections']['maxItems']==0
    assert schema['properties']['source_metadata_selections']['maxItems']==0
    assert 'default' not in finding['properties']['dossier_id']
    assert JudgmentReport.model_json_schema()==original
    # Unknown/empty is still a decision, never fabricated corroboration.
    assert 'minItems' not in finding['properties']['observation_ids']
    working=generation_schema(WorkingReview)
    for name in ('SourceExcerptChoice','SourceMetadataChoice'):
        assert next(iter(working['$defs'][name]['properties']))=='mode'
