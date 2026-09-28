from test_search_coverage import search_fixture
from workbench.linux_tools import execute_tool
from workbench.models import InvestigationToolRequest
import pytest


def test_partial_search_exposes_copyable_exact_limit_continuation(tmp_path):
    search_fixture(tmp_path, indexed=12)
    body = InvestigationToolRequest(
        evidence_path='evidence.E01', run_id='RUN-' + 'a' * 32,
        request={'tool': 'search', 'query': 'needle', 'limit': 8})
    first = execute_tool(tmp_path, tmp_path, body)
    assert first['has_more'] and first['continuation_request']
    continuation = first['continuation_request']
    assert continuation['limit'] == 8
    resumed = execute_tool(tmp_path, tmp_path, InvestigationToolRequest(
        evidence_path='evidence.E01', run_id=body.run_id, request=continuation))
    assert resumed['status'] == 'covered'
    assert len(resumed['observations']) == 4


def test_complete_search_has_no_continuation_request(tmp_path):
    search_fixture(tmp_path, indexed=2)
    body = InvestigationToolRequest(
        evidence_path='evidence.E01', run_id='RUN-' + 'a' * 32,
        request={'tool': 'search', 'query': 'needle', 'limit': 8})
    result = execute_tool(tmp_path, tmp_path, body)
    assert result['complete'] and result['continuation_request'] is None


@pytest.mark.parametrize('change',[{'limit':30},{'query':'different'},{'partition_offset':4096}])
def test_modified_continuation_is_still_rejected(tmp_path,change):
    search_fixture(tmp_path,indexed=12)
    body=InvestigationToolRequest(evidence_path='evidence.E01',run_id='RUN-'+'a'*32,
                                  request={'tool':'search','query':'needle','limit':8})
    first=execute_tool(tmp_path,tmp_path,body)
    changed={**first['continuation_request'],**change}
    result=execute_tool(tmp_path,tmp_path,InvestigationToolRequest(
        evidence_path=body.evidence_path,run_id=body.run_id,request=changed))
    assert result['status']=='failed' and not result['observations']


def test_continuation_does_not_authorize_changed_retained_sources(tmp_path):
    search_fixture(tmp_path,indexed=12)
    body=InvestigationToolRequest(evidence_path='evidence.E01',run_id='RUN-'+'a'*32,
                                  request={'tool':'search','query':'needle','limit':8})
    first=execute_tool(tmp_path,tmp_path,body)
    with (tmp_path/body.run_id/'source.txt').open('ab') as stream:stream.write(b'new bytes')
    result=execute_tool(tmp_path,tmp_path,InvestigationToolRequest(
        evidence_path=body.evidence_path,run_id=body.run_id,request=first['continuation_request']))
    assert result['status']=='failed' and not result['observations']
