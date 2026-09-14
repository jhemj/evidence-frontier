"""Citation diagnostics never repair or adopt a rejected interpretation."""
from collections import Counter

CONTRACT_VERSION='dossier-citations-3'


def errors(output, dossier_ids, observation_ids, dossier_allowed=None, observations=None):
    wanted=set(dossier_ids);allowed=set(observation_ids);issues=[]
    counts=Counter(f['dossier_id'] for f in output['findings'])
    if set(counts)!=wanted or any(n!=1 for n in counts.values()):
        issues.append({'code':'dossier_membership','missing':sorted(wanted-counts.keys()),
                       'unexpected':sorted(counts.keys()-wanted),
                       'duplicates':sorted(k for k,n in counts.items() if n>1)})
    for f in output['findings']:
        if f['judgment']!='미확인' and 'stages' in f and not f['stages']:
            issues.append({'code':'missing_claim_stages','dossier_id':f['dossier_id']})
        for stage in f.get('stages',[]):
            refs=set(stage['observation_ids'])
            permitted=set(dossier_allowed.get(f['dossier_id'],[])) if dossier_allowed is not None else allowed
            if refs-permitted:issues.append({'code':'stage_citation_scope','dossier_id':f['dossier_id']})
            if stage['judgment']!='미확인' and not refs:issues.append({'code':'stage_missing_support','dossier_id':f['dossier_id']})
            if observations and stage['judgment']=='확인' and stage['stage'] in ('execution','connection','objective'):
                sources=[observations[oid] for oid in refs if oid in observations]
                static_types={'linux_configuration','linux_persistence','linux_path_match','linux_binary','filesystem_time','linux_account',
                    'linux_tool_result','linux_literal_match','linux_detection','linux_inspection_result','linux_ssh_trust','linux_persistence_link'}
                if sources and all(o['type'] in static_types for o in sources):
                    issues.append({'code':'static_facts_not_behavior','dossier_id':f['dossier_id'],'stage':stage['stage']})
        unknown=sorted(set(f['observation_ids'])-allowed)
        if unknown:issues.append({'code':'unknown_observation','dossier_id':f['dossier_id'],'ids':unknown})
        if dossier_allowed is not None:
            unrelated=sorted((set(f['observation_ids'])&allowed)-set(dossier_allowed.get(f['dossier_id'],[])))
            if unrelated:issues.append({'code':'unrelated_observation','dossier_id':f['dossier_id'],'ids':unrelated})
        if f['judgment']!='미확인' and not f['observation_ids']:
            issues.append({'code':'missing_support','dossier_id':f['dossier_id']})
    for check in output.get('next_checks',[]):
        if check['hypothesis_id'] not in wanted:
            issues.append({'code':'unknown_check_dossier','id':check['hypothesis_id']})
    return issues


def check_errors(output, checks, allowed_by_dossier):
    issues=[];by_id={c['id']:c for c in checks};seen=set()
    for assessment in output.get('check_assessments',[]):
        cid=assessment['check_id'];check=by_id.get(cid)
        if not check or cid in seen:
            issues.append({'code':'invalid_check_assessment','id':cid});continue
        seen.add(cid)
        refs=set(assessment['observation_ids'])
        if refs-set(check.get('observation_ids',[])):
            issues.append({'code':'check_citation_scope','id':cid})
        if assessment['outcome']!='inconclusive' and not refs:
            issues.append({'code':'check_missing_support','id':cid})
        if assessment['outcome']=='refutes' and check.get('status') not in ('covered','covered_zero') and not refs:
            issues.append({'code':'partial_search_not_refutation','id':cid})
    # Missing assessment is explicitly unknown, never silently a successful
    # falsification. Preserve this in the accepted, fully cited result.
    for cid in by_id.keys()-seen:
        output.setdefault('check_assessments',[]).append({'check_id':cid,'outcome':'inconclusive',
            'reason':'실제 검사 결과에 대한 판별 평가가 제공되지 않았습니다.','observation_ids':[]})
    return issues
