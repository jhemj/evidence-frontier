import json
import pytest
from test_search_coverage import search_fixture
from workbench.linux_tools import execute_tool
from workbench.models import InvestigationToolRequest
from workbench.retrieval import fingerprint_scope, read_source


def request(tmp_path, **fields):
    return execute_tool(tmp_path,tmp_path,InvestigationToolRequest(evidence_path='evidence.E01',
        run_id='RUN-'+'a'*32,request={'tool':'search','query':'needle',**fields}))


@pytest.mark.parametrize('pending',[b'\xe1',b'\xe1\x80'])
@pytest.mark.parametrize('tail',[b'B\n',b''])
def test_pending_decoder_bytes_are_not_marked_already_searched(tmp_path,pending,tail):
    from workbench.retrieval import LINE_LIMIT
    raw=b'x'*(LINE_LIMIT-len(b'needle'+pending))+b'needle'+pending+tail
    result=search_fixture(tmp_path,raw=raw,query='needle\ufffd')
    assert len(result['observations'])==1
    assert 'needle\ufffd' in result['observations'][0]['fields']['excerpt']


def test_pagination_reaches_late_raw_hits_and_binds_filters(tmp_path):
    first=search_fixture(tmp_path,indexed=45,raw=b'needle late\n')
    second=request(tmp_path,cursor=first['next_cursor'])
    assert second['complete'] and len(second['observations'])==16
    assert second['observations'][-1]['fields']['excerpt']=='needle late\n'
    assert request(tmp_path,cursor=first['next_cursor'],path='/etc')['status']=='failed'
    run=tmp_path/('RUN-'+'a'*32)
    with (run/'events.ndjson').open('a') as f:f.write('{}\n')
    assert request(tmp_path,cursor=first['next_cursor'])['status']=='failed'


def test_scan_budget_continuation_without_matching_prefix(tmp_path,monkeypatch):
    search_fixture(tmp_path,raw=b'other\n'*100+b'needle tail\n')
    monkeypatch.setattr('workbench.retrieval.SCAN_BUDGET',100)
    found=[];cursor='';seen=set()
    for _ in range(12):
        page=request(tmp_path,cursor=cursor);found+=page['observations']
        cursor=page['next_cursor']
        if not cursor:break
        assert cursor not in seen;seen.add(cursor)
    assert not cursor and len(found)==1


def test_source_range_preserves_decompressed_coordinate_and_checks_hash(tmp_path):
    search_fixture(tmp_path,raw=b'a'*9000+b'needle tail\n')
    run=tmp_path/('RUN-'+'a'*32);manifest=json.loads((run/'manifest.json').read_text())
    manifest['sources'][0]['locator_basis']='gzip decompressed bytes'
    (run/'manifest.json').write_text(json.dumps(manifest))
    result=request(tmp_path,tool='read_source',path=run.name+'/source.txt',byte_offset=8990,byte_length=256)
    assert result['status']=='partial'
    fields=result['observations'][0]['fields']
    assert 'needle tail' in fields['excerpt'] and fields['byte_offset']==8990
    assert fields['locator_basis']=='gzip decompressed bytes'
    assert request(tmp_path,tool='read_source',path='../source.txt')['status']=='failed'
    (run/'source.txt').write_text('changed')
    assert request(tmp_path,tool='read_source',path=run.name+'/source.txt')['status']=='failed'


def test_filter_time_unknown_and_account_boundaries(tmp_path):
    search_fixture(tmp_path,raw=b'needle root\nneedle rootkit\n')
    assert len(request(tmp_path,account='root')['observations'])==1
    result=request(tmp_path,time_from='2026-01-01T00:00:00Z')
    assert not result['observations'] and result['unknown_time_excluded']==2
    assert request(tmp_path,time_from='2026-01-01')['status']=='failed'


def test_scope_fingerprints_include_range_not_reason():
    base={'tool':'read_file','path':'/etc/cron.d/a'}
    assert fingerprint_scope(base)!=fingerprint_scope({**base,'byte_offset':9000})
    assert fingerprint_scope(base)==fingerprint_scope({**base,'reason':'different explanation'})


def test_late_match_excerpt_contains_match_and_long_boundary_is_not_lost(tmp_path,monkeypatch):
    search_fixture(tmp_path,raw=b'x'*20000+b'needle'+b'x'*100+b'\n')
    result=request(tmp_path)
    assert 'needle' in result['observations'][0]['fields']['excerpt']
    assert result['observations'][0]['fields']['byte_offset']>0
    monkeypatch.setattr('workbench.retrieval.LINE_LIMIT',20003)
    result=request(tmp_path)
    assert any('needle' in o['fields']['excerpt'] for o in result['observations'])


def test_unicode_casefold_match_has_correct_late_excerpt(tmp_path):
    search_fixture(tmp_path,raw=('x'*20000+' Straße\n').encode(),query='STRASSE')
    result=request(tmp_path,query='STRASSE')
    fields=result['observations'][0]['fields']
    assert 'strasse' in fields['excerpt'].casefold()
    assert fields['byte_offset']>0
    assert result['observations'][0]['source_location'].endswith(':offset:'+str(fields['byte_offset']))


def test_long_line_overlap_does_not_repeat_a_match(tmp_path,monkeypatch):
    search_fixture(tmp_path,raw=b'x'*80+b'needle'+b'x'*70+b'\n')
    monkeypatch.setattr('workbench.retrieval.LINE_LIMIT',100)
    result=request(tmp_path)
    assert len(result['observations'])==1


def test_utf8_boundary_inside_multibyte_character_is_searchable(tmp_path,monkeypatch):
    search_fixture(tmp_path,raw=('가'*31+'침해흔적'+'나'*30+'\n').encode())
    monkeypatch.setattr('workbench.retrieval.LINE_LIMIT',100)
    result=request(tmp_path,query='침해흔적')
    assert len(result['observations'])==1 and '침해흔적' in result['observations'][0]['fields']['excerpt']


def test_damaged_bytes_do_not_hide_valid_unicode_later_in_line(tmp_path):
    from workbench.retrieval import literal_byte_hit
    raw=b'prefix\xff\xfe'+('가'*3000+' Straße 침해흔적\n').encode()
    search_fixture(tmp_path,raw=raw)
    for query in ('strasse','침해흔적'):
        result=request(tmp_path,query=query)
        assert len(result['observations'])==1
        fields=result['observations'][0]['fields']
        assert query in fields['excerpt'].casefold()
        hit=literal_byte_hit(raw,query)
        assert raw[hit:].decode(errors='replace').casefold().startswith(query)


def test_identical_retained_bytes_require_unambiguous_origin(tmp_path):
    search_fixture(tmp_path,raw=b'needle retained\n')
    run=tmp_path/('RUN-'+'a'*32);manifest=json.loads((run/'manifest.json').read_text())
    first=manifest['sources'][0]
    manifest['sources'].append({**first,'partition_offset':999,'inode':22,'source_offset':500})
    (run/'manifest.json').write_text(json.dumps(manifest))
    scope={'tool':'read_source','path':run.name+'/source.txt'}
    assert request(tmp_path,**scope)['status']=='failed'
    selected=request(tmp_path,**scope,partition_offset=999,inode=22,source_offset=500)
    assert selected['observations'][0]['fields']['partition_offset']==999
    assert selected['observations'][0]['fields']['image_file_byte_offset']==500


@pytest.mark.parametrize('raw',[b'A\xffB',b'A\xe1\x80B',b'A\xef\xbf\xbdB'])
def test_replacement_literal_preserves_python_decode_semantics(raw):
    from workbench.retrieval import literal_byte_hit
    assert raw.decode(errors='replace').casefold()=='a\ufffdb'
    assert literal_byte_hit(raw,'a\ufffdb')==0
    assert literal_byte_hit(raw,'\ufffdb')==1
