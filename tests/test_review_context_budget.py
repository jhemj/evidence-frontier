from copy import deepcopy

from workbench.review_context import fit, serialize


def test_verbose_planner_metadata_is_bounded_before_source_fields_and_ids_survive():
    observations = [{'id': f'O{i}', 'fields': {'path': f'/source/{i}', 'excerpt': 'x' * 400}} for i in range(6)]
    pack = {'observations': observations,
            'selection_audit': {'rows': [{'id': f'O{i}', 'reason': 'r' * 1000} for i in range(500)]},
            'hypotheses': [{'number': i, 'question': 'q' * 500} for i in range(4)]}
    fit(pack, maximum=5000)
    assert len(serialize(pack)) <= 5000
    assert pack['selection_audit']['selected_observation_ids'] == [f'O{i}' for i in range(6)]
    assert pack['selection_audit']['selection_audit_omitted'] is True
    assert len(pack['hypotheses']) == 4
    assert all(o['id'].startswith('O') and 'path' in o['fields'] for o in pack['observations'])


def test_omission_is_disclosed_not_interpreted_as_absence():
    pack = {'observations': [{'id': 'O1', 'fields': {'path': '/x'}}],
            'selection_audit': {'verbose': 'x' * 10000}}
    fit(pack, maximum=1000)
    assert 'omitted' in pack['selection_audit']['scope'].lower()
    assert 'selection_audit_omitted' in pack['selection_audit']
    assert 'available' not in pack['selection_audit'] # Unknown is not zero/all-selected.


def test_metadata_compaction_preserves_known_counts_all_hypotheses_and_locators():
    observations=[{'id':f'O{i}','source_location':f'inode={i},offset={i*40}',
        'source_origin':'image/partition-1','fields':{'path':f'/source/{i}',
        'line':i+1,'byte_offset':i*40,'source_sha256':'a'*64,'excerpt':'x'*100}} for i in range(15)]
    hypotheses=[{'hypothesis_id':f'H{i}','question':f'Question {i}'} for i in range(30)]
    pack={'observations':deepcopy(observations),'hypotheses':hypotheses,
        'selection_audit':{'available':1800,'included':15,'omitted':1785,'not_previously_presented':4,
            'lead_family_audit':{'available':['a','b','c'],'included':['a'],'omitted':['b','c'],'omitted_count':2},
            'selected':[{'id':o['id'],'reason':'R'*1000} for o in observations]}}
    fit(pack,8000)
    assert len(serialize(pack))<=8000
    assert pack['observations']==observations and pack['hypotheses']==hypotheses
    audit=deepcopy(pack['selection_audit'])
    assert (audit['available'],audit['included'],audit['omitted'])==(1800,15,1785)
    assert audit['lead_family_audit']['omitted_count']==2
    pack['padding']='P'*10000
    import pytest
    with pytest.raises(ValueError):fit(pack,8000)
    assert pack['selection_audit']==audit # A second fit cannot erase counts.


def test_source_compaction_is_serialized_bounded_but_locators_and_times_are_exact():
    from workbench.review_context import source_fields
    fields={'path':'/long/'+'x'*900,'artifact_path':'source/'+'a'*500,'source_sha256':'a'*64,
        'partition_offset':4096,'inode':123,'source_offset':1200,'byte_offset':0,'byte_length':8192,
        'json_pointer':'/'+('x'*500),'locator_basis':'raw bytes','ctime_ns':1782104669000000001,
        'time_record':{'raw':'2026-06-22T05:04:29.000000001Z'},'file_context':{'ctime':1782104669},
        'excerpt':'\x00\x01"\\'*1000}
    before=deepcopy(fields);out=source_fields(fields,250)
    assert len(serialize(out['excerpt']))<=250 and out['excerpt_truncated']
    assert all(out[k]==v for k,v in fields.items() if k!='excerpt')
    assert fields==before


def test_unfittable_identity_fails_closed_instead_of_shortening_path():
    import pytest
    path='/'+('a'*5000)
    pack={'observations':[{'id':'O','source_location':'immutable','fields':{'path':path,'excerpt':'x'*9000}}]}
    with pytest.raises(ValueError):fit(pack,1500)
    assert pack['observations'][0]['fields']['path']==path
    assert pack['observations'][0]['id']=='O'


def test_shared_metadata_is_exact_reversible_and_idempotent():
    from workbench.review_context import share_metadata,expand_metadata
    rows=[{'id':f'O{i}','fields':{'path':f'/source/{i}'},
        'context_limit':'Byte locations need source context. '*6,
        'time_semantics':{'kind_basis':'registered artifact semantics; not actor attribution',
            'timezone_basis':'explicit source timezone in original log',
            'raw':f'2026-09-01T00:00:{i:02d}Z','epoch_nanoseconds':str(1782104669000000001+i)}} for i in range(12)]
    pack={'observations':rows};before=deepcopy(pack)
    share_metadata(pack)
    assert len(serialize(pack))<len(serialize(before))
    assert [o['fields'] for o in rows]==[o['fields'] for o in before['observations']]
    assert all('raw' in o['time_semantics'] and 'epoch_nanoseconds' in o['time_semantics'] for o in rows)
    shared=deepcopy(pack);share_metadata(pack);assert shared==pack
    expand_metadata(pack);assert pack==before


def test_common_metadata_does_not_fill_originally_absent_or_different_values():
    from workbench.review_context import share_metadata,expand_metadata
    pack={'observations':[{'id':str(i),'fields':{},'context_limit':'L'*300,
        'time_semantics':{'timezone_basis':('source '+str(i))*50}} for i in range(8)]}
    del pack['observations'][0]['context_limit']
    before=deepcopy(pack);share_metadata(pack);expand_metadata(pack)
    assert pack==before


def test_budget_list_prefix_discloses_unpresented_items_and_nested_locators():
    from workbench.review_context import source_fields
    path='/long/'+('x'*900)
    fields={'linked_records':[{'path':path,'source_location':path+':'+str(i),'excerpt':'x'*1000} for i in range(10)],
        'referenced_paths':[path,'/two','/three','/four','/five','/six']}
    before=deepcopy(fields)
    first=source_fields(fields,250)
    second=source_fields(first,128,2)
    assert fields==before
    assert len(second['linked_records'])==2
    assert second['linked_records'][0]['path']==path
    assert second['linked_records'][0]['source_location']==path+':0'
    assert second['referenced_paths'][0]==path
    assert second['context_omissions']['/linked_records']['input_items']==10
    assert second['context_omissions']['/linked_records']['omitted_items']==8
    assert second['context_omissions']['/referenced_paths']['omitted_items']==4
