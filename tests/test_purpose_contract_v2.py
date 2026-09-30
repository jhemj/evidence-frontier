"""No-model, anonymous reproductions of retained contract failure shapes."""
from copy import deepcopy
import hashlib
import json

import pytest

from workbench.models import (TestDesignV2 as DesignV2,TestObjectRef as ObjectRef,
    ProviderConfig,JudgmentReport,review_output_schema,InvestigationPlanV2)
from workbench.store import Store
from workbench.review_contracts import contract,attach
from workbench.test_admission import assess,bind_reuse
from workbench.test_contract_v2 import (build_manifest,current_manifest,object_ref,
    POLICY,UNSUPPORTED,after_result,bind_presented_view,result_errors,bind_followup_owner,
    bind_existing_result,controller_collection)
from workbench.review_validation import check_errors,errors
from workbench.review_policy import logical_owner
from workbench import question_engine


@pytest.fixture
def fixture(tmp_path):
    store=Store(tmp_path/'fixture.db')
    case=store.add('case','',target_os='linux');cid=case['id'];store.update(cid,case_id=cid)
    evidence=store.add('evidence',cid,connected=True,signature='fixture-image')
    task=store.add('task',cid,evidence_id=evidence['id'],retry_generation=0,test_contract_policy=POLICY)
    owner=store.add('dossier',cid,task_id=task['id'],evidence_id=evidence['id'],generation=0,
        title='Does the retained record contain a configuration line?',observation_ids=[])
    question=store.add('case_question',cid,task_id=task['id'],evidence_id=evidence['id'],generation=0,
        question_key='question',question='What does this record establish?',definition_revision='definition',dependency_revision='dependency',version=1)
    source=store.add('observation',cid,evidence_id=evidence['id'],source_run='run',type='linux_configuration',
        fields={'path':'/fixture/config','excerpt':'setting=value','source_complete':True,'excerpt_truncated':False})
    manifest=build_manifest(store,cid,task,evidence,'run',owners=[owner],questions=[question],observations=[source])
    rules={k:UNSUPPORTED for k in ('supports','refutes','inconclusive','found','no_match_in_scope','partial','unavailable')}
    rules.update(found='The requested record is returned',no_match_in_scope='Complete current requested scope has no match',
        partial='Only part of requested scope is available',unavailable='The requested material could not be obtained')
    design={'version':2,'purpose':'discover','immediate_observable':'source_content',
        'owner_ref':object_ref(owner),'question_ref':object_ref(question),'target_ref':None,
        'target_scope':manifest['scope'],'target_proposition':'','required_inputs':[],
        'baseline_ref':None,'required_result_view':'body_excerpt','outcome_rules':rules,
        'allowed_outcomes':['found','no_match_in_scope','partial','unavailable'],
        'design_timing':'before_result','uses_existing_result':False,'lineage':None,
        'expected_update':'Retrieve a current scoped source; not execution or authorization proof','reopen_on':'A changed source coordinate'}
    call={'tool':'read_file','path':'/fixture/config','query':'',
        'hypothesis_id':owner['id'],'question_id':question['id'],'success_condition':'requested source found',
        'refutation_condition':'','inconclusive_condition':'partial extraction','test_design':design}
    return store,cid,evidence,task,owner,question,source,manifest,call


def admission(fixture,call=None,manifest=None):
    store,cid,_,_,_,_,_,context,original=fixture
    context=manifest or context
    return assess(call or original,policy=POLICY,context=context,current_context=current_manifest(store,cid,context))


def job(fixture,call=None,*,complete=True,observations=None):
    store,cid,evidence,task,owner,question,source,manifest,original=fixture
    call=call or original
    input_record=store.add('review_input',cid,task_id=task['id'],evidence_id=evidence['id'],generation=0,
        pack={'test_contract_context':manifest})
    intent=question_engine.reserve(store,cid,task,question,call,evidence,'run',source_record_id=input_record['id'])
    condition=contract(call)
    physical=store.add('investigation_job',cid,task_id=task['id'],evidence_id=evidence['id'],generation=0,
        source_run='run',status='ingested',request=call,contracts=[condition],test_intent_ids=[intent['id']],
        observation_ids=[source['id']] if observations is None else observations,result_scope={'complete':complete})
    question_engine.finish_intents(store,cid,physical)
    return store.get(intent['id']),physical,condition


def outcome(physical,condition,outcome='found',refs=None):
    return {'check_id':physical['id'],'dossier_id':condition['dossier_id'],'contract_id':condition['contract_id'],
        'outcome':outcome,'basis':'positive_evidence','reason':'Only this scoped observation is established',
        'observation_ids':physical['observation_ids'] if refs is None else refs}


def discriminate(call,owner,*,support=True,refute=False):
    result=deepcopy(call);design=result['test_design']
    rules={k:UNSUPPORTED for k in design['outcome_rules']}
    rules['inconclusive']='The required result does not discriminate this proposition'
    if support:rules['supports']='An exact returned record positively matches this proposition'
    if refute:rules['refutes']='An exact contrary record falsifies this proposition'
    design.update(purpose='discriminate',target_ref=object_ref(owner),target_proposition=owner['title'],
        outcome_rules=rules,allowed_outcomes=[k for k,v in rules.items() if v!=UNSUPPORTED])
    return result


def test_legacy_default_and_schemas_are_not_changed(fixture):
    assert ProviderConfig().test_contract_policy=='legacy'
    legacy={'hypothesis_id':'owner','success_condition':'positive','refutation_condition':''}
    conditions={k:legacy.get(k,'') for k in ('success_condition','refutation_condition','inconclusive_condition')}
    assert contract(legacy)['contract_id']==hashlib.sha256(json.dumps(conditions,sort_keys=True).encode()).hexdigest()
    assert 'TestDesignV2' not in JudgmentReport.model_json_schema().get('$defs',{})
    assert review_output_schema('judgment',{}).__name__=='JudgmentReport'
    assert review_output_schema('judgment',{'test_contract_policy':POLICY}).__name__=='JudgmentReportV2'
    assert 'TestDesignV2' in InvestigationPlanV2.model_json_schema()['$defs']
    # The old known blank-refutation failure remains a failure.
    check={'id':'legacy','status':'covered','observation_ids':['o'],'contracts':[contract(legacy)]}
    assessment={'check_id':'legacy','dossier_id':'owner','contract_id':contract(legacy)['contract_id'],'outcome':'supports','observation_ids':['o']}
    assert any(e['code']=='missing_discriminating_condition' for e in check_errors({'check_assessments':[assessment]},[check],{'owner':['o']}))


@pytest.mark.parametrize('support,refute',[(True,False),(False,True),(True,True)])
def test_one_sided_discriminator_is_explicit_and_valid(fixture,support,refute):
    call=discriminate(fixture[-1],fixture[4],support=support,refute=refute)
    assert admission(fixture,call)['eligible']
    intent,physical,condition=job(fixture,call)
    value='supports' if support else 'refutes'
    source=fixture[6]
    assert check_errors({'check_assessments':[outcome(physical,condition,value)]},[physical],
        {fixture[4]['id']:[source['id']]},presented_observations={source['id']:source})==[]
    if not refute:
        assert 'outcome_not_supported_by_this_test' in [e['code'] for e in check_errors(
            {'check_assessments':[outcome(physical,condition,'refutes')]},[physical],
            {fixture[4]['id']:[source['id']]},presented_observations={source['id']:source})]


def test_blank_both_sides_fail_before_transport(fixture):
    call=discriminate(fixture[-1],fixture[4],support=False,refute=False)
    assert admission(fixture,call)['reason']=='invalid_test_design'
    with pytest.raises(ValueError):contract(call)


def test_discovery_can_collect_fact_but_not_support_a_hypothesis(fixture):
    assert admission(fixture)['eligible']
    intent,physical,condition=job(fixture)
    source=fixture[6]
    assert check_errors({'check_assessments':[outcome(physical,condition)]},[physical],
        {fixture[4]['id']:[source['id']]},presented_observations={source['id']:source})==[]
    bad={'check_assessments':[outcome(physical,condition,'supports')]}
    codes={e['code'] for e in check_errors(bad,[physical],{fixture[4]['id']:[source['id']]},presented_observations={source['id']:source})}
    assert 'discovery_not_hypothesis_discrimination' in codes
    # A separately validated narrow literal still uses unchanged fact checks.
    finding={'dossier_id':fixture[4]['id'],'judgment':'확인','observation_ids':[source['id']],
        'fact_assertions':[{'observation_id':source['id'],'pointer':'/fields/excerpt','relation':'equals','value':'setting=value'}]}
    assert not errors({'findings':[finding]},[fixture[4]['id']],[source['id']],observations={source['id']:source},canonical_observations={source['id']:source})


def test_metadata_is_not_body_or_execution_success(fixture):
    source=fixture[6];meta=deepcopy(source);meta['fields'].pop('excerpt')
    manifest=build_manifest(fixture[0],fixture[1],fixture[3],fixture[2],'run',owners=[fixture[4]],questions=[fixture[5]],observations=[meta])
    call=deepcopy(fixture[-1]);call['test_design']['required_inputs']=[{
        'ref':object_ref(source),'role':'context','required_view':'body_excerpt','trust_basis':'retained_source','scope':manifest['scope']}]
    assert admission(fixture,call,manifest)['reason']=='presentation_requirement_unmet'
    call['test_design']['required_inputs'][0]['required_view']='metadata'
    assert admission(fixture,call,manifest)['eligible']
    intent,physical,condition=job(fixture)
    codes={e['code'] for e in check_errors({'check_assessments':[outcome(physical,condition)]},[physical],
        {fixture[4]['id']:[source['id']]},presented_observations={source['id']:meta},canonical_observations={source['id']:source})}
    assert 'presentation_requirement_unmet' in codes
    static=deepcopy(fixture[-1]);static['tool']='static_file';static['test_design']['immediate_observable']='static_properties'
    assert admission(fixture,static)['reason']=='capability_mismatch'


def test_exact_scope_owner_input_versions_and_trust_fail_closed(fixture):
    for mutate in ('owner','question','scope','target','input','trust'):
        call=deepcopy(fixture[-1]);design=call['test_design']
        if mutate=='owner':call['hypothesis_id']='other-owner'
        elif mutate=='question':call['question_id']='other-question'
        elif mutate=='scope':design['target_scope']['generation']=2
        elif mutate=='target':design['owner_ref']['version']='0'*64
        else:
            design['required_inputs']=[{'ref':object_ref(fixture[6]),'role':'context',
                'required_view':'metadata','trust_basis':'retained_source','scope':design['target_scope']}]
            if mutate=='input':design['required_inputs'][0]['ref']['version']='0'*64
            else:design['required_inputs'][0]['trust_basis']='independent_baseline'
        assert admission(fixture,call)['reason']=='internal_reference_error'
    wrong=deepcopy(fixture[-1]);wrong['test_design']['version']='2'
    assert admission(fixture,wrong)['reason']=='contract_encoding_error'


def test_changed_purpose_rule_view_or_version_changes_contract_hash(fixture):
    call=fixture[-1];old=contract(call)['contract_id']
    for mutate in ('view','rule','version','proposition'):
        new=deepcopy(call)
        if mutate=='view':new['test_design']['required_result_view']='metadata'
        elif mutate=='rule':new['test_design']['outcome_rules']['found']='Different narrow collection criterion'
        elif mutate=='version':new['test_design']['owner_ref']['version']='0'*64
        else:new=discriminate(call,fixture[4])
        assert contract(new)['contract_id']!=old


def test_existing_result_new_contract_has_lineage_not_independence(fixture):
    intent,physical,old=job(fixture)
    new=after_result(fixture[-1],old,physical)
    assert new['test_design']['design_timing']=='after_result'
    assert contract(new)['contract_id']!=old['contract_id']
    assert fixture[-1]['test_design']['design_timing']=='before_result'
    context=build_manifest(fixture[0],fixture[1],fixture[3],fixture[2],'run',owners=[fixture[4]],questions=[fixture[5]],observations=[fixture[6]],jobs=[physical])
    assert admission(fixture,new,context)['eligible']
    assert admission(fixture,new)['reason']=='internal_reference_error'
    attached=attach(physical,new)
    assert old in attached and contract(new) in attached and len(attached)==2
    bind_reuse(fixture[0],fixture[1],physical,intent)
    receipt=fixture[0].list('test_result_use',fixture[1])[0]
    assert receipt['new_execution'] is False and receipt['independent_evidence'] is False
    fixture[0].update(physical['id'],result_scope={'complete':False,'changed':True})
    assert admission(fixture,new,context)['reason']=='internal_reference_error'


def test_exact_writer_does_not_transfer_or_adopt_hidden_body(fixture):
    store,cid,_,task,owner,_,source,_,_=fixture
    intent,physical,condition=job(fixture)
    assert logical_owner(intent,physical)==condition
    value=outcome(physical,condition)
    question_engine.record_check_assessments(store,cid,task,{'check_assessments':[value]},'hidden')
    assert store.get(intent['id'])['assessment_status']=='unassessed'
    question_engine.record_check_assessments(store,cid,task,{'check_assessments':[value]},'shown',presented_observations={source['id']:source})
    assert store.get(intent['id'])['assessment_status']=='assessed'
    assert len(store.get(intent['id'])['assessment_history'])==1
    other=deepcopy(intent);other['scope']['logical_contract']['dossier_id']='another-owner'
    assert logical_owner(other,physical) is None
    # An explicit stale source/question version is never promoted to current.
    store.update(fixture[5]['id'],dependency_revision='changed-dependency')
    newjob=store.update(physical['id'],result_scope={'complete':False})
    question_engine.finish_intents(store,cid,newjob)
    question_engine.record_check_assessments(store,cid,task,{'check_assessments':[value]},'stale',presented_observations={source['id']:source})
    assert store.get(intent['id'])['assessment_status']=='unassessed'


def test_partial_zero_results_do_not_become_no_match_or_refutation(fixture):
    _,physical,condition=job(fixture,complete=False,observations=[])
    assert 'partial_scope_not_no_match' in result_errors(condition['test_design'],physical,{},outcome='no_match_in_scope')
    assert not result_errors(condition['test_design'],physical,{},outcome='partial')
    assert not result_errors(condition['test_design'],physical,{},outcome='unavailable')


def test_full_body_requires_actual_whole_canonical_presentation(fixture):
    source=fixture[6];shown=deepcopy(source)
    shown['fields']['excerpt']='setting'
    shown['fields']['presentation_full_body']=True  # Untrusted evidence flag.
    assert bind_presented_view(shown,source).get('test_contract_presentation') is None
    complete=bind_presented_view(source,source)
    assert complete['test_contract_presentation']['full_body_verified']


@pytest.mark.parametrize('shape',[
    'discovery_blank_refutation','metadata_only_body_intent','question_id_as_owner',
    'missing_target_proposition','partial_search_as_refutation'])
def test_five_anonymous_retained_failure_shapes(fixture,shape):
    call=deepcopy(fixture[-1])
    if shape=='discovery_blank_refutation':
        assert admission(fixture,call)['eligible']  # Only collection, never hypothesis support.
    elif shape=='metadata_only_body_intent':
        call['tool']='static_file';call['test_design']['immediate_observable']='static_properties'
        assert admission(fixture,call)['reason']=='capability_mismatch'
    elif shape=='question_id_as_owner':
        call['hypothesis_id']=fixture[5]['id']
        assert admission(fixture,call)['reason']=='internal_reference_error'
    elif shape=='missing_target_proposition':
        call=discriminate(call,fixture[4]);call['test_design']['target_proposition']=''
        assert admission(fixture,call)['reason']=='invalid_test_design'
    else:
        _,physical,condition=job(fixture,complete=False,observations=[])
        assert 'outcome_not_supported_by_this_test' in result_errors(condition['test_design'],physical,{},outcome='refutes')


def test_new_physical_scope_and_legacy_generation_are_not_rewritten(fixture):
    from workbench.check_ledger import key
    call=fixture[-1];owner={'task_id':fixture[3]['id'],'evidence_id':fixture[2]['id'],'generation':0}
    for change in ({'partition_offset':4096},{'inode':123},{'byte_offset':8192},{'path':'/fixture/new'}):
        assert key(call,owner)!=key({**call,**change},owner)
    design=deepcopy(call['test_design']);design.pop('version')
    old=assess({'tool':'read_file','test_design':{'purpose':'discover'}},observation_ids=[])
    assert old['design']['purpose']=='discover' and old['legacy_untyped'] is False
    assert admission(fixture,{**call,'test_design':{'purpose':'discover'}})['reason']=='contract_encoding_error'


def test_explicit_followup_owner_creates_new_contract_and_keeps_original(fixture):
    store,cid,evidence,task,owner,question,source,manifest,call=fixture
    plan=store.add('synthesis_input',cid,task_id=task['id'],evidence_id=evidence['id'],generation=0,
        pack={'test_contract_context':manifest})
    original=question_engine.reserve(store,cid,task,question,call,evidence,'run',source_record_id=plan['id'])
    assert original['admission']['eligible']
    new_owner=store.add('dossier',cid,task_id=task['id'],evidence_id=evidence['id'],generation=0,
        title='Explicit followup routing',observation_ids=[source['id']],origin_hypothesis_id=owner['id'])
    child,binding=bind_followup_owner(store,cid,task,evidence,'run',call,original,new_owner,plan['id'])
    assert child['test_design']['target_ref']==call['test_design']['target_ref']
    assert call['hypothesis_id']==owner['id']
    assert contract(child)['contract_id']!=contract(call)['contract_id']
    derived=question_engine.reserve(store,cid,task,question,child,evidence,'run',source_record_id=binding['id'])
    assert derived['admission']['eligible']
    assert store.get(original['id'])['scope']['logical_contract']['dossier_id']==owner['id']
    assert derived['scope']['logical_contract']['dossier_id']==new_owner['id']
    assert derived['scope']['logical_contract']['controller_owner_lineage']['parent_contract_id']==contract(call)['contract_id']
    assert not question_engine.reserve(store,cid,task,question,child,evidence,'run',source_record_id=plan['id'])['admission']['eligible']
    with pytest.raises(ValueError):store.update(binding['id'],proposal={})


def test_planner_can_own_only_the_exact_supplied_question(fixture):
    call=deepcopy(fixture[-1]);call.pop('hypothesis_id')
    call['test_design']['owner_ref']=object_ref(fixture[5])
    assert admission(fixture,call)['eligible']
    assert contract(call)['dossier_id']==fixture[5]['id']
    call['test_design']['owner_ref']=object_ref(fixture[4])
    assert admission(fixture,call)['reason']=='internal_reference_error'


def test_taskless_explicit_coverage_owner_is_not_a_dynamic_or_foreign_owner(fixture):
    store,cid,evidence,task,_,question,source,_,call=fixture
    coverage=store.add('hypothesis',cid,evidence_id=evidence['id'],text='Coverage question',contract='coverage-v1')
    manifest=build_manifest(store,cid,task,evidence,'run',owners=[coverage],questions=[question],observations=[source])
    proposal=deepcopy(call);proposal['hypothesis_id']=coverage['id'];proposal['test_design']['owner_ref']=object_ref(coverage)
    assert admission(fixture,proposal,manifest)['eligible']
    dynamic=store.add('hypothesis',cid,evidence_id=evidence['id'],text='Dynamic unknown owner',hypothesis_kind='dynamic')
    invalid=build_manifest(store,cid,task,evidence,'run',owners=[dynamic],questions=[question],observations=[source])
    assert not any(o['ref']['id']==dynamic['id'] for o in invalid['objects'])


def test_controller_reuse_binding_adds_no_body_or_independent_execution(fixture):
    store,cid,evidence,task,owner,question,source,manifest,call=fixture
    intent,physical,parent=job(fixture)
    origin=store.get(intent['scope']['test_contract_source_record_id'])
    proposal=deepcopy(call);proposal['test_design']['outcome_rules']['found']='Another scoped collection criterion'
    new_intent=question_engine.reserve(store,cid,task,question,proposal,evidence,'run',source_record_id=origin['id'])
    derived,binding=bind_existing_result(store,cid,task,evidence,'run',proposal,new_intent,physical,origin['id'],parent_contract=parent)
    adopted=question_engine.reserve(store,cid,task,question,derived,evidence,'run',source_record_id=binding['id'])
    assert adopted['admission']['eligible']
    assert derived['test_design']['uses_existing_result'] is True
    assert binding['test_contract_context']['objects']==manifest['objects']
    assert len(store.list('investigation_job',cid))==1
    with pytest.raises(ValueError):bind_existing_result(store,cid,task,evidence,'run',proposal,new_intent,physical,origin['id'],parent_contract={**parent,'contract_id':'0'*64})


def test_controller_source_challenge_remains_discovery_not_verification(fixture):
    store,cid,evidence,task,_,question,_,_,_=fixture
    for tool in ('read_file','search'):
        call,source=controller_collection(store,cid,task,evidence,'run',{'tool':tool,'path':'/fixture/config','query':'config'},question)
        intent=question_engine.reserve(store,cid,task,question,call,evidence,'run',source_record_id=source['id'])
        assert intent['admission']['eligible']
        assert intent['scope']['test_design']['purpose']=='discover'
        assert 'supports' not in intent['scope']['test_design']['allowed_outcomes']


def test_synthesis_followup_handoff_is_executable_without_silent_owner_change(fixture):
    from types import SimpleNamespace
    from workbench.case_synthesis import admit_checks
    store,cid,evidence,task,_,question,source,_,call=fixture
    hypothesis=store.add('hypothesis',cid,evidence_id=evidence['id'],text='Does this source have the configuration?',hypothesis_kind='coverage_domain')
    question=store.update(question['id'],source_ids=[hypothesis['id']])
    call=deepcopy(call);call['hypothesis_id']=hypothesis['id'];call['test_design']['owner_ref']=object_ref(hypothesis);call['test_design']['question_ref']=object_ref(question)
    context=build_manifest(store,cid,task,evidence,'run',owners=[hypothesis],questions=[question],observations=[source])
    plan=store.add('synthesis_input',cid,task_id=task['id'],evidence_id=evidence['id'],generation=0,pack={'test_contract_context':context})
    env=store.add('observation',cid,evidence_id=evidence['id'],type='linux_environment',fields={'run_id':'run'})
    controller=SimpleNamespace(store=store,active_observations=lambda _: [source,env])
    did=admit_checks(controller,cid,evidence,task,hypothesis,[call],[source['id']],[],source_record_id=plan['id'])
    assert did
    physical=store.list('investigation_job',cid)[0]
    assert physical['contracts'][0]['dossier_id']==did
    assert physical['contracts'][0]['controller_owner_lineage']['from_ref']==object_ref(hypothesis)
    original=[i for i in store.list('test_intent',cid) if i['scope']['logical_contract']['dossier_id']==hypothesis['id']]
    assert len(original)==1 and original[0]['status']=='reserved'
