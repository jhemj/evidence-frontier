"""Opt-in purpose/outcome contracts, not a replacement for legacy validation.

All references are Controller-offered, versioned retained objects. A discovery
result says what was collected in its scope, not whether a hypothesis is true.
Reinterpreting a returned result creates new logical lineage, never evidence.
This module performs no I/O other than caller-provided local Store reads.
"""
from copy import deepcopy
import hashlib
import json

from .models import TestDesignV2
from pydantic import ValidationError

POLICY='purpose-outcomes-v2'
UNSUPPORTED='unsupported_by_this_test'
VIEWS={'metadata':0,'body_excerpt':1,'full_body':2}


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()


def is_v2(value):
    return isinstance(value,dict) and value.get('version')==2


def object_version(row):
    """Semantic object identity excludes queue/status/display bookkeeping."""
    kind=row.get('kind')
    if kind=='case_question':
        value={k:row.get(k) for k in ('id','kind','case_id','task_id','evidence_id','generation',
            'definition_revision','dependency_revision','question','source_ids')}
    elif kind=='dossier':
        # A dossier is the logical review owner, not an adopted proposition.
        # Updating its answer cannot silently rewrite its input membership.
        value={k:row.get(k) for k in ('id','kind','case_id','task_id','evidence_id','generation',
            'revision','observation_ids','all_observation_ids','title','group_key','partition_version','superseded')}
    elif kind in ('hypothesis','claim'):
        value={k:row.get(k) for k in ('id','kind','case_id','task_id','evidence_id','generation',
            'revision','observation_ids','finding','statement','text','title','question','reasoning',
            'supporting_evidence_ids','refuting_evidence_ids','superseded')}
    else:
        value=row
    return digest(value)


def object_ref(row):
    return {'kind':row['kind'],'id':row['id'],'version':object_version(row)}


def presented_view(row):
    """Full retained material is NOT proof that the complete body was shown."""
    fields=row.get('fields') or {}
    body=fields.get('excerpt')
    if not (isinstance(body,str) and body) and not fields.get('excerpt_spans') and not row.get('source_span'):
        return 'metadata'
    # Only an explicit upstream presentation marker grants full-body status.
    # source_complete usually describes the physical extraction, not this page.
    presentation=row.get('test_contract_presentation') or {}
    if presentation.get('full_body_verified') is True and not fields.get('excerpt_truncated'):
        return 'full_body'
    return 'body_excerpt'


def bind_presented_view(shown,canonical):
    """Controller-only marker after byte-for-byte whole-body comparison.

    An excerpt/source_complete flag supplied by evidence or a model does not
    certify a whole presentation. This helper never fetches omitted material.
    """
    result=deepcopy(shown)
    result.pop('test_contract_presentation',None)
    visible=(shown.get('fields') or {}).get('excerpt')
    retained=(canonical.get('fields') or {}).get('excerpt')
    full=(isinstance(visible,str) and bool(visible) and visible==retained
          and not (canonical.get('fields') or {}).get('excerpt_truncated')
          and (canonical.get('fields') or {}).get('source_complete') is True)
    if full:result['test_contract_presentation']={'full_body_verified':True,'canonical_version':object_version(canonical)}
    return result


def _proposition(row):
    finding=row.get('finding') or row
    return next((finding.get(k) for k in ('statement','text','card_summary','title','question')
                 if isinstance(finding.get(k),str) and finding[k].strip()),'')


def build_manifest(store,cid,task,evidence,source_run,*,owners=(),questions=(),observations=(),jobs=()):
    """Build from *actual presented* objects; no all-case implicit allowlist.

    Pass resolved model-view observations, not retained canonical bodies. Their
    versions are computed from the retained originals but view capabilities
    come only from the supplied presentation. Unknown trust remains unknown.
    """
    scope={'case_id':cid,'task_id':task['id'],'evidence_id':evidence['id'],
           'generation':task.get('retry_generation',0),'source_run':source_run}
    objects=[]
    for shown in list(owners)+list(questions)+list(observations):
        try:row=store.get(shown['id'])
        except (KeyError,ValueError,TypeError):continue
        if row.get('case_id')!=cid:continue
        if row.get('evidence_id',evidence['id'])!=evidence['id']:continue
        unscoped_coverage=(row.get('kind')=='hypothesis' and row.get('hypothesis_kind')!='dynamic'
                           and row.get('task_id') is None and row.get('generation') is None)
        if row.get('kind') not in ('observation','evidence','case_question') and not unscoped_coverage:
            if row.get('task_id')!=task['id'] or row.get('generation',0)!=scope['generation']:continue
        if row.get('kind')=='observation' and row.get('source_run',source_run)!=source_run:continue
        ref=object_ref(row)
        view_row=bind_presented_view(shown,row) if row['kind']=='observation' else shown
        item={'ref':ref,'view':presented_view(view_row) if row['kind']=='observation' else 'metadata',
              'trust_bases':['unassessed','retained_source'],'proposition':_proposition(shown)}
        # Independence cannot be inferred from separate file names or IDs.
        if row.get('trust_basis_verified')=='independent_baseline':item['trust_bases'].append('independent_baseline')
        if item not in objects:objects.append(item)
    lineages=[]
    from .review_contracts import contracts
    for job in jobs:
        if (job.get('case_id')!=cid or job.get('task_id')!=task['id']
                or job.get('evidence_id')!=evidence['id'] or job.get('generation',0)!=scope['generation']
                or job.get('source_run')!=source_run or job.get('status')!='ingested'):continue
        for contract in contracts(job):
            lineages.append({'parent_contract_id':contract['contract_id'],'job_id':job['id'],
                'result_revision':digest({'job_id':job['id'],'observation_ids':job.get('observation_ids',[]),'scope':job.get('result_scope') or {}})})
    return {'version':2,'scope':scope,'objects':objects,'result_lineages':lineages}


def current_manifest(store,cid,manifest):
    objects=[]
    for item in manifest.get('objects',[]):
        try:row=store.get(item['ref']['id'],item['ref']['kind'])
        except (KeyError,ValueError,TypeError):continue
        if row.get('case_id')!=cid:continue
        # Preserve actual presented view; refresh only retained identity/trust.
        objects.append({**item,'ref':object_ref(row),
            'trust_bases':['unassessed','retained_source']+
                (['independent_baseline'] if row.get('trust_basis_verified')=='independent_baseline' else [])})
    lineages=[]
    for item in manifest.get('result_lineages',[]):
        try:job=store.get(item['job_id'],'investigation_job')
        except ValueError:continue
        actual=digest({'job_id':job['id'],'observation_ids':job.get('observation_ids',[]),'scope':job.get('result_scope') or {}})
        if job.get('case_id')==cid and actual==item['result_revision']:lineages.append(item)
    return {**manifest,'objects':objects,'result_lineages':lineages}


def admit(call,context,current_context,capabilities):
    try:design=TestDesignV2.model_validate(call.get('test_design')).model_dump()
    except (ValueError,TypeError) as error:
        semantic=isinstance(error,ValidationError) and any(e['type']=='value_error' for e in error.errors())
        return {'eligible':False,'reason':'invalid_test_design' if semantic else 'contract_encoding_error','design':call.get('test_design') or {},
                'diagnostics':['purpose/outcome schema or required discriminator is invalid']}
    if not isinstance(context,dict) or not isinstance(current_context,dict):
        return {'eligible':False,'reason':'internal_reference_error','design':design,'diagnostics':['No saved exact input manifest']}
    offered=context.get('objects',[]);current=current_context.get('objects',[])
    def exact(ref):return next((o for o in offered if o.get('ref')==ref),None)
    def fresh(ref):return any(o.get('ref')==ref for o in current)
    errors=[]
    if design['target_scope']!=context.get('scope') or context.get('scope')!=current_context.get('scope'):
        errors.append('target_scope_not_current')
    if design['design_timing']=='after_result':
        lineage=design['lineage']
        if not any(all(r.get(k)==lineage[k] for k in lineage) for r in context.get('result_lineages',[])):
            errors.append('result_lineage_not_presented')
        elif not any(all(r.get(k)==lineage[k] for k in lineage) for r in current_context.get('result_lineages',[])):
            errors.append('result_lineage_changed')
    if design['owner_ref']['id']!=call.get('hypothesis_id',design['owner_ref']['id']):errors.append('logical_owner_mismatch')
    if not call.get('hypothesis_id') and design['owner_ref']!=design['question_ref']:
        errors.append('planner_owner_must_be_exact_question')
    if design['question_ref']['id']!=call.get('question_id'):errors.append('question_mismatch')
    refs=[design['owner_ref'],design['question_ref']]+([design['target_ref']] if design['target_ref'] else [])
    for ref in refs:
        if not exact(ref) or not fresh(ref):errors.append('unoffered_or_stale_object_ref')
    target=exact(design['target_ref']) if design['target_ref'] else None
    if target and target.get('proposition')!=design['target_proposition']:
        errors.append('target_proposition_mismatch')
    for required in design['required_inputs']:
        if required['scope']!=design['target_scope']:errors.append('required_input_scope_mismatch')
        item=exact(required['ref'])
        if required['ref']['kind']!='observation' or not item or not fresh(required['ref']):
            errors.append('unoffered_or_stale_input_ref');continue
        if required['trust_basis'] not in item.get('trust_bases',[]):errors.append('unverified_trust_basis')
    reason='internal_reference_error' if errors else None
    if reason is None:
        if call.get('tool') not in capabilities or design['immediate_observable'] not in capabilities.get(call.get('tool'),[]):
            reason='capability_mismatch';errors.append('tool_cannot_supply_immediate_observable')
        elif design['required_result_view']!='metadata' and call.get('tool') in ('static_file','archive_list','correlate'):
            reason='capability_mismatch';errors.append('tool_does_not_return_requested_body_view')
        elif any(VIEWS[exact(r['ref']).get('view','metadata')]<VIEWS[r['required_view']] for r in design['required_inputs']):
            reason='presentation_requirement_unmet';errors.append('required_input_body_not_presented')
    return {'version':POLICY,'eligible':reason is None,'reason':reason,'design':design,
            'diagnostics':errors,'legacy_untyped':False,'scope':'Feasibility and exact references only; no truth or execution authority.'}


def result_errors(design,job,observations,*,presented_observations=None,outcome=None,refs=()):
    """Revalidate actual result/view; extraction completion is not truth."""
    errors=[];scope=job.get('result_scope') or {}
    if design.get('design_timing')=='after_result':
        actual=job.get('result_revision') or digest({'job_id':job['id'],'observation_ids':job.get('observation_ids',[]),'scope':scope})
        if (design.get('lineage') or {}).get('result_revision')!=actual:errors.append('result_lineage_changed')
    if outcome is not None and outcome not in design['allowed_outcomes']:
        errors.append('outcome_not_supported_by_this_test')
    if design['purpose']=='discover' and outcome in ('supports','refutes','inconclusive'):
        errors.append('discovery_not_hypothesis_discrimination')
    returned=set(job.get('observation_ids',[]))
    if set(refs)-returned:errors.append('result_reference_scope_mismatch')
    actual={i:observations[i] for i in returned if i in observations}
    shown=actual if presented_observations is None else {i:presented_observations[i] for i in actual if i in presented_observations}
    positive=outcome in ('supports','refutes','found')
    if positive and set(refs)-set(actual):errors.append('internal_reference_error')
    required=design['required_result_view']
    selected=[shown[i] for i in refs if i in shown] if outcome is not None else list(shown.values())
    if required!='metadata' and (positive or outcome is None):
        if not selected or any(VIEWS[presented_view(o)]<VIEWS[required] for o in selected):
            errors.append('presentation_requirement_unmet')
    if positive and not refs:errors.append('check_missing_support')
    if outcome=='no_match_in_scope' and (not scope.get('complete') or job.get('status') not in ('ingested','covered','covered_zero')):
        errors.append('partial_scope_not_no_match')
    if outcome=='unavailable' and not (scope.get('failure') or not returned):
        errors.append('available_result_not_unavailable')
    if positive and scope.get('failure') and not actual:errors.append('material_unavailable')
    return list(dict.fromkeys(errors))


def after_result(call,previous_contract,job):
    """New immutable logical contract for an existing physical result."""
    result=deepcopy(call)
    design=TestDesignV2.model_validate(result.get('test_design')).model_dump()
    design.update(design_timing='after_result',uses_existing_result=True,lineage={
        'parent_contract_id':previous_contract['contract_id'],
        'result_revision':digest({'job_id':job['id'],'observation_ids':job.get('observation_ids',[]),'scope':job.get('result_scope') or {}})})
    result['test_design']=TestDesignV2.model_validate(design).model_dump()
    return result


def bind_followup_owner(store,cid,task,evidence,source_run,call,original_intent,new_owner,source_record_id):
    """Explicit Controller routing handoff, never a model-selected new owner.

    Synthesis first owns a coverage/hypothesis test, then creates a follow-up
    review dossier. Preserve the original intent and bind a NEW child contract.
    A shared source/question alone cannot authorize this ownership transfer.
    """
    from .review_contracts import contract
    original=contract(call)
    scope=(original_intent.get('scope') or {})
    if (original.get('contract_version')!=2 or not original_intent.get('admission',{}).get('eligible')
            or original_intent.get('case_id')!=cid
            or scope.get('task_id')!=task['id'] or scope.get('evidence_id')!=evidence['id']
            or scope.get('generation',0)!=task.get('retry_generation',0) or scope.get('source_run')!=source_run
            or (scope.get('logical_contract') or {}).get('contract_id')!=original['contract_id']):
        raise ValueError('Unverified original test owner')
    if (new_owner.get('case_id')!=cid or new_owner.get('kind')!='dossier'
            or new_owner.get('task_id')!=task['id'] or new_owner.get('evidence_id')!=evidence['id']
            or new_owner.get('generation',0)!=task.get('retry_generation',0)
            or new_owner.get('origin_hypothesis_id')!=original['dossier_id']):
        raise ValueError('Follow-up owner is not an explicit child of the original hypothesis')
    source=store.get(source_record_id)
    context=source.get('test_contract_context',(source.get('pack') or {}).get('test_contract_context'))
    if (not context or source.get('case_id')!=cid or source.get('task_id')!=task['id']
            or source.get('generation',0)!=task.get('retry_generation',0)):
        raise ValueError('Missing original immutable test input')
    result=deepcopy(call)
    result['hypothesis_id']=new_owner['id']
    result['test_design']['owner_ref']=object_ref(new_owner)
    lineage={'relation':'controller_followup_owner','parent_contract_id':original['contract_id'],
             'original_intent_id':original_intent['id'],'source_record_id':source_record_id,
             'from_ref':call['test_design']['owner_ref'],'to_ref':object_ref(new_owner)}
    result['_controller_owner_lineage']=lineage
    projected=deepcopy(context)
    projected['objects'].append({'ref':object_ref(new_owner),'view':'metadata',
        'trust_bases':['unassessed','retained_source'],'proposition':_proposition(new_owner)})
    binding=store.add('receipt',cid,receipt_type='test_contract_binding',task_id=task['id'],evidence_id=evidence['id'],
        generation=task.get('retry_generation',0),source_record_id=source_record_id,
        original_intent_id=original_intent['id'],controller_owner_lineage=lineage,
        test_contract_context=projected,proposal=result,
        scope='Controller routing lineage only; the model did not select the new dossier owner.')
    return result,binding


def bind_existing_result(store,cid,task,evidence,source_run,call,original_intent,job,source_record_id,*,parent_contract):
    """Controller-owned new after-result contract, with exact physical lineage.

    The old proposal/input/intent remain unchanged. Material not presented in
    the model's original input is not added to its body allowlist by this step.
    It must be shown to a later evaluator before a positive adoption.
    """
    from .review_contracts import contract,contracts
    original=contract(call);scope=original_intent.get('scope') or {}
    if (original.get('contract_version')!=2 or not original_intent.get('admission',{}).get('eligible')
            or original_intent.get('case_id')!=cid or scope.get('task_id')!=task['id']
            or scope.get('evidence_id')!=evidence['id'] or scope.get('source_run')!=source_run
            or scope.get('generation',0)!=task.get('retry_generation',0)
            or (scope.get('logical_contract') or {}).get('contract_id')!=original['contract_id']):
        raise ValueError('Unverified original contract for result reuse')
    if (job.get('case_id')!=cid or job.get('task_id')!=task['id'] or job.get('evidence_id')!=evidence['id']
            or job.get('generation',0)!=task.get('retry_generation',0)
            or job.get('source_run')!=source_run or job.get('status')!='ingested'
            or parent_contract not in contracts(job)):
        raise ValueError('Existing result/parent contract is not in this exact scope')
    source=store.get(source_record_id)
    context=source.get('test_contract_context',(source.get('pack') or {}).get('test_contract_context'))
    if not context or source.get('case_id')!=cid or source.get('task_id')!=task['id']:
        raise ValueError('No saved original proposal input')
    result=after_result(call,parent_contract,job)
    projected=deepcopy(context)
    projected.setdefault('result_lineages',[]).append({**result['test_design']['lineage'],'job_id':job['id']})
    binding=store.add('receipt',cid,receipt_type='test_contract_binding',task_id=task['id'],evidence_id=evidence['id'],
        generation=task.get('retry_generation',0),source_record_id=source_record_id,
        original_intent_id=original_intent['id'],controller_owner_lineage=result.get('_controller_owner_lineage'),
        test_contract_context=projected,proposal=result,
        scope='Controller result reuse after inspection; no new execution, independent evidence or original-input body expansion.')
    return result,binding


def controller_collection(store,cid,task,evidence,source_run,call,question):
    """Typed discovery for an existing Controller-owned source reread path.

    This does not turn a claim challenge into positive hypothesis verification.
    It has no model output, body judgment, or implicit independent baseline.
    """
    context=build_manifest(store,cid,task,evidence,source_run,owners=[question],questions=[question])
    rules={k:UNSUPPORTED for k in ('supports','refutes','inconclusive','found','no_match_in_scope','partial','unavailable')}
    rules.update(found='The requested source or matching record is returned within the actual requested scope',
        no_match_in_scope='The complete requested current search scope contains no matching record',
        partial='The returned collection covers only part of the requested scope',
        unavailable='The requested collection cannot be obtained with this execution')
    result=deepcopy(call)
    result.update(question_id=question['id'],success_condition=rules['found'],refutation_condition='',inconclusive_condition=rules['partial'])
    result['test_design']=TestDesignV2.model_validate({'version':2,'purpose':'discover',
        'immediate_observable':'matching_records' if call['tool']=='search' else 'source_content',
        'owner_ref':object_ref(question),'question_ref':object_ref(question),'target_ref':None,
        'target_scope':context['scope'],'target_proposition':'','required_inputs':[],'baseline_ref':None,
        'required_result_view':'metadata' if call['tool']=='search' else 'body_excerpt',
        'outcome_rules':rules,'allowed_outcomes':['found','no_match_in_scope','partial','unavailable'],
        'design_timing':'before_result','uses_existing_result':False,'lineage':None,
        'expected_update':'Retrieve source context for later interpretation; not execution, approval or intrusion proof.',
        'reopen_on':'Changed source object, coordinate or extraction capability.'}).model_dump()
    source=store.add('receipt',cid,receipt_type='test_contract_binding',task_id=task['id'],evidence_id=evidence['id'],
        generation=task.get('retry_generation',0),test_contract_context=context,proposal=result,
        scope='Controller-generated source collection; not a model inference or independent verification.')
    return result,source
