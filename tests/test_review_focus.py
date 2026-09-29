from copy import deepcopy
import hashlib

import pytest

from workbench.review_focus import project
from workbench.review_stream import reduction_pack, span_rows
from workbench.review_context import InputBudgetError


def fixture(text='앞문장\n허용 기록\n뒷문장'):
    row={'id':'o','type':'linux_tool_result','timestamp':None,'source_location':'retained.json',
         'fields':{'excerpt':text,'path':'/config','source_sha256':'source-hash',
                   'source_complete':False,'byte_offset':4096,'file_context':{'ctime_ns':123}}}
    pack={'observations':[row],'required_dossiers':[], 'executed_checks':[],
          'allowed_observation_ids_by_dossier':{}}
    output={'findings':[{'observation_ids':['o']}],
            'excerpt_selections':[{'observation_id':'o','quote':'허용 기록','reason':'Narrow recorded fact'}]}
    return pack,output


def test_quote_selection_is_exact_partial_and_keeps_original_identity():
    pack,output=fixture();before=deepcopy(pack)
    focused=project(pack,output);row=focused['observations'][0];span=row['fields']['excerpt_spans'][0]
    assert pack==before
    assert span['byte_start']==len('앞문장\n'.encode())
    assert span['byte_end']==len('앞문장\n허용 기록'.encode())
    assert span['full_sha256']==hashlib.sha256(before['observations'][0]['fields']['excerpt'].encode()).hexdigest()
    assert span['coordinate_basis']=='canonical_field_utf8'
    assert span['text']=='허용 기록'
    assert row['fields']['byte_offset']==4096
    assert row['fields']['source_sha256']=='source-hash'
    assert row['fields']['file_context']=={'ctime_ns':123}
    assert row['projection_omissions']['replacement']=='model_selected_exact_spans'


def test_full_presented_quote_is_identity_not_a_larger_partial_span():
    pack,output=fixture('whole command')
    output['excerpt_selections'][0]['quote']='whole command'
    assert project(pack,output)==pack
    # Even a no-op quote is validated; duplicate/mixed choices still fail.
    with pytest.raises(ValueError):project(pack,{**output,'excerpt_selections':output['excerpt_selections']*2})
    with pytest.raises(ValueError):project(pack,{**output,'source_metadata_selections':[{'observation_id':'o','reason':'metadata'}]})
    pack,output=fixture();selected=project(pack,output)
    sid=selected['observations'][0]['fields']['excerpt_spans'][0]['span_id']
    output['excerpt_selections'][0]['source_span_id']=sid
    assert project(selected,output)==selected


@pytest.mark.parametrize('outcome',['supports','refutes'])
def test_metadata_only_cannot_erase_positive_check_source_body(outcome):
    pack,output=fixture();output['excerpt_selections']=[]
    output['source_metadata_selections']=[{'observation_id':'o','reason':'Only metadata.'}]
    output['check_assessments']=[{'check_id':'check','outcome':outcome,'observation_ids':['o']}]
    with pytest.raises(ValueError,match='positive check outcome'):project(pack,output)
    output['check_assessments'][0]['outcome']='inconclusive'
    assert 'excerpt' not in project(pack,output)['observations'][0]['fields']


@pytest.mark.parametrize('change',[
    {'quote':'허용기록'}, {'quote':'허용 기록…'}, {'quote':''}, {'occurrence':1},
    {'occurrence':True}, {'observation_id':'foreign'}, {'source_span_id':'foreign'}])
def test_selection_rejects_invented_text_scope_and_coordinates(change):
    pack,output=fixture();output['excerpt_selections'][0].update(change)
    with pytest.raises(ValueError):project(pack,output)


def test_occurrence_counts_overlapping_literals_and_preserves_control_characters():
    pack,output=fixture('aaaa\x00\x00끝')
    output['excerpt_selections'][0].update(quote='aa',occurrence=2)
    span=project(pack,output)['observations'][0]['fields']['excerpt_spans'][0]
    assert (span['byte_start'],span['byte_end'])==(2,4)
    output['excerpt_selections'][0].update(quote='\x00\x00끝',occurrence=0)
    assert project(pack,output)['observations'][0]['fields']['excerpt_spans'][0]['text']=='\x00\x00끝'


def test_nested_selection_keeps_canonical_byte_range_and_full_digest():
    pack,output=fixture();selected=project(pack,output)
    span=selected['observations'][0]['fields']['excerpt_spans'][0]
    output['excerpt_selections'][0].update(source_span_id=span['span_id'],quote='기록')
    second=project(selected,output)['observations'][0]['fields']['excerpt_spans'][0]
    assert second['byte_start']==len('앞문장\n허용 '.encode())
    assert second['full_sha256']==span['full_sha256']
    assert second['sha256']==hashlib.sha256('기록'.encode()).hexdigest()


def test_atomic_source_choice_and_unambiguous_parent_are_exact_not_guessed():
    pack,old=fixture();selected=project(pack,old)
    output={'findings':old['findings'],'source_selections':[{'observation_id':'o','mode':'excerpt',
        'reason':'A precise nested quote','passages':[{'quote':'기록','occurrence':0}]}]}
    result=project(selected,output)['observations'][0]['fields']['excerpt_spans'][0]
    assert result['byte_start']==len('앞문장\n허용 '.encode())
    output['source_selections'][0]['passages'][0]['source_span_id']='wrong'
    with pytest.raises(ValueError):project(selected,output)
    output['source_selections']=[{'observation_id':'o','mode':'metadata_only','reason':'Metadata only.'}]
    assert 'excerpt_spans' not in project(selected,output)['observations'][0]['fields']
    with pytest.raises(ValueError):project(selected,{**output,'source_selections':output['source_selections']*2})


def test_parent_cannot_be_inferred_when_multiple_fragments_are_presented():
    pack,output=fixture();output['excerpt_selections'].append({'observation_id':'o','quote':'뒷문장','reason':'Contrary context'})
    selected=project(pack,output)
    request={'findings':output['findings'],'source_selections':[{'observation_id':'o','mode':'excerpt','reason':'Selection',
        'passages':[{'quote':'기록'}]}]}
    with pytest.raises(ValueError,match='parent span'):project(selected,request)


def test_selected_excerpt_cannot_discard_note_literal():
    pack,output=fixture()
    output['findings'][0]['fact_assertions']=[{'observation_id':'o','pointer':'/fields/excerpt',
        'operator':'contains','value':'뒷문장'}]
    with pytest.raises(ValueError,match='asserted source literal'):project(pack,output)


def test_metadata_selection_is_explicit_keeps_fields_and_immutable_source():
    pack,output=fixture();before=deepcopy(pack)
    output['excerpt_selections']=[]
    output['source_metadata_selections']=[{'observation_id':'o','reason':'Only the returned file metadata is relevant.'}]
    selected=project(pack,output);row=selected['observations'][0]
    assert pack==before
    assert row['fields']=={k:v for k,v in before['observations'][0]['fields'].items() if k!='excerpt'}
    assert row['projection_omissions']['replacement']=='model_selected_metadata_only'
    page={'id':'p','receipt_id':'r','included_ids':['o'],'pack':pack,'selected_pack':selected,'output':output}
    stream={'id':'s','canonical':pack,'canonical_sha256':'canonical','page_count':1,'maximum':36000}
    reduced=reduction_pack(stream,[page])
    assert 'excerpt' not in reduced['observations'][0]['fields']
    # A later whole-source objection re-presents the original, never the metadata-only view.
    stream['open_objections']=[{'id':'objection','observation_ids':['o'],'span_ids':[]}]
    assert reduction_pack(stream,[page])['observations'][0]['fields']['excerpt']==before['observations'][0]['fields']['excerpt']


@pytest.mark.parametrize('mode',['foreign','duplicate','mixed','literal','whole_fields','objection','span'])
def test_metadata_selection_rejects_scope_loss_and_contradictory_citations(mode):
    pack,output=fixture();output['excerpt_selections']=[]
    output['source_metadata_selections']=[{'observation_id':'o','reason':'Only metadata.'}]
    if mode=='foreign':output['source_metadata_selections'][0]['observation_id']='foreign'
    if mode=='duplicate':output['source_metadata_selections']*=2
    if mode=='mixed':output['excerpt_selections']=[{'observation_id':'o','quote':'허용 기록','reason':'quote'}]
    if mode in ('literal','whole_fields'):
        output['findings'][0]['fact_assertions']=[{'observation_id':'o','pointer':'/fields' if mode=='whole_fields' else '/fields/excerpt','value':'허용 기록'}]
    if mode=='objection':pack['open_objections']=[{'observation_ids':['o']}]
    if mode=='span':
        pack['observations'][0]['source_span']={'span_id':'span'}
        output['findings'][0]['evidence_span_ids']=['span']
    with pytest.raises(ValueError):project(pack,output)


def test_reduction_uses_selected_originals_not_narrative_and_keeps_whole_objections():
    pack,output=fixture('앞문장\n허용 기록\n'+'background '*5000)
    output['findings'][0]['reason']='UNTRUSTED MODEL INTERPRETATION'
    selected=project(pack,output)
    page={'id':'p','receipt_id':'r','included_ids':['o'],'pack':pack,'selected_pack':selected,'output':output}
    stream={'id':'s','canonical':pack,'canonical_sha256':'canonical','page_count':1,'maximum':36000}
    result=reduction_pack(stream,[page])
    assert result['observations'][0]['fields']['excerpt']=='허용 기록'
    assert 'UNTRUSTED' not in str(result['observations'])
    stream['open_objections']=[{'id':'objection','observation_ids':['o'],'span_ids':[]}]
    with pytest.raises(InputBudgetError):reduction_pack(stream,[page])


def test_selected_contrary_span_survives_later_notes_that_omit_it():
    pack,output=fixture();selected=project(pack,output)
    sid,row=next(span_rows(selected['observations'][0]))
    page={'id':'p','receipt_id':'r','included_ids':['o'],'pack':pack,'output':{'findings':[]}}
    stream={'id':'s','canonical':pack,'canonical_sha256':'canonical','page_count':1,'maximum':36000,
            'source_span_catalog':{sid:row},
            'open_objections':[{'id':'objection','observation_ids':['o'],'span_ids':[sid]}]}
    result=reduction_pack(stream,[page])
    assert result['observations'][0]['fields']['excerpt']=='허용 기록'
    assert result['open_objections'][0]['span_ids']==[sid]


def test_literal_quote_transport_does_not_rewrite_handle_shaped_source():
    from workbench.model_references import ReferenceProjection
    projection=ReferenceProjection({'allowed_observation_ids':['OBSERVATION-000000000001']})
    output={'excerpt_selections':[{'observation_id':'R1','quote':'R1'}]}
    decoded=projection.decode(output)
    assert decoded['excerpt_selections']==[{'observation_id':'OBSERVATION-000000000001','quote':'R1'}]


def test_independent_readonly_quote_audit_detects_tampering(tmp_path):
    from workbench.store import Store
    from scripts.audit_review_focus import audit
    pack,output=fixture();selected=project(pack,output)
    path=tmp_path/'audit.sqlite3';store=Store(path)
    stream=store.add('review_stream','case',canonical=pack)
    page=store.add('review_page','case',stream_id=stream['id'],selected_pack=selected)
    result=audit(path,stream['id'])
    assert result['passed'] and result['spans_checked']==1 and result['source_writes']==0
    selected['observations'][0]['fields']['excerpt_spans'][0]['text']='tampered'
    store.update(page['id'],selected_pack=selected)
    assert not audit(path,stream['id'])['passed']


def test_independent_metadata_audit_preserves_all_non_excerpt_fields(tmp_path):
    from workbench.store import Store
    from scripts.audit_review_focus import audit
    pack,output=fixture();output['excerpt_selections']=[]
    output['source_metadata_selections']=[{'observation_id':'o','reason':'Only metadata.'}]
    selected=project(pack,output)
    path=tmp_path/'metadata.sqlite3';store=Store(path)
    stream=store.add('review_stream','case',canonical=pack)
    page=store.add('review_page','case',stream_id=stream['id'],selected_pack=selected)
    result=audit(path,stream['id'])
    assert result['passed'] and result['metadata_selections_checked']==1
    selected['observations'][0]['fields']['file_context']['ctime_ns']=456
    store.update(page['id'],selected_pack=selected)
    assert not audit(path,stream['id'])['passed']


def test_focus_target_is_not_an_artificial_half_envelope_gate(tmp_path,monkeypatch):
    from workbench.store import Store
    import workbench.review_stream as rs
    store=Store(tmp_path/'focus.sqlite3')
    pack,output=fixture()
    old=store.add('review_page','case',output=output)
    stream=store.add('review_stream','case',maximum=36000)
    page=store.add('review_page','case',focus_of=old['id'],focus_budget_chars=18000)
    meta={'phase':'focus_page','page_id':page['id'],'stream_id':stream['id']}
    # Shared contracts need not split equally between two notes. The actual
    # combined-envelope check still runs later; only strict progress is allowed.
    monkeypatch.setattr(rs,'reduction_pack',lambda s,p,final=False: {'payload':'abcdefghij'*(2400 if p[0]['id']==old['id'] else 2000)})
    assert not rs.focus_budget_errors(store,meta,pack,output,pack)
    monkeypatch.setattr(rs,'reduction_pack',lambda s,p,final=False:{'payload':'abcdefghij'*2400})
    assert rs.focus_budget_errors(store,meta,pack,output,pack)[0]['code']=='focus_selection_over_budget'
