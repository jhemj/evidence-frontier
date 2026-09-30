"""Citation diagnostics never repair or adopt a rejected interpretation."""
from collections import Counter

CONTRACT_VERSION='dossier-citations-7'


def errors(output, dossier_ids, observation_ids, dossier_allowed=None, observations=None, require_literals=False,
           canonical_observations=None):
    wanted=set(dossier_ids);allowed=set(observation_ids);issues=[]
    counts=Counter(f['dossier_id'] for f in output['findings'])
    if set(counts)!=wanted or any(n!=1 for n in counts.values()):
        issues.append({'code':'dossier_membership','missing':sorted(wanted-counts.keys()),
                       'unexpected':sorted(counts.keys()-wanted),
                       'duplicates':sorted(k for k,n in counts.items() if n>1)})
    for f in output['findings']:
        if f.get('basis')=='absence' and f['judgment']!='미확인':
            issues.append({'code':'absence_preconditions_unverified','dossier_id':f['dossier_id']})
        permitted=set(dossier_allowed.get(f['dossier_id'],[])) if dossier_allowed is not None else allowed
        if observations is not None:
            available_spans={s['span_id'] for oid in permitted if oid in observations
                for s in ([observations[oid]['source_span']] if observations[oid].get('source_span') else
                    observations[oid].get('fields',{}).get('excerpt_spans',[])) if s.get('span_id')}
            if set(f.get('evidence_span_ids',[]))-available_spans:
                issues.append({'code':'evidence_span_citation_scope','dossier_id':f['dossier_id']})
        relevance=f.get('incident_relevance') or {}
        relevance_ids=set(relevance.get('observation_ids',[]))
        if relevance_ids-permitted:
            issues.append({'code':'relevance_citation_scope','dossier_id':f['dossier_id']})
        if relevance.get('level') in ('direct','indirect') and (not relevance_ids or not relevance.get('reason')):
            issues.append({'code':'relevance_missing_basis','dossier_id':f['dossier_id']})
        if observations is not None:
            from .semantic_contract import assertion_errors, source_facts
            issues.extend({**issue,'dossier_id':f['dossier_id']} for issue in assertion_errors(f,observations,permitted))
            if canonical_observations is not None:
                # The model's view may contain a selected excerpt instead of
                # the retained whole field. A field assertion must be true in
                # BOTH views before adoption; report export uses the latter.
                # Never change equals to contains or retrieve hidden literals.
                issues.extend({**issue,'dossier_id':f['dossier_id'],
                    'scope':'canonical_source',
                    'instruction':'Field pointers address retained observation fields, not a selected page. '
                        'equals requires the whole field. For a short exact passage already presented, '
                        'use contains; never quote text outside this presentation.'}
                    for issue in assertion_errors(f,canonical_observations,permitted))
            if require_literals and f['judgment']!='미확인' and not f.get('fact_assertions') and source_facts([observations[i] for i in f['observation_ids'] if i in observations]):
                issues.append({'code':'missing_literal_assertion','dossier_id':f['dossier_id']})
        if set(f.get('counterevidence_ids',[]))-permitted:
            issues.append({'code':'counterevidence_citation_scope','dossier_id':f['dossier_id']})
        if f['judgment']!='미확인' and 'stages' in f and not f['stages']:
            issues.append({'code':'missing_claim_stages','dossier_id':f['dossier_id']})
        for stage in f.get('stages',[]):
            refs=set(stage['observation_ids'])
            permitted=set(dossier_allowed.get(f['dossier_id'],[])) if dossier_allowed is not None else allowed
            if refs-permitted:issues.append({'code':'stage_citation_scope','dossier_id':f['dossier_id']})
            if stage['judgment']!='미확인' and not refs:issues.append({'code':'stage_missing_support','dossier_id':f['dossier_id']})
            if observations:
                from .evidence_semantics import stage_issues
                issues.extend({**issue,'dossier_id':f['dossier_id']} for issue in stage_issues(stage,[observations[oid] for oid in refs if oid in observations]))
            if observations and stage['judgment']=='확인' and stage['stage'] in ('execution','connection','objective'):
                sources=[observations[oid] for oid in refs if oid in observations]
                static_types={'linux_configuration','linux_persistence','linux_path_match','linux_binary','filesystem_time','linux_account',
                    'linux_tool_result','linux_literal_match','linux_detection','linux_inspection_result','linux_ssh_trust','linux_persistence_link','linux_baseline_sample'}
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
    for proposal in output.get('explanation_proposals',[]):
        if not proposal.get('trigger_observation_ids') or set(proposal['trigger_observation_ids'])-allowed:
            issues.append({'code':'explanation_trigger_scope'})
    return issues


def check_errors(output, checks, allowed_by_dossier):
    issues=[];by_key={};seen=set()
    for check in checks:
        for c in check.get('contracts') or [{'dossier_id':'','contract_id':''}]:
            by_key[(check['id'],c['dossier_id'],c['contract_id'])]=(check,c)
    for assessment in output.get('check_assessments',[]):
        key=(assessment['check_id'],assessment.get('dossier_id',''),assessment.get('contract_id',''))
        pair=by_key.get(key)
        if pair is None or key in seen:
            issues.append({'code':'invalid_check_assessment','id':key});continue
        seen.add(key);check,contract=pair
        if assessment.get('basis')=='absence' and assessment['outcome']!='inconclusive':
            issues.append({'code':'absence_preconditions_unverified','id':key})
        refs=set(assessment['observation_ids'])
        allowed=set(check.get('observation_ids',[]))
        if key[1]:allowed &= set(allowed_by_dossier.get(key[1],[]))
        if refs-allowed:
            issues.append({'code':'check_citation_scope','id':key,
                           'invalid_observation_ids':sorted(refs-allowed),
                           'allowed_observation_ids':sorted(allowed),
                           'detail':'A check assessment cites only this executed check\'s returned observations '
                                    'within its dossier scope. Baseline/comparison observations may support '
                                    'the overall finding but are not outputs of this check. Reassess the '
                                    'narrow check outcome; do not substitute or silently remove evidence.'})
        if assessment['outcome']!='inconclusive' and not refs:
            issues.append({'code':'check_missing_support','id':key})
        if key[1] and assessment['outcome']!='inconclusive' and not contract.get('refutation_condition'):
            issues.append({'code':'missing_discriminating_condition','id':key})
        if assessment['outcome']=='refutes' and check.get('status') not in ('covered','covered_zero') and not refs:
            issues.append({'code':'partial_search_not_refutation','id':key})
    for key in by_key.keys()-seen:
        output.setdefault('check_assessments',[]).append({'check_id':key[0],'dossier_id':key[1],'contract_id':key[2],
            'outcome':'inconclusive','evaluation_status':'unassessed','reason':'이 가설의 판별조건에 대한 결과 평가가 제공되지 않았습니다.','observation_ids':[]})
    return issues
