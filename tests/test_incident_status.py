from copy import deepcopy
import pytest
from workbench.incident_status import assessment_errors, project
from workbench.case_synthesis import source_revision
from workbench.models import IncidentAssessment


def document(verdict='confirmed'):
    observation={'id':'o','evidence_id':'e','type':'synthetic','fields':{'path':'/fixture'},'source_location':'fixture'}
    a={'verdict':verdict,'scope':'권한 없는 실행 여부','rationale':'합성 근거 대조 · 실제 증거 아님',
       'supporting_evidence_ids':['o'] if verdict!='refuted' else [],'refuting_evidence_ids':['o'] if verdict=='refuted' else []}
    row={'id':'s','task_id':'t','evidence_id':'e','generation':0,'hypothesis_id':'h','status':'reviewed',
         'finding':{'judgment':'확인'},'incident_assessment':a,
         'supporting_evidence_ids':a['supporting_evidence_ids'],'refuting_evidence_ids':a['refuting_evidence_ids'],
         'source_ids':['o'],'source_revision':source_revision([observation])}
    return {'evidence':[{'id':'e'}],'task':[{'id':'t','retry_generation':0}],'observation':[observation],'case_synthesis':[row]}


def test_only_explicit_separate_intrusion_assessment_can_raise_indicator():
    d=document();assert project(d)['verdict']=='confirmed'
    d['case_synthesis'][0].pop('incident_assessment')
    d['triage']={'cards':[{'semantic_judgment':'확인','relevance_score':100}]*100}
    assert project(d)['verdict']=='undetermined'
    assert project(d)['pending']==1


def test_new_reviewed_counterinterpretation_invalidates_live_incident_board():
    from workbench.case_synthesis import review_revision
    d=document();r=d['case_synthesis'][0]
    dossier={'id':'d','task_id':'t','generation':0,'status':'reviewed','observation_ids':['o'],'finding':{'reason':'before'}}
    d['dossier']=[dossier];r['review_revision']=review_revision([dossier],['o'])
    assert project(d)['verdict']=='confirmed'
    dossier['finding']['reason']='new contrary interpretation'
    assert project(d)['verdict']=='undetermined'


@pytest.mark.parametrize('mutation', ['disconnect','retry','supersede','source_change','new_related_source','open_objection','followup','failed'])
def test_unresolved_or_stale_scope_does_not_keep_positive_verdict(mutation):
    d=document();r=d['case_synthesis'][0]
    if mutation=='disconnect':d['evidence'][0]['connected']=False
    elif mutation=='retry':d['task'][0]['retry_generation']=1
    elif mutation=='supersede':d['task'][0]['superseded']=True
    elif mutation=='source_change':d['observation'][0]['fields']['path']='/changed'
    elif mutation=='new_related_source':d['observation'].append({**d['observation'][0],'id':'o2'})
    elif mutation=='open_objection':r['finding']['open_objections']=[{'id':'unresolved'}]
    elif mutation=='followup':r['followup_dossier_id']='new-check'
    elif mutation=='failed':r['status']='model_failed'
    assert project(d)['verdict']=='undetermined'


def test_latest_question_revision_can_lower_verdict_but_never_certify_whole_case_clean():
    d=document();new=deepcopy(document('refuted')['case_synthesis'][0]);new['id']='new'
    d['case_synthesis'].append(new)
    result=project(d)
    assert result['verdict']=='undetermined'
    assert result['counts']['refuted']==1 and result['counts']['confirmed']==0
    assert len(result['assessments'])==1


def test_intrusion_contract_demands_role_bound_positive_evidence():
    row=document()['case_synthesis'][0];output={**row,'findings':[row['finding']]}
    assert not assessment_errors(output)
    output['incident_assessment']['supporting_evidence_ids']=[]
    assert assessment_errors(output)[0]['code']=='incident_assessment_positive_basis_required'
    output['incident_assessment']['supporting_evidence_ids']=['foreign']
    assert assessment_errors(output)[0]['code']=='incident_assessment_citation_scope'
    output['incident_assessment']['supporting_evidence_ids']=['o'];output['findings'][0]['judgment']='유력'
    assert assessment_errors(output)[0]['code']=='incident_confirmation_not_resolved'
    with pytest.raises(ValueError):IncidentAssessment.model_validate({**output['incident_assessment'],'probability':99})


@pytest.mark.parametrize('kind',['linux_configuration','linux_binary','linux_persistence','windows_task','windows_registry'])
def test_static_content_cannot_confirm_intrusion_even_with_confirmed_literal(kind):
    d=document();d['observation'][0]['type']=kind
    row=d['case_synthesis'][0]
    row['source_revision']=source_revision(d['observation'])
    issues=assessment_errors({**row,'findings':[row['finding']]},{'o':d['observation'][0]})
    assert any(i['code']=='incident_confirmation_static_only' for i in issues)
    assert project(d)['verdict']=='undetermined'
    row['incident_assessment']['verdict']='suspected'
    assert not assessment_errors({**row,'findings':[row['finding']]},{'o':d['observation'][0]})
