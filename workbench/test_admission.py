"""Controller-owned test feasibility, separate from model intent and budgets.

Capabilities describe immediate observables, never incident verdicts. A useful
discovery read is allowed without pretending it can compare an absent baseline
or establish historical execution. No evidence commands are executed here.
"""
from .models import TestDesign
from .retrieval import fingerprint_scope

VERSION = 'test-admission-2'
CAPABILITIES = {
    'search': ('matching_records','source_content'),
    'read_source': ('source_content',),
    'read_file': ('source_content',),
    'static_file': ('static_properties','source_content'),
    'archive_list': ('archive_members',),
    'correlate': ('record_association',),
}


def catalog(platform='linux'):
    names=('search','read_source','correlate') if platform=='windows' else tuple(CAPABILITIES)
    return {tool:list(CAPABILITIES[tool]) for tool in names}


def assess(call, platform='linux', observation_ids=(), *, source_locators=(),policy='legacy',context=None,current_context=None):
    from .test_contract_v2 import POLICY,is_v2,admit
    if policy==POLICY or is_v2(call.get('test_design')):
        return admit(call,context,current_context,catalog(platform))
    design=TestDesign.model_validate(call.get('test_design') or {}).model_dump(exclude_none=True)
    tool=call.get('tool');capabilities=catalog(platform)
    # Old persisted proposals have no design. Their operation remains a
    # discovery only; never claim that legacy free prose passed this gate.
    legacy=not bool(call.get('test_design'))
    if legacy and tool in capabilities:
        design['immediate_observable']=capabilities[tool][0]
    missing=sorted(set(design['required_observation_ids']+design['baseline_observation_ids'])-set(observation_ids))
    from .locator_admission import check
    binding=check(call,source_locators) if platform=='linux' else None
    reason=('unsupported_tool' if tool not in capabilities else
            'required_input_unavailable' if missing else
            'capability_intent_mismatch' if design['immediate_observable'] not in capabilities[tool] else
            binding['reason'] if binding and binding['status']=='contradicted' else None)
    return {'version':VERSION,'eligible':reason is None,'reason':reason,'design':design,
            'missing_observation_ids':missing,'legacy_untyped':legacy,
            'source_object_binding':binding,
            'scope':'Immediate observable feasibility only; no semantic truth, budget or permission expansion.'}


def reusable(jobs, fingerprint, call, *, source_run=None, evidence_id=None):
    """Prefer exact execution identity; reuse only typed resolution failures.

    Caller already filters case/evidence/task/generation/source-run scope.
    A changed inode/partition or source run stays eligible. Byte ranges differ
    for successful reads, but cannot repair an unchanged path-resolution miss.
    Legacy error prose and transient I/O errors are NEVER negative-cache keys.
    """
    jobs=[j for j in jobs if evidence_id is None or j.get('evidence_id')==evidence_id]
    # The exact pre-existing fingerprint already incorporates the source run.
    # Older graph jobs did not duplicate source_run as a standalone column.
    exact=next((j for j in jobs if j.get('fingerprint')==fingerprint
        and (source_run is None or j.get('source_run',source_run)==source_run)),None)
    if exact:return exact
    jobs=[j for j in jobs if source_run is None or j.get('source_run')==source_run]
    if call.get('tool')!='read_file':return None
    identity={k:fingerprint_scope(call).get(k) for k in ('tool','path','partition_offset','inode')}
    for job in jobs:
        scope=job.get('result_scope') or {}
        failure=scope.get('failure') or {}
        if job.get('status')!='ingested' or failure.get('code')!='path_not_resolved' or failure.get('retryable') is not False:
            continue
        if failure.get('resolver_version')!='linux-image-resolution-1':continue
        prior={k:fingerprint_scope(job['request']).get(k) for k in identity}
        if prior==identity:return job
    return None


def execution_fingerprint(jobs, fingerprint):
    """One bounded retry for a typed transient failure; never retry by prose.

    A distinct worker key is essential: reusing the first key would merely
    return the worker's cached failed result. Admission still applies ordinary
    tool and assessment budgets. An exhausted retry stays an explicit gap.
    """
    prior=next((j for j in reversed(jobs) if j.get('fingerprint')==fingerprint),None)
    if (prior and prior.get('status')=='ingested'
            and (prior.get('result_scope',{}).get('failure') or {}).get('retryable') is True):
        return fingerprint+':transient-retry:1'
    return fingerprint


def bind_reuse(store,cid,job,intent):
    """Explicit logical use of a prior physical result, not new evidence."""
    if not intent:return
    if any(r.get('case_id',cid)!=cid for r in (job,intent)):
        raise ValueError('foreign result reuse')
    scope=intent.get('scope',{})
    for key in ('task_id','evidence_id','generation','source_run'):
        if key in job and key in scope and job[key]!=scope[key]:
            raise ValueError('result reuse scope mismatch: '+key)
    rows=store.list('test_result_use',cid)
    if any(r['job_id']==job['id'] and r['test_intent_id']==intent['id'] for r in rows):return
    store.add('test_result_use',cid,job_id=job['id'],test_intent_id=intent['id'],
        question_id=intent.get('question_id'),task_id=intent.get('scope',{}).get('task_id'),
        generation=intent.get('scope',{}).get('generation',0),
        original_observation_ids=job.get('observation_ids',[]),result_scope=job.get('result_scope',{}),
        new_execution=False,independent_evidence=False,
        reason='existing_result' if job.get('fingerprint') else 'existing_resolution_result')
