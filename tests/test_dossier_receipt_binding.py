"""Native success/adoption receipt contracts; all transport is local stubs."""
from copy import deepcopy
import hashlib
import json

import httpx
import pytest

from test_dossiers import setup
from workbench.dossiers import finish, model_receipt_fields, ReceiptBindingError
from workbench.request_compiler import compile_request
from workbench.request_lifecycle import reference


def bindings():
    compiled=compile_request({'protocol':'ollama','model':'fixture-model',
        'base_url':'http://127.0.0.1:11434'},'fixture question',{'observations':[]},'analyst')
    prepared={'id':'REVIEW_INPUT-fixture','kind':'review_input','case_id':'CASE-fixture',
        'created_at':'fixture-time','input_sha256':'a'*64,'compiled_request':compiled.identity,'generation':0}
    context={k:deepcopy(prepared[k]) for k in ('input_sha256','compiled_request','generation')}
    context['input_record_id']=prepared['id']
    receipt={'compiled_request':compiled.identity,'model':'fixture-model','output':{'summary':'fixture'},
        'request_attempt_id':'MODEL_ATTEMPT-fixture',
        'adapter_transport':{'request_body_sha256':'b'*64,'delivery_state':'response_received'}}
    fixed={'task_id':'TASK-fixture','evidence_id':'EVIDENCE-fixture','receipt_type':'dossier_model','batch_id':'BATCH-fixture'}
    return compiled,prepared,context,receipt,fixed


def test_identical_shared_identity_is_coalesced_with_both_origins_and_separate_wire():
    _,prepared,context,receipt,fixed=bindings()
    original=deepcopy((prepared,context,receipt,fixed))
    values=model_receipt_fields(context,receipt,fixed,prepared)
    assert values['compiled_request']==context['compiled_request']==receipt['compiled_request']
    provenance=values['compiled_request_provenance']
    assert provenance['prepared_input_ref']==reference(prepared)
    assert provenance['provider_field_present'] and provenance['provider_native_identity_equal'] is True
    assert values['adapter_transport']==receipt['adapter_transport']
    assert values['adapter_transport']['request_body_sha256']!=values['compiled_request']['request_body_sha256']
    assert 'wire identity' in provenance['scope']
    assert (prepared,context,receipt,fixed)==original


@pytest.mark.parametrize('key',['request_body_sha256','messages_sha256','output_schema_sha256',
    'template_sha256','protocol','request_utf8_bytes','count_basis','scope'])
def test_conflicting_compiled_identity_cannot_overwrite_prepared_input(key):
    _,prepared,context,receipt,fixed=bindings()
    receipt['compiled_request'][key]='other-value'
    with pytest.raises(ReceiptBindingError) as error:
        model_receipt_fields(context,receipt,fixed,prepared)
    assert error.value.category=='model_receipt_binding_conflict'
    assert error.value.fields==['compiled_request']


@pytest.mark.parametrize('key',['input_sha256','generation','task_id','batch_id','receipt_type',
    'case_id','kind','id','created_at','compiled_request_provenance'])
def test_other_shared_or_store_identity_conflicts_are_never_blanket_merged(key):
    _,prepared,context,receipt,fixed=bindings()
    receipt[key]='wrong-value'
    with pytest.raises(ReceiptBindingError) as error:
        model_receipt_fields(context,receipt,fixed,prepared)
    assert key in error.value.fields


def test_source_record_itself_must_match_controller_binding():
    _,prepared,context,receipt,fixed=bindings()
    prepared['compiled_request']['messages_sha256']='c'*64
    with pytest.raises(ReceiptBindingError,match='compiled_request'):
        model_receipt_fields(context,receipt,fixed,prepared)


def test_legacy_missing_provider_identity_is_unknown_not_observed_wire_proof():
    _,prepared,context,receipt,fixed=bindings()
    receipt.pop('compiled_request')
    values=model_receipt_fields(context,receipt,fixed,prepared)
    assert values['compiled_request']==context['compiled_request']
    assert values['compiled_request_provenance']['provider_field_present'] is False
    assert values['compiled_request_provenance']['provider_native_identity_equal'] is None


@pytest.mark.parametrize('paged',[False,True])
def test_actual_native_provider_success_is_saved_and_adopted_without_duplicate_kwargs(tmp_path,monkeypatch,paged):
    controller,cid,evidence,task,_=setup(tmp_path)
    for name in ('FRONTIER_MODEL_DIGEST','MODEL_RELAY_URL','MODEL_API_KEY','MODEL_SECONDARY_API_KEY',
            'FRONTIER_RESOURCE_BROKER','FRONTIER_RESOURCE_GROUP'):
        monkeypatch.delenv(name,raising=False)
    if paged:
        # Force a lossless source-page boundary once, not budget acceptance.
        from workbench.review_context import fit_metadata_only,InputBudgetError
        first=[True]
        def need_page(*args,**kwargs):
            if first[0]:
                first[0]=False
                raise InputBudgetError('Fixture asks for a source page')
            return fit_metadata_only(*args,**kwargs)
        monkeypatch.setattr('workbench.review_context.fit_metadata_only',need_page)
    from workbench.provider import Provider
    original_init=Provider.__init__
    dispatched=[]
    def initialize(self,config):
        original_init(self,config)
        self.client.close()
        def transport(request):
            compiled=self._active_compiled
            assert request.url.path==compiled.path
            assert request.content==compiled.payload_json.encode()
            dispatched.append(hashlib.sha256(request.content).hexdigest())
            from workbench.review_stream import resolved
            # Decode the immutable compiled pack's reference projection via its
            # supplied aliases, not invented evidence IDs.
            refs=compiled.references.encode_map
            inputs=controller.store.list('review_input',cid)
            shown=resolved(inputs[-1]['pack'])
            findings=[{'dossier_id':refs[d['id']],'title':d['title'],'judgment':'미확인',
                'reason':'Only the retained scoped record was reviewed; execution remains unknown.',
                'observation_ids':[refs[i] for i in d['observation_ids'] if i in refs],
                'alternatives':['Authorized operation remains possible.'],'remaining_checks':[]}
                for d in shown['required_dossiers']]
            output={'summary':'Scoped fixture review','findings':findings,'next_checks':[],
                'check_assessments':[],'objection_assessments':[]}
            if compiled.working:output['source_selections']=[]
            return httpx.Response(200,json={'done_reason':'stop','message':{'content':json.dumps(output)},
                'prompt_eval_count':100,'eval_count':40})
        self.client=httpx.Client(transport=httpx.MockTransport(transport))
    monkeypatch.setattr(Provider,'__init__',initialize)
    finish(controller,cid,evidence,task)
    kind='dossier_page_model' if paged else 'dossier_model'
    receipts=[r for r in controller.store.list('receipt',cid) if r.get('receipt_type')==kind]
    assert len(receipts)==1 and len(dispatched)==1
    stored=receipts[0];prepared=controller.store.get(stored['input_record_id'])
    assert stored['compiled_request']==prepared['compiled_request']
    assert stored['compiled_request']['request_body_sha256']==dispatched[0]
    assert stored['compiled_request_provenance']['prepared_input_ref']==reference(prepared)
    assert stored['compiled_request_provenance']['provider_native_identity_equal'] is True
    assert not [r for r in controller.store.list('receipt',cid) if r.get('receipt_type')=='dossier_model_error']
    lifecycle=controller.store.list('request_lifecycle',cid)
    accepted='page_accepted' if paged else 'accepted'
    assert any(r['phase']==accepted for r in lifecycle)
    if paged:
        assert any(p['status']=='reviewed' for p in controller.store.list('review_page',cid))
    else:
        assert controller.store.list('dossier_batch',cid)[0]['output']['findings']


@pytest.mark.parametrize('paged',[False,True])
def test_conflicting_provider_success_receipt_is_diagnosed_without_adoption(tmp_path,monkeypatch,paged):
    controller,cid,evidence,task,_=setup(tmp_path)
    if paged:
        from workbench.review_context import fit_metadata_only,InputBudgetError
        first=[True]
        def need_page(*args,**kwargs):
            if first[0]:
                first[0]=False
                raise InputBudgetError('Fixture asks for a source page')
            return fit_metadata_only(*args,**kwargs)
        monkeypatch.setattr('workbench.review_context.fit_metadata_only',need_page)
    def conflict(config,question,pack,role,*,compiled_request,**kwargs):
        output={'summary':'Scoped fixture','findings':[{'dossier_id':d['id'],'title':d['title'],
            'judgment':'미확인','reason':'No execution conclusion','observation_ids':d['observation_ids'],
            'alternatives':[],'remaining_checks':[]} for d in pack['required_dossiers']], 'next_checks':[]}
        identity=deepcopy(compiled_request.identity);identity['request_body_sha256']='f'*64
        return output,{'compiled_request':identity,'output':output}
    monkeypatch.setattr('workbench.dossiers.consult',conflict)
    finish(controller,cid,evidence,task)
    assert not [r for r in controller.store.list('receipt',cid) if r.get('receipt_type') in ('dossier_model','dossier_page_model','dossier_model_partial')]
    diagnostic=controller.store.list('review_diagnostic',cid)[-1]
    assert diagnostic['failure_category']=='model_receipt_binding_conflict'
    assert diagnostic['compiled_request']['request_body_sha256']!='f'*64
    assert diagnostic['model_metadata']['compiled_request']['request_body_sha256']=='f'*64
    assert diagnostic['validation_errors'][0]['code']=='model_receipt_binding_conflict'
    assert not any(r['phase'] in ('accepted','page_accepted','partial_accepted')
        for r in controller.store.list('request_lifecycle',cid))


def test_compilation_conflict_is_global_even_for_valid_independent_final_siblings(tmp_path,monkeypatch):
    controller,cid,evidence,task,observation=setup(tmp_path)
    controller.store.add('observation',cid,evidence_id=evidence['id'],type='linux_detection',
        timestamp=None,source_location='fixture:/etc/cron.d/b',
        fields={**observation['fields'],'path':'/etc/cron.d/b'})
    seen=[]
    def propose(config,question,pack,role,*,compiled_request,**kwargs):
        seen.append(deepcopy(pack))
        output={'summary':'Scoped fixture','findings':[{'dossier_id':d['id'],'title':d['title'],
            'judgment':'미확인','reason':'No execution conclusion','observation_ids':d['observation_ids'],
            'alternatives':[],'remaining_checks':[]} for d in pack['required_dossiers']], 'next_checks':[]}
        identity=deepcopy(compiled_request.identity)
        if len(seen)>1:identity['request_body_sha256']='f'*64
        return output,{'compiled_request':identity,'output':output}
    monkeypatch.setattr('workbench.dossiers.consult',propose)
    finish(controller,cid,evidence,task)
    finish(controller,cid,evidence,task)
    assert len(seen)==2 and len(seen[-1]['required_dossiers'])==2
    failed_input=controller.store.list('review_input',cid)[-1]
    batch=controller.store.get(failed_input['batch_id'])
    assert batch['round']==1  # This would otherwise permit independent subset isolation.
    assert not [r for r in controller.store.list('receipt',cid)
        if r.get('input_record_id')==failed_input['id'] and r.get('receipt_type') in
        ('dossier_model','dossier_page_model','dossier_model_partial')]
    assert not any(d.get('partial_adoption_from') for d in controller.store.list('dossier',cid))
    failure=controller.store.list('review_diagnostic',cid)[-1]
    assert failure['validation_errors']==[{'code':'model_receipt_binding_conflict',
        'conflicting_fields':['compiled_request']}]
    assert not any(r['input_record_id']==failed_input['id'] and r['phase'] in
        ('accepted','page_accepted','partial_accepted') for r in controller.store.list('request_lifecycle',cid))
