from workbench.evaluation import evaluate

CRITERIA={'positive_rules':['bad'],'normal_paths':['/normal'],'forbidden_confirmed_stages':['objective'],'denominators':{}}


def test_unscanned_normal_is_not_true_negative_and_missed_positive_stays_denominator():
    doc={'observations':[],'evaluation_scanned_paths':[],'dossiers':[],'judgments':[]}
    result=evaluate(doc,CRITERIA)
    assert result['pattern_recall']==0 and result['normal_false_positive_rate'] is None
    assert result['normal_unprocessed']==1 and result['normal_true_negatives']==0


def test_mixed_control_recall_false_positive_and_failed_review_are_separate():
    doc={'observations':[{'id':'a','type':'linux_detection','fields':{'rule_id':'bad','path':'/normal'}}],
        'evaluation_scanned_paths':['/normal'],'dossier_batches':[{'task_id':'task','dossier_ids':['d']}],
        'dossiers':[{'task_id':'task','status':'model_failed'}],'judgments':[]}
    result=evaluate(doc,CRITERIA)
    assert result['pattern_recall']==1 and result['normal_false_positive_rate']==1
    assert result['review_rate']==0 and result['unreviewed_or_failed']==1
