from copy import deepcopy

import pytest

from test_dossiers import setup
from workbench import objection_ledger as ledger
from workbench.evidence_spans import manifest
from workbench.investigation import compact_observation
from workbench.models import JudgmentReport
from workbench.review_context import fit_metadata_only, InputBudgetError
from workbench.review_paging import build_pages, ProjectionTooLarge


def test_long_tail_and_lists_survive_judgment_source_projection(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    row=deepcopy(o)
    row['fields'].update(excerpt='normal header\n'+'abcdefghij'*800+'\nDECISIVE CONTRARY CONDITION',
                         members=list(range(50)))
    exact=compact_observation(row,preserve_content=True)
    assert exact['fields']['excerpt']==row['fields']['excerpt']
    assert exact['fields']['members']==list(range(50))
    pack={'observations':[exact],'required_dossiers':[], 'executed_checks':[]}
    fitted=deepcopy(pack);fit_metadata_only(fitted)
    assert fitted['observations'][0]['fields']['excerpt'].endswith('DECISIVE CONTRARY CONDITION')
    with pytest.raises(InputBudgetError):fit_metadata_only(deepcopy(pack),2000)
    with pytest.raises(ProjectionTooLarge):build_pages(pack,maximum=2000,fit_fn=fit_metadata_only)
    assert row['fields']['excerpt'].endswith('DECISIVE CONTRARY CONDITION')


def test_manifest_is_exact_field_utf8_not_guessed_file_range():
    pack={'observations':[{'id':'o','source_location':'file:42','fields':{
        'excerpt':'한글\n\ufffd','byte_offset':42,'byte_length':100,'excerpt_truncated':True}}]}
    entry=next(s for s in manifest(pack)['spans'] if s['field_pointer']=='/fields/excerpt')
    assert entry['byte_start']==0 and entry['byte_end']==len('한글\n\ufffd'.encode())
    assert entry['source_locator']['byte_offset']==42
    assert entry['coordinate_basis']=='canonical_field_utf8'
    assert 'source_byte_offset' not in entry
    assert entry['extent']=='partial_field' and entry['source_completeness']=='partial'


def test_objections_survive_silence_restart_and_require_explicit_source_resolution(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    pack={'observations':[compact_observation(o,preserve_content=True)]}
    output={'findings':[{'dossier_id':'d','title':'Target claim', 'counterevidence_ids':[o['id']]}]}
    ids=ledger.capture(c.store,cid,t,output,'receipt-original',manifest(pack))
    assert ledger.capture(c.store,cid,t,output,'receipt-repeat',manifest(pack))==ids
    opened=ledger.current(c.store,cid,t,['d'])
    assert len(opened)==1 and opened[0]['span_ids']
    assert ledger.current(c.store,cid,{**t,'retry_generation':1},['d'])==[]
    assert ledger.current(c.store,cid,t,['other'])==[]
    final={'findings':[{'dossier_id':'d','title':'Narrow fact','judgment':'확인'}]}
    qualified=ledger.qualify(final,opened)
    assert qualified['findings'][0]['judgment']=='확인'
    assert qualified['findings'][0]['publication_status']=='qualified_open_objections'
    ledger.resolve(c.store,final,'receipt-silent')
    assert ledger.current(c.store,cid,t,['d'])
    answer={'objection_assessments':[{'objection_id':ids[0],'outcome':'resolved',
        'reason':'Positive retained evidence distinguishes scope.',
        'basis':'positive_evidence','observation_ids':[o['id']]}]}
    assert ledger.errors(answer,ledger.view(opened),{'d':[]})
    assert ledger.errors(answer,ledger.view(opened),{'d':[o['id']]},intermediate=True)
    assert not ledger.errors(answer,ledger.view(opened),{'d':[o['id']]})
    ledger.resolve(c.store,answer,'receipt-final')
    assert not ledger.current(c.store,cid,t,['d'])
    historical=ledger.current(c.store,cid,t,['d'],only_open=False)[0]
    assert historical['assessment_receipt_id']=='receipt-original'
    assert historical['decision_revision']
    decision=c.store.get(historical['decision_revision'])
    assert decision['original_observation_ids']==[o['id']]
    assert decision['original_span_ids']==historical['span_ids']
    with pytest.raises(ValueError,match='불변'):c.store.update(decision['id'],reason='overwrite')


def test_absence_and_missing_resolution_source_cannot_close_objection():
    objections=[{'id':'obj','dossier_id':'d','observation_ids':['counter']}]
    answer={'objection_assessments':[{'objection_id':'obj','outcome':'resolved',
        'basis':'absence','observation_ids':[]}]}
    assert any(i['code']=='objection_resolution_requires_positive_source'
               for i in ledger.errors(answer,objections,{'d':['counter']}))


def test_top_level_synthesis_refutation_is_persistent_even_without_duplicate_finding_citation(tmp_path):
    c,cid,e,t,o=setup(tmp_path)
    output={'findings':[{'dossier_id':'question','title':'Narrow question','reason':'An incompatible source remains.',
        'counterevidence_ids':[]}],'refuting_evidence_ids':[o['id']]}
    opened=ledger.capture(c.store,cid,t,output,'synthesis-page',manifest({'observations':[o]}))
    assert len(opened)==1
    assert c.store.get(opened[0])['observation_ids']==[o['id']]
    assert c.store.get(opened[0])['status']=='open'


def test_output_schema_keeps_legacy_and_new_dispositions_compatible():
    payload={'summary':'scope','findings':[{'dossier_id':'d','title':'fact','reason':'scope','judgment':'미확인'}]}
    assert JudgmentReport.model_validate(payload).objection_assessments==[]
    payload['objection_assessments']=[{'objection_id':'obj','outcome':'open','reason':'Pending test.'}]
    assert JudgmentReport.model_validate(payload).objection_assessments[0].outcome=='open'


def test_case_synthesis_cannot_silently_settle_a_related_dossier_objection(tmp_path,monkeypatch):
    from workbench.case_synthesis import tick
    c,cid,e,t,o=setup(tmp_path)
    counter=c.store.add('observation',cid,evidence_id=e['id'],type='linux_configuration',
        timestamp=None,source_location='/counter',fields={'excerpt':'Recorded competing explanation.'})
    h=c.store.add('hypothesis',cid,evidence_id=e['id'],contract='linux-v1',number=1,
        text='Incident question',expected_source_types=[o['type']])
    dossier=c.store.add('dossier',cid,task_id=t['id'],evidence_id=e['id'],generation=0,
        observation_ids=[o['id']],status='reviewed',finding={'title':'Narrow fact','observation_ids':[o['id']]})
    dossiers=[dossier]
    ledger.capture(c.store,cid,t,{'findings':[{'dossier_id':dossier['id'],'title':'Original claim',
        'counterevidence_ids':[counter['id']]}]},'prior-assessment',manifest({'observations':[counter]}))
    def model(config,question,pack,**kw):
        assert counter['id'] in pack['allowed_observation_ids']
        assert pack['open_objections'][0]['origin_dossier_id']==dossier['id']
        return {'summary':'Limited synthesis','findings':[{'dossier_id':h['id'],'title':'Open question',
            'reason':'Sources do not settle this question.','judgment':'미확인','observation_ids':[o['id']]}]},{}
    monkeypatch.setattr('workbench.case_synthesis.consult',model)
    assert tick(c,cid,e,t,dossiers) is False
    synthesis=c.store.list('case_synthesis',cid)[0]
    assert synthesis['status']=='objections_open'
    assert synthesis['finding']['open_objections'][0]['observation_ids']==[counter['id']]
    assert ledger.current(c.store,cid,t,[dossier['id']])
