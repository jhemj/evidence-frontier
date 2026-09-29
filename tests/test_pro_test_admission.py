from copy import deepcopy
import pytest
from workbench.models import TestDesign
from workbench.test_admission import assess,reusable,bind_reuse
from workbench.retrieval import fingerprint_scope
from workbench.store import Store


def request(**changes):
    return {'tool':'read_file','path':'/synthetic/x','partition_offset':0,'inode':21,
            'byte_offset':0,'byte_length':8192,**changes}


def test_read_identity_ignores_unapplied_arguments_not_real_ranges():
    a=request()
    assert fingerprint_scope(a)==fingerprint_scope(request(limit=1,query='unused',account='unused',source_offset=42))
    for changes in ({'byte_offset':8192},{'byte_length':256},{'inode':22},{'partition_offset':None}):
        assert fingerprint_scope(a)!=fingerprint_scope(request(**changes))


@pytest.mark.parametrize('tool,observable',[
    ('read_file','source_content'),('read_source','source_content'),('static_file','static_properties'),
    ('archive_list','archive_members'),('search','matching_records'),('correlate','record_association')])
def test_actual_immediate_capabilities_allow_discovery(tool,observable):
    r=request(tool=tool,test_design=TestDesign(immediate_observable=observable,expected_update='Locate discriminating material').model_dump())
    assert assess(r)['eligible']
    assert not assess({**r,'test_design':{**r['test_design'],'immediate_observable':'trusted_baseline_comparison'}})['eligible']


def test_missing_inputs_do_not_become_absence_or_malice():
    r=request(test_design={'immediate_observable':'source_content','required_observation_ids':['o']})
    out=assess(r,observation_ids=[])
    assert out['reason']=='required_input_unavailable' and out['missing_observation_ids']==['o']
    assert assess(r,observation_ids=['o'])['eligible']
    assert assess(r,'windows',['o'])['reason']=='unsupported_tool'


def test_only_typed_deterministic_resolution_failures_reuse_across_ranges():
    job={'id':'j','fingerprint':'old','status':'ingested','request':request(),
         'result_scope':{'failure':{'code':'path_not_resolved','retryable':False,'resolver_version':'linux-image-resolution-1'}}}
    assert reusable([job],'new',request(byte_offset=9999))==job
    assert reusable([job],'new',request(inode=22)) is None
    assert reusable([job],'new',request(partition_offset=None)) is None
    transient=deepcopy(job);transient['result_scope']['failure']['retryable']=True
    assert reusable([transient],'new',request()) is None
    legacy={**job,'result_scope':{'error':'path not found'}}
    assert reusable([legacy],'new',request()) is None
    changed=deepcopy(job);changed['result_scope']['failure']['resolver_version']='different'
    assert reusable([changed],'new',request()) is None


def test_reuse_has_explicit_logical_binding_without_new_physical_execution(tmp_path):
    s=Store(tmp_path/'c.db')
    j={'id':'j','fingerprint':'p','observation_ids':['o'],'result_scope':{'complete':True}}
    a={'id':'a','question_id':'q1','scope':{'task_id':'t'}}
    b={**a,'id':'b','question_id':'q2'}
    for intent in (a,b,a):bind_reuse(s,'c',j,intent)
    rows=s.list('test_result_use','c')
    assert len(rows)==2
    assert all(not r['new_execution'] and not r['independent_evidence'] for r in rows)
    assert {r['question_id'] for r in rows}=={'q1','q2'}
    assert not s.list('investigation_job','c')


def test_failure_reuse_never_crosses_source_run_evidence_or_resolver():
    j={'id':'j','status':'ingested','fingerprint':'old','source_run':'r','evidence_id':'e','request':request(),
       'result_scope':{'failure':{'code':'path_not_resolved','retryable':False,'resolver_version':'linux-image-resolution-1'}}}
    assert reusable([j],'new',request(byte_length=256),source_run='r',evidence_id='e')==j
    assert reusable([j],'new',request(),source_run='r2',evidence_id='e') is None
    assert reusable([j],'new',request(),source_run='r',evidence_id='e2') is None


def test_transient_retry_is_typed_bounded_and_bypasses_worker_failed_cache():
    from workbench.test_admission import execution_fingerprint
    j={'id':'j','status':'ingested','fingerprint':'base','request':request(),
       'result_scope':{'failure':{'code':'transient_read_error','retryable':True}}}
    key=execution_fingerprint([j],'base')
    assert key!='base' and reusable([j],key,request()) is None
    retry={**j,'id':'retry','fingerprint':key}
    assert execution_fingerprint([j,retry],'base')==key
    assert reusable([j,retry],key,request())==retry
    assert execution_fingerprint([{**j,'result_scope':{'error':'timeout'}}],'base')=='base'
