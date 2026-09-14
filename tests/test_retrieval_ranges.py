import json
import pytest
from test_search_coverage import search_fixture
from workbench.linux_tools import execute_tool
from workbench.models import InvestigationToolRequest
from workbench.retrieval import fingerprint_scope, read_source


def request(tmp_path, **fields):
    return execute_tool(tmp_path,tmp_path,InvestigationToolRequest(evidence_path='evidence.E01',
        run_id='RUN-'+'a'*32,request={'tool':'search','query':'needle',**fields}))


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
