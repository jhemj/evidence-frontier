import hashlib
import json

from workbench.linux_tools import execute_tool
from workbench.models import InvestigationToolRequest


def search_fixture(tmp_path, indexed=0, raw=b'', query='needle'):
    (tmp_path/'evidence.E01').write_bytes(b'inert fixture')
    run=tmp_path/('RUN-'+'a'*32);run.mkdir()
    events=[{'type':'linux_system_event','fields':{'excerpt':'needle '+str(i)}} for i in range(indexed)]
    (run/'events.ndjson').write_text(''.join(json.dumps(e)+'\n' for e in events),encoding='utf-8')
    (run/'filesystem_inventory.ndjson').write_text('',encoding='utf-8')
    (run/'source.txt').write_bytes(raw)
    (run/'manifest.json').write_text(json.dumps({'image':'evidence.E01','sources':[
        {'relative_path':'source.txt','sha256':hashlib.sha256(raw).hexdigest(),'path':'/var/log/messages',
         'partition_offset':0,'inode':1,'complete':True}]}),encoding='utf-8')
    body=InvestigationToolRequest(evidence_path='evidence.E01',run_id=run.name,request={'tool':'search','query':query})
    return execute_tool(tmp_path,tmp_path,body)


def test_index_cap_does_not_claim_complete_below_combined_cap(tmp_path):
    result=search_fixture(tmp_path,indexed=45)
    assert result['matches']==31 and result['returned']==30
    assert result['omitted_matches']==1 and result['next_cursor']
    assert result['status']=='partial' and result['truncated'] and not result['complete']


def test_raw_matches_do_not_hide_omitted_index_matches(tmp_path):
    result=search_fixture(tmp_path,indexed=31,raw=b'needle\n'*29)
    assert result['matches']==31 and result['returned']==30
    assert result['omitted_matches']==1 and not result['complete']


def test_literal_phrase_does_not_silently_become_boolean_search(tmp_path):
    result=search_fixture(tmp_path,raw=b'NEEDLE established\nneedle other established\n',query='needle established')
    assert result['matches']==result['returned']==1
    assert result['complete'] and not result['truncated']
    assert result['observations'][0]['fields']['excerpt']=='NEEDLE established\n'
    assert 'no AND/OR/regex' in result['query_semantics']
