"""Predeclared control metrics; never infer real-case detection rate from fixtures."""
def evaluate(document, criteria):
    observations={o['id']:o for o in document['observations']}
    detected=[o for o in observations.values() if o['type']=='linux_detection']
    actual={o['fields']['rule_id'] for o in detected};expected=set(criteria['positive_rules'])
    positives=len(expected & actual)
    normal=set(criteria['normal_paths'])
    normal_fp={o['fields']['path'] for o in detected if o['fields']['path'] in normal}
    # Collection records alone are not proof a negative control was scanned.
    scanned=set(document.get('evaluation_scanned_paths',[]))
    normal_scanned=normal & scanned
    normal_tp=normal_fp & normal_scanned
    normal_tn=normal_scanned-normal_fp
    batches=document.get('dossier_batches',[])
    current_ids={did for b in batches for did in b['dossier_ids']}
    dossiers=document.get('dossiers',[])
    # Include deferred dossiers in the current generation, not only admissions.
    generations={(b['task_id'],b.get('generation',0)) for b in batches}
    current=[d for d in dossiers if (d['task_id'],d.get('generation',0)) in generations]
    reviewed=[d for d in current if d['status']=='reviewed' and d.get('finding')]
    findings=[f for j in document.get('judgments',[]) for f in j['findings']]
    unknown=sum(len(set(f.get('observation_ids',[]))-observations.keys()) for f in findings)
    violations=[]
    for f in findings:
        for stage in f.get('stages',[]):
            unknown+=len(set(stage['observation_ids'])-observations.keys())
            if stage['judgment']=='확인' and stage['stage'] in criteria['forbidden_confirmed_stages']:
                violations.append({'title':f['title'],'stage':stage['stage'],'statement':stage['statement']})
    return {'pattern_recall':positives/len(expected) if expected else None,
        'expected_rule_families':len(expected),'detected_rule_families':positives,
        'normal_scanned':len(normal_scanned),'normal_unprocessed':len(normal-normal_scanned),
        'normal_false_positives':len(normal_fp),'normal_false_positive_rate':len(normal_tp)/len(normal_scanned) if normal_scanned else None,
        'normal_true_negatives':len(normal_tn),'reviewed':len(reviewed),'review_total':len(current),
        'review_rate':len(reviewed)/len(current) if current else None,
        'unreviewed_or_failed':len(current)-len(reviewed),'unknown_citations':unknown,
        'stage_scope_violations':violations,'denominators':criteria['denominators'],
        'real_case_detection_rate':None,'semantic_grading_scope':'Structural source/forbidden-stage checks only; full semantic correctness is not established'}
