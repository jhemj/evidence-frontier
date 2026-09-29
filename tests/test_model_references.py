from copy import deepcopy
import json

import httpx
import pytest

from workbench.model_references import ReferenceProjection
from workbench.provider import ModelOutputError, Provider
from workbench.review_validation import errors, check_errors

O1 = 'OBSERVATION-123456789abc'
O2 = 'OBSERVATION-123456789abd'
D1 = 'DOSSIER-123456789abc'
D2 = 'DOSSIER-123456789abd'
C1 = 'a' * 64
J1 = 'INVESTIGATION_JOB-123456789abc'


def fixture():
    return {'observations': [{'id': O1, 'fields': {'excerpt': O2, 'value': 'R1', 'id': O2}},
                             {'id': O2, 'fields': {'path': '/two'}}],
            'allowed_observation_ids': [O1, O2],
            'allowed_observation_ids_by_dossier': {D1: [O1], D2: [O2]},
            'required_dossiers': [{'dossier_id': D1}, {'dossier_id': D2}],
            'executed_checks': [{'id': J1, 'observation_ids': [O1],
                                 'contracts': [{'dossier_id': D1, 'contract_id': C1}]}],
            'literal_fact_candidates': [{'observation_id': O1, 'pointer': '/fields/excerpt', 'value': O2}]}


def test_exact_roundtrip_structural_references_never_change_literals():
    pack = fixture(); original = deepcopy(pack); p = ReferenceProjection(pack); encoded = p.encode(pack)
    assert pack == original
    assert encoded['observations'][0]['fields'] == pack['observations'][0]['fields']
    assert encoded['literal_fact_candidates'][0]['value'] == O2
    assert encoded['literal_fact_candidates'][0]['observation_id'] == p.encode_map[O1]
    assert encoded['allowed_observation_ids_by_dossier'][p.encode_map[D1]] == [p.encode_map[O1]]
    assert encoded['executed_checks'][0]['contracts'][0]['contract_id'] == p.encode_map[C1]
    result = {'findings': [{'dossier_id': p.encode_map[D1], 'observation_ids': [p.encode_map[O1]],
                           'fact_assertions': encoded['literal_fact_candidates']}]}
    decoded = p.decode(result)
    assert decoded['findings'][0]['observation_ids'] == [O1]
    assert decoded['findings'][0]['fact_assertions'][0]['value'] == O2
    assert ReferenceProjection(pack).receipt() == p.receipt()
    assert p.receipt()['bindings'][p.encode_map[O1]] == O1


def test_unknown_handle_and_near_match_never_repair_citations():
    p = ReferenceProjection(fixture())
    with pytest.raises(ValueError, match='Unknown'):
        p.decode({'observation_ids': ['R99999']})
    wrong = O1[:-1]
    assert p.decode({'observation_ids': [wrong]})['observation_ids'] == [wrong]
    # A source string named R1 is not an instruction or a structural citation.
    assert p.decode({'value': 'R1', 'title': 'R99999'}) == {'value': 'R1', 'title': 'R99999'}


def test_existing_structural_identifiers_cannot_collide_with_handles():
    pack=fixture();pack['allowed_observation_ids'].append('R1')
    p=ReferenceProjection(pack)
    assert 'R1' not in p.decode_map
    assert p.decode({'observation_ids':['R1']})['observation_ids']==['R1']


def test_generated_narrative_references_decode_but_evidence_and_tool_literals_do_not():
    p=ReferenceProjection(fixture());handle=p.encode_map[O1]
    out=p.decode({'reason':f'{handle}은 기록이며 x{handle} 또는 {handle}x는 아니다.',
                  'alternatives':[f'({handle})'], 'value':handle, 'path':handle, 'query':handle,
                  'fields':{'excerpt':handle}})
    assert out['reason']==f'{O1}은 기록이며 x{handle} 또는 {handle}x는 아니다.'
    assert out['alternatives']==[f'({O1})']
    assert out['value']==out['path']==out['query']==out['fields']['excerpt']==handle


def test_exact_handle_resolution_does_not_grant_cross_dossier_or_check_scope():
    p = ReferenceProjection(fixture())
    output = p.decode({'findings': [{'dossier_id': p.encode_map[D1], 'judgment': '미확인',
                                    'observation_ids': [p.encode_map[O2]]}]})
    assert any(e['code'] == 'unrelated_observation' for e in errors(output, [D1], [O1,O2], {D1:[O1]}))
    output = p.decode({'check_assessments': [{'check_id': p.encode_map[J1], 'dossier_id':p.encode_map[D1],
        'contract_id':p.encode_map[C1], 'outcome':'inconclusive', 'observation_ids':[p.encode_map[O2]]}]})
    assert any(e['code']=='check_citation_scope' for e in check_errors(output, fixture()['executed_checks'], {D1:[O1,O2]}))


def test_provider_sends_handles_returns_canonical_and_records_binding():
    p = Provider({'protocol':'ollama','base_url':'http://127.0.0.1:11434','model':'fixture'})
    p.client.close(); pack=fixture(); projection=ReferenceProjection(pack)
    def respond(request):
        data=json.loads(request.content); sent=json.loads(data['messages'][1]['content'])['evidence_pack']
        assert sent['observations'][0]['id']==projection.encode_map[O1]
        assert sent['observations'][0]['fields']==pack['observations'][0]['fields']
        return httpx.Response(200,json={'done_reason':'stop','message':{'content':json.dumps({
            'alternatives':[], 'contradicting_observation_ids':[sent['observations'][0]['id']], 'missing_checks':[]})}})
    p.client=httpx.Client(transport=httpx.MockTransport(respond))
    out,receipt=p.generate('question',pack,role='falsifier')
    assert out['contradicting_observation_ids']==[O1]
    assert receipt['output']==out
    assert receipt['reference_projection']==projection.receipt()
    assert receipt['model_reference_output']['contradicting_observation_ids']==[projection.encode_map[O1]]


def test_provider_bad_handle_fails_closed_with_replay_binding():
    p=Provider({'protocol':'ollama','base_url':'http://127.0.0.1:11434','model':'fixture'});p.client.close()
    p.client=httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(200,json={
        'done_reason':'stop','message':{'content':json.dumps({'alternatives':[],
          'contradicting_observation_ids':['R99999'],'missing_checks':[]})}})))
    with pytest.raises(ModelOutputError) as error:
        p.generate('question',fixture(),role='falsifier')
    assert error.value.category=='output_reference'
    assert error.value.metadata['reference_projection']['bindings']


def test_input_budget_matches_exact_wire_projection_without_shortening_sources():
    from workbench.review_context import fit_metadata_only, model_view_size, serialize, InputBudgetError
    pack=fixture();pack['observations'][0]['fields']['excerpt']='unchanged full source ' * 70
    original=deepcopy(pack);wire=model_view_size(pack)
    assert wire<len(serialize(pack))
    fit_metadata_only(pack, maximum=wire)
    assert pack==original and model_view_size(pack)==wire
    with pytest.raises(InputBudgetError):
        fit_metadata_only(deepcopy(original), maximum=500)


def test_provider_and_budget_use_identical_lossless_tables_before_scope_validation():
    from workbench.model_tables import decode, MANIFEST
    from workbench.review_context import model_view_size, serialize
    pack={'observations':[{'id':f'OBSERVATION-{i:012x}', 'type':'fixture',
        'source_location':f'fixture:{i}', 'fields':{'excerpt':f'원문 {i}\r\n','value':None}}
        for i in range(30)]}
    original=deepcopy(pack);projection=ReferenceProjection(pack)
    p=Provider({'protocol':'ollama','base_url':'http://127.0.0.1:11434','model':'fixture'});p.client.close()
    def respond(request):
        data=json.loads(request.content);sent=json.loads(data['messages'][1]['content'])['evidence_pack']
        assert MANIFEST in sent
        assert len(serialize(sent))==model_view_size(pack)
        assert decode(sent)==projection.encode(pack)
        return httpx.Response(200,json={'done_reason':'stop','message':{'content':json.dumps({
            'alternatives':[], 'contradicting_observation_ids':[projection.encode_map[pack['observations'][17]['id']]],
            'missing_checks':[]})}})
    p.client=httpx.Client(transport=httpx.MockTransport(respond))
    output,receipt=p.generate('question',pack,role='falsifier')
    assert pack==original
    assert output['contradicting_observation_ids']==[pack['observations'][17]['id']]
    assert receipt['table_projection']['version']=='model-tables-1'
