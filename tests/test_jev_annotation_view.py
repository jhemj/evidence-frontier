from copy import deepcopy
import pytest
from test_observer_view import records,make
from workbench.observer_view import digest,validate


def annotated():
    rows=records();rows[0]['epoch_id']='epoch'
    dossier={'id':'D1','kind':'dossier','case_id':'CASE-demo','task_id':'T1','evidence_id':'E1',
        'generation':0,'status':'reviewed','receipt_id':'R1','title':'fixture record',
        'observation_ids':['O1'],'finding':{'observation_ids':['O1'],'literal_statement':'fixture record'}}
    material={k:dossier.get(k) for k in ('id','task_id','evidence_id','generation','receipt_id','finding','title','observation_ids')}
    rows += [dossier,{'id':'JEV1','kind':'jev_annotation','case_id':'CASE-demo','subject_id':'D1',
        'status':'annotated','epoch_id':'epoch','source_binding':None,'evidence_version':digest(rows[1]),
        'material':material,'subject_digest':digest(material),'category':'activity_record','label':'활동 기록',
        'refs':[{'id':'O1','version':digest(rows[3])}]}]
    return rows


def test_advisory_label_has_no_probability_or_incident_authority():
    view=validate(make(annotated()))
    assert len(view['jev_annotations'])==1
    annotation=view['jev_annotations'][0]
    assert annotation['probabilities'] is None and annotation['policy_applied'] is False
    assert view['summary']['intrusion']['verdict']=='undetermined'


@pytest.mark.parametrize('changed',['source','title','evidence','epoch','generation','disconnect'])
def test_advisory_display_rejects_changed_scope(changed):
    rows=deepcopy(annotated())
    if changed=='source':rows[3]['fields']['excerpt']='new original'
    elif changed=='title':rows[-2]['title']='changed'
    elif changed=='evidence':rows[1]['signature']='changed'
    elif changed=='epoch':rows[0]['epoch_id']='new'
    elif changed=='generation':rows[2]['retry_generation']=1
    else:rows[1]['connected']=False
    assert make(rows)['jev_annotations']==[]
