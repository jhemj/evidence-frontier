from copy import deepcopy

from workbench.review_progress import project
from workbench.review_queue import schedule


def fixture():
    finding={'dossier_id':'old','title':'Observed configuration','judgment':'확인','reason':'Exact recorded setting','observation_ids':['o']}
    old={'id':'old','evidence_id':'e','task_id':'t','group_key':'g','generation':0,'status':'reviewed',
         'finding':finding,'created_at':'2026-01-01T00:00:00+00:00','receipt_id':'r'}
    new={**old,'id':'new','generation':1,'status':'deferred','finding':None}
    return {'evidence':[{'id':'e','connected':True}],'task':[{'id':'t','retry_generation':1}],
            'observation':[{'id':'o','evidence_id':'e'}],'dossier':[old,new]}


def test_prior_review_is_visible_without_becoming_current_confirmation():
    data=fixture();before=deepcopy(data);result=project(data)
    assert (result['total'],result['ever_reviewed'],result['current_reviewed'],result['never_reviewed'])==(1,1,0,0)
    history=result['historical_assessments'][0]
    assert history['freshness']=='historical_not_revalidated'
    assert history['receipt_id']=='r' and history['dossier_id']=='old'
    assert data==before


def test_disconnected_superseded_or_missing_sources_do_not_count():
    data=fixture();data['evidence'][0]['connected']=False
    assert project(data)['total']==0
    data=fixture();data['task'][0]['superseded']=True
    assert project(data)['total']==0
    data=fixture();data['observation']=[]
    assert project(data)['ever_reviewed']==0 and project(data)['never_reviewed']==1


def test_old_different_scope_and_future_reviews_cannot_fill_current_gap():
    data=fixture();data['dossier'][0]['task_id']='superseded-task'
    assert project(data)['ever_reviewed']==0
    data=fixture();data['dossier'][0]['group_key']='no-longer-present'
    assert project(data)['ever_reviewed']==0
    data=fixture();data['dossier'][0]['generation']=2
    assert project(data)['ever_reviewed']==0


def test_repeated_review_counts_once_and_selects_latest():
    data=fixture();data['dossier'][1]['status']='reviewed';data['dossier'][1]['finding']=data['dossier'][0]['finding']
    result=project(data)
    assert result['ever_reviewed']==result['current_reviewed']==1
    assert result['historical_only']==0


def test_failed_new_review_never_restores_old_success_as_current():
    data=fixture();data['dossier'][1]['status']='model_failed'
    data['receipt']=[{'id':'r','created_at':'2026-01-01T01:02:03+00:00'}]
    result=project(data)
    assert result['current_reviewed']==0 and result['historical_only']==1
    assert result['historical_assessments'][0]['reviewed_at']=='2026-01-01T01:02:03+00:00'
    assert data['dossier'][1]['status']=='model_failed'


def test_other_evidence_source_cannot_validate_historical_review():
    data=fixture();data['evidence'].append({'id':'other','connected':True})
    data['observation'][0]['evidence_id']='other'
    assert project(data)['ever_reviewed']==0


def test_never_reviewed_precedes_old_deferred_but_already_reviewed():
    old={'id':'old','baseline':False,'group_key':'a','review_family':'execution','review_priority':0,
         'deferred_since':'2025-01-01','previously_reviewed':True}
    unseen={**old,'id':'unseen','group_key':'b','review_priority':9,'deferred_since':'2026-01-01','previously_reviewed':False}
    assert schedule([old,unseen],limit=1)[0]['id']=='unseen'
