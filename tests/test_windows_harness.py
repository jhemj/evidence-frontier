"""Synthetic Windows contracts; no private case data or executable payloads."""
import hashlib
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from workbench.api import create_app
from workbench.controller import Controller
from workbench.store import Store
from workbench.models import InvestigationToolRequest
from workbench.windows_analysis import normalize_event, normalize_srum, powershell_sessions, task_identity
from workbench.windows_scan import scan, parse_task
from workbench.windows_bundle import inspect_bundle, read_bundle
from workbench.evidence_semantics import time_record, wow64_path, windows_path, activity_context, stage_issues
from workbench.parser_contract import consume


def event(n=1, **fields):
    return {'ts_utc': f'2026-09-01T00:00:{n:02}+00:00', 'event_id': 400,
            'event_record_id': n, 'execution_process_id': 123, 'channel': 'Windows PowerShell',
            'path': r'C:\Lab\fixture.exe', **fields}


def bundle(tmp_path, extras=None):
    entries={'BUNDLE_SUMMARY.json': json.dumps({'schema':'acas-is.safe-evidence-bundle/v1',
        'executable_payloads_included':False, 'case_id':'SYNTHETIC'}).encode(),
        'evidence/powershell_ioc_summary.json':json.dumps({'classic_log':{'start_events':[event()]},
            'operational_log':{}}).encode(),
        'incident/case_findings.json':json.dumps({'findings':[{'finding_id':'F1','judgment':'confirmed'}]}).encode()}
    entries.update(extras or {})
    entries['MANIFEST_SHA256.txt']=''.join(hashlib.sha256(v).hexdigest()+'  '+k+'\n' for k,v in entries.items()).encode()
    path=tmp_path/'safe.zip'
    with zipfile.ZipFile(path,'w') as z:
        for name,data in entries.items():z.writestr(name,data)
    return path


def test_api_os_is_case_scoped_and_default_is_backward_compatible(tmp_path):
    app=create_app(tmp_path/'data',tmp_path,start_worker=False)
    with TestClient(app) as client:
        headers={'X-Requested-With':'frontier'}
        for platform in ('linux','windows'):
            response=client.post('/api/cases',headers=headers,json={'name':'Synthetic '+platform,'target_os':platform})
            assert response.status_code==200 and response.json()['target_os']==platform
        assert client.post('/api/cases',headers=headers,json={'name':'Legacy'}).json()['target_os']=='linux'
        assert client.post('/api/cases',headers=headers,json={'name':'Invalid','target_os':'macos'}).status_code==422
        assert [x['target_os'] for x in client.get('/api/cases').json()]==['linux','windows','linux']


def test_windows_queue_has_no_linux_scan_or_linux_normalizer(tmp_path):
    (tmp_path/'fixture.ndjson').write_text(json.dumps(event()))
    c=Controller(Store(tmp_path/'db'),tmp_path)
    cid=c.create('Windows','','standard','windows')['id']
    c.register(cid,'fixture.ndjson');c.ensure_investigation(cid);c.ensure_investigation(cid)
    tasks=c.store.list('task',cid)
    assert {t['action'] for t in tasks}=={'integrity','windows_scan','windows_investigate','ai_judgment','investigation_report'}
    assert len(tasks)==5
    assert {h['contract'] for h in c.store.list('hypothesis',cid)}=={'windows-v1'}
    assert len(c.store.list('hypothesis',cid))==10
    (tmp_path/'not-supported.txt').write_text('synthetic')
    with pytest.raises(ValueError,match='Windows 증거'):c.register(cid,'not-supported.txt')


def test_record_failure_preserves_survivors_and_iterator_failure_is_partial():
    def parser(raw,n):
        if raw=='broken':raise ValueError('synthetic record error')
        return {'fields':{'value':raw}}
    result=consume([1,'broken',3],parser,retain_failure=lambda data,n:'retained/'+str(n))
    assert result['parsed_records']==2 and result['failed_records']==1
    assert result['records_seen']==3 and result['enumeration_complete'] and not result['complete']
    assert result['failures'][0]['raw_locator']=='retained/2'
    def broken_iterator():
        yield 1
        raise ValueError('iterator failed')
    result=consume(broken_iterator(),parser)
    assert result['unread_records'] is None and result['status']=='partial'
    assert consume([1,2],parser,limit=1)['limit_reached']


def test_srum_new_column_does_not_drop_table_or_prove_network_success():
    raw={'AppId':r'C:\Lab\fixture.exe','TimeStamp':'2026-09-01T00:00:00Z','BytesSent':1,'WakeCount':7}
    result=consume([raw],lambda r,n:normalize_srum(r,'network_data','SRUDB.dat',n))
    assert result['parsed_records']==1 and result['degraded_records']==1 and result['status']=='partial'
    row=result['rows'][0]
    assert row['fields']['raw_fields']['WakeCount']==7
    assert stage_issues({'stage':'connection','judgment':'확인'},[row])


@pytest.mark.parametrize('typ', ['windows_amcache','windows_shimcache','windows_file','windows_scriptblock','windows_srum_network','windows_prior_interpretation','windows_source_excerpt'])
def test_static_or_script_record_cannot_prove_execution(typ):
    assert stage_issues({'stage':'execution','judgment':'확인'},[{'type':typ,'fields':{}}])


def test_connection_failure_is_narrow_positive_evidence_not_success():
    rows=[{'type':'windows_network','fields':{'network_state':'failed'}}]
    assert not stage_issues({'stage':'connection','network_state':'failed','judgment':'확인'},rows)
    assert stage_issues({'stage':'connection','network_state':'succeeded','judgment':'확인'},rows)
    assert stage_issues({'stage':'objective','judgment':'확인'},rows)
    assert not stage_issues({'stage':'execution','judgment':'확인'},[{'type':'windows_process','fields':{}}])


def test_times_preserve_source_precision_and_do_not_guess_timezone():
    assert time_record('2026-01-01T00:00:00')['normalized_utc'] is None
    high=time_record('2026-01-01T00:00:00.1234567Z')
    assert high['raw'].endswith('1234567Z') and high['precision']=='7 fractional digits'
    assert high['normalization_precision'].startswith('microsecond')
    assert time_record('not-a-date')['error']


def test_wow64_requires_caller_and_redirection_state():
    literal=r'C:\Windows\System32\fixture.exe'
    assert wow64_path(literal)['resolved_path'] is None
    assert len(wow64_path(literal,32,64)['candidates'])==2
    assert wow64_path(literal,32,64,True)['resolved_path']==r'c:\windows\syswow64\fixture.exe'
    assert wow64_path(literal,32,64,False)['resolved_path']==windows_path(literal)
    assert wow64_path(r'C:\Windows\System32\drivers\etc\hosts',32,64,True)['resolved_path'].endswith(r'system32\drivers\etc\hosts')
    assert wow64_path(r'C:\Windows\Sysnative\fixture.exe',32,64)['resolved_path'].endswith(r'system32\fixture.exe')
    assert windows_path(r'C:\Lab\fixture.exe:stream').endswith(':stream')


def test_task_uri_versions_are_scoped_to_os_and_not_basename():
    a=task_identity('os1',r'\Fixture',[{'Command':'one'}],'Tasks/one')
    b=task_identity('os1',r'\fixture',[{'Command':'two'}],'Tasks_Migrated/two')
    assert a['task_identity']==b['task_identity'] and a['version_sha256']!=b['version_sha256']
    assert a['task_identity']!=task_identity('os2',r'\Fixture',[{'Command':'one'}],'Tasks/one')['task_identity']
    xml=b'<Task xmlns="urn:test"><RegistrationInfo><URI>\\Fixture</URI></RegistrationInfo><Actions><Exec><Command>fixture.exe</Command></Exec></Actions></Task>'
    row=parse_task(xml,'Tasks/Fixture','os1')
    assert row['fields']['actions']==[{'Command':'fixture.exe'}]
    assert stage_issues({'stage':'execution','judgment':'확인'},[row])
    changed=parse_task(xml.replace(b'<Actions>',b'<Triggers/><Actions>'),'Tasks/Fixture','os1')
    assert changed['fields']['version_sha256']!=row['fields']['version_sha256']
    assert changed['fields']['actions_sha256']==row['fields']['actions_sha256']


def test_requery_uses_source_linked_full_paths_not_basenames():
    from workbench.windows_analysis import ioc_checks
    rows=[{'id':'o1','type':'windows_file','fields':{'path':r'C:\Lab\fixture.exe'}},
          {'id':'o2','type':'windows_file','fields':{'path':r'c:\lab\fixture.exe'}},
          {'id':'o3','type':'windows_file','fields':{'path':'fixture.exe'}}]
    checks=ioc_checks(rows)
    assert len(checks)==1 and checks[0]['query']==r'C:\Lab\fixture.exe' and 'o1' in checks[0]['reason']


def test_postincident_tag_requires_basis_and_date_alone_is_not_attribution():
    assert activity_context({'activity_class':'collection','cutover_time':'2026-09-01T00:00:00Z'})['category']=='unattributed'
    assert activity_context({'activity_class':'collection','activity_basis_locators':['manifest/collector']})['category']=='collection'


def test_powershell_counts_boundary_and_never_infers_success():
    starts=[event(1,stage='masqueraded_powershell',behavior='fixture_iwr_iex'),
            event(2,stage='outer_launcher'),event(3,stage='copy_helper'),
            event(20,stage='masqueraded_powershell',behavior='fixture_iwr_iex',execution_process_id=456)]
    failures=[event(4,normalized_error='remote_server_connection_failed',behavior='fixture_iwr_iex')]
    result=powershell_sessions(starts+[starts[0]],failures,'2026-09-01T00:00:04Z','2026-09-01T00:00:30Z')
    assert (result['engine_starts'],result['batch_candidates'])==(4,2)
    assert (result['retained_network_actions'],result['retained_failures'],result['retained_indeterminate'])==(2,1,1)
    assert result['confirmed_successes']==0
    assert powershell_sessions(starts,[{**failures[0],'boot_id':'other'}])['unmatched_failure_records']
    assert powershell_sessions([event(1),event(1,os_instance='other')],[])['engine_starts']==2


def test_bundle_hashes_imported_claims_are_not_native_proof(tmp_path):
    result=inspect_bundle(bundle(tmp_path))
    assert result['verified_member_count']==3 and result['raw_source_reverified'] is False
    assert len(result['observations'])==2
    assert result['observations'][1]['type']=='windows_prior_interpretation'
    assert all(o['fields']['json_pointer'] and o['fields']['imported_normalized'] for o in result['observations'])


@pytest.mark.parametrize('extra', [{'../escape.json':b'{}'},{'run.exe':b'not executed'},{'disguised.json':b'MZfake'}])
def test_bundle_rejects_unsafe_members(tmp_path,extra):
    with pytest.raises(ValueError):read_bundle(bundle(tmp_path,extra))


def test_bundle_rejects_hash_mismatch_and_unlisted_member(tmp_path):
    path=bundle(tmp_path)
    with zipfile.ZipFile(path,'a') as z:z.writestr('unlisted.json',b'{}')
    with pytest.raises(ValueError,match='every member'):read_bundle(path)
    path=bundle(tmp_path)
    with zipfile.ZipFile(path) as z:entries={n:z.read(n) for n in z.namelist()}
    entries['incident/case_findings.json']=b'{}'
    with zipfile.ZipFile(path,'w') as z:
        for name,data in entries.items():z.writestr(name,data)
    with pytest.raises(ValueError,match='hash mismatch'):read_bundle(path)


def test_scan_followups_export_and_tamper_guard(tmp_path,monkeypatch):
    from workbench.linux_tools import execute_tool
    from workbench.investigation_export import prepare
    from workbench.investigation import compact_observation
    source=tmp_path/'fixture.ndjson'
    source.write_text('\n'.join([json.dumps(event()),'{bad',json.dumps(event(2,path=r'C:\Other\fixture.exe'))]))
    result=scan(source,tmp_path/'analysis');run=tmp_path/'analysis'/result['run_id']
    assert result['status']=='partial' and len(result['observations'])==3
    assert result['source_coverage'][0]['failed_records']==1
    assert result['observations'][0]['fields']['raw_source_reverified'] is False
    req=lambda **request:InvestigationToolRequest(target_os='windows',evidence_path=source.name,run_id=run.name,request=request)
    found=execute_tool(tmp_path,tmp_path/'analysis',req(tool='search',query='fixture.exe',path=r'c:\lab'))
    assert found['observations'] and {o['fields']['path'] for o in found['observations']}=={r'C:\Lab\fixture.exe'}
    ref=result['observations'][1]['fields']['artifact_path']
    assert execute_tool(tmp_path,tmp_path/'analysis',req(tool='read_source',path=ref))['observations'][0]['type']=='windows_source_excerpt'
    assert execute_tool(tmp_path,tmp_path/'analysis',req(tool='read_file',path='/etc/passwd'))['status']=='unsupported'
    legacy=InvestigationToolRequest(evidence_path=source.name,run_id=run.name,request={'tool':'correlate'})
    assert execute_tool(tmp_path,tmp_path/'analysis',legacy)['status']=='failed'
    correlation=execute_tool(tmp_path,tmp_path/'analysis',req(tool='correlate'))
    assert correlation['observations'][0]['type']=='windows_counterevidence'
    observations=[{**o,'id':str(i),'evidence_id':'e'} for i,o in enumerate(result['observations']+correlation['observations'])]
    assert 'context_request' not in compact_observation(observations[-1])
    monkeypatch.setenv('ANALYSIS_ROOT',str(tmp_path/'analysis'))
    destination=tmp_path/'report';destination.mkdir()
    doc={'observations':observations,'hypotheses':[],'tool_receipts':[],'case':{'name':'Synthetic'},'id':'report','generated_at':'now'}
    generated,files,hashes=prepare(doc,destination)
    assert 'hash_scope' in generated['EVIDENCE_MANIFEST.csv'].decode('utf-8-sig')
    assert 'TIMELINE.csv' in files and hashes and 'REPORT_EVIDENCE_MAP.csv' in generated
    (run/'events.ndjson').write_text('tampered')
    assert execute_tool(tmp_path,tmp_path/'analysis',req(tool='search',query='fixture'))['status']=='failed'
    with pytest.raises(ValueError,match='해시 불일치'):prepare(doc,destination)


def test_native_adapter_os_gate_and_per_source_failures(tmp_path,monkeypatch):
    # Exercise the raw-image dispatch without fabricating a Windows disk image.
    from dissect.target import Target
    def missing(*args):raise FileNotFoundError('synthetic missing artifact')
    target=SimpleNamespace(os='linux',disks=[])
    monkeypatch.setattr(Target,'open',lambda _:target)
    with pytest.raises(ValueError,match='target OS'):scan(tmp_path/'fixture.raw',tmp_path/'analysis')
    target.os='windows';target.fs=SimpleNamespace(path=missing);target.registry=SimpleNamespace(key=missing)
    result=scan(tmp_path/'fixture.raw',tmp_path/'analysis')
    assert result['status']=='partial' and result['observations'][0]['fields']['events']==0
    assert sum(c['status']=='unavailable' for c in result['source_coverage'])==13


def test_report_coverage_counterevidence_and_metadata_time(tmp_path):
    from test_dual_reports import fixture
    from workbench.reporting import report_document,render_view,cited_ids
    from workbench.report_views import project
    c,cid,e,t,o=fixture(tmp_path);c.store.update(cid,target_os='windows')
    contrary=c.store.add('observation',cid,evidence_id=e['id'],type='windows_counterevidence',timestamp=None,source_location='fixture:counter',fields={'path':'fixture.exe'})
    env=c.store.add('observation',cid,evidence_id=e['id'],type='windows_environment',timestamp='1900-01-01T00:00:00Z',source_location='collector',
        fields={'events':7,'source_coverage':[{'source':'PowerShell Operational','status':'partial','parsed_records':7,'failed_records':1,'retention_first_reported':'2026-09-01T00:00:00Z','absence_is_refutation':False}]})
    doc=report_document(c,cid)
    doc['judgments'][0]['findings'][0]['counterevidence_ids']=[contrary['id']]
    view=project(doc)
    assert contrary['id'] in cited_ids(doc) and view['executive']['target_os']=='windows'
    html=render_view(view['analyst'])
    assert 'PowerShell Operational' in html and '반대 근거' in html and 'failed_records' in html
    assert '1900-01-01' not in render_view(view['executive'])
    assert not doc['completion']['analysis_complete_in_supported_scope']


def test_validation_rejects_absence_and_unrelated_counterevidence():
    from workbench.review_validation import errors
    finding={'dossier_id':'d','judgment':'확인','basis':'absence','observation_ids':['o'],
             'counterevidence_ids':['unrelated'],'stages':[]}
    codes={e['code'] for e in errors({'findings':[finding]},['d'],['o'],{'d':['o']})}
    assert {'absence_preconditions_unverified','counterevidence_citation_scope'}<=codes


def test_windows_graph_dispatch_keeps_platform(tmp_path,monkeypatch):
    from workbench.investigation_graph import tick,digest
    from workbench.investigation import seed
    monkeypatch.setenv('DATA_ROOT',str(tmp_path/'data'));monkeypatch.setenv('INVESTIGATION_MODEL_CALLS','6')
    c=Controller(Store(tmp_path/'case.db'),tmp_path);cid=c.create('Windows graph','','standard','windows')['id']
    c.store.update(cid,status='running')
    ev=c.store.add('evidence',cid,path='fixture.E01',signature='fixture',connected=True)
    task=c.store.add('task',cid,action='windows_investigate',cell_id='CELL',evidence_id=ev['id'])
    c.store.add('receipt',cid,evidence_id=ev['id'],result={'run_id':'RUN-'+'a'*32})
    c.store.add('config','',provider={'model':'local','base_url':'http://localhost:11434','protocol':'ollama'})
    seed(c,cid,ev['id']);submissions=[]
    def model(self,question,pack,role):
        assert pack['target_os']=='windows' and pack['available_tools']==['search','read_source','correlate']
        output={'summary':'Synthetic','claims':[],'hypotheses':[],'remaining_questions':[],'tool_calls':[]}
        return output,{'output':output}
    def worker(method,path,**kwargs):
        if method=='POST':submissions.append(kwargs['json'])
        result={'status':'covered','complete':True,'observations':[]}
        return {'status':'succeeded','result':result,'result_sha256':digest(result)}
    monkeypatch.setattr('workbench.investigation_graph.Provider.generate',model)
    monkeypatch.setattr('workbench.investigation_graph.worker_request',worker)
    for _ in range(200):
        result=tick(c,cid,ev,task)
        if result:break
    assert result and submissions and all(s['investigation']['target_os']=='windows' for s in submissions)
