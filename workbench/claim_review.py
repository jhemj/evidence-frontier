"""Claim-bound review input, separate from open-ended discovery selection.

Required originals cannot compete with detector diversity for presentation.
Only optional context may be omitted. An unfit required source set is an input
gap, never a completed review with a silently missing premise.
"""
from copy import deepcopy
import hashlib

from .investigation import compact_observation
from .review_context import InputBudgetError, fit_metadata_only, serialize


VERSION = 'claim-review-input-1'


def claim_scope(claim):
    snapshot={key:deepcopy(claim.get(key)) for key in
              ('id','case_id','text','observation_ids','counterevidence_ids')}
    # Detect concurrent replacement without giving a previous model's prose
    # the appearance of another original source in the next review.
    snapshot['previous_review_sha256']=hashlib.sha256(
        serialize(claim.get('falsification')).encode()).hexdigest()
    return snapshot


def bound_jobs(claim, jobs):
    return [j for j in jobs if claim['id'] in j.get('claim_ids', [])]


def build_pack(controller, claim, jobs=(), maximum=36000):
    from .investigator import TOOLS
    from .evidence_selection import related_sources
    from .review_stream import resolved
    cid=claim['case_id']; platform=controller.store.get(cid).get('target_os','linux')
    active=controller.active_observations(cid)
    by_id={o['id']:o for o in active}
    required=list(dict.fromkeys(claim['observation_ids']+claim.get('counterevidence_ids',[])
        +(claim.get('falsification') or {}).get('contradicting_observation_ids',[])))
    missing=sorted(set(required)-set(by_id))
    if not required or missing:
        raise InputBudgetError('Claim review requires every active cited original; missing: '+', '.join(missing))
    evidence_ids={by_id[oid]['evidence_id'] for oid in required}
    eligible=[o for o in active if o['evidence_id'] in evidence_ids]
    assigned=bound_jobs(claim,jobs)
    checks=[{'id':j['id'],'request':deepcopy(j['request']),'status':j.get('result_status'),
             'observation_ids':list(j.get('observation_ids',[])),
             'result_scope':deepcopy(j.get('result_scope',{}))} for j in assigned]
    pack={'target_os':platform,'available_tools':list(TOOLS[platform]),
          'observations':[compact_observation(by_id[oid],preserve_content=True) for oid in required],
          'executed_checks':checks,'selection_is_partial':True,
          'review_scope':{'version':VERSION,'claim_id':claim['id'],
              'claim_snapshot':claim_scope(claim),
              'required_observation_ids':required,'missing_required_ids':[],
              'executed_check_ids':[j['id'] for j in assigned],
              'unassigned_historical_checks':sum(j.get('purpose')=='challenge' and j not in assigned for j in jobs),
              'instruction':'Every cited premise is presented. Additional case sources may be unexamined, never absent. '
                  'Check results belong only to the explicitly bound jobs; their omitted returned sources are NOT evaluated. '
                  'A repeated read or duplicate record is not independent corroboration.'}}
    def finalize(value, reasons):
        ids=[o['id'] for o in resolved(value)['observations']]
        if set(required)-set(ids):raise InputBudgetError('Required claim premises were omitted')
        value['total_observations']=len(eligible);value['included_observations']=len(ids)
        value['selection_audit']={'version':VERSION,'available':len(eligible),'included':len(ids),
            'omitted':len(eligible)-len(ids),'required':required,'missing_required':[],
            'selected':[{'id':oid,'reason':reasons[oid]} for oid in ids],
            'scope':'All claim premises retained; optional context is partial. Not a completeness or independence certificate.'}
        for check in value['executed_checks']:
            check['unpresented_observation_ids']=[oid for oid in check['observation_ids'] if oid not in ids]
        fit_metadata_only(value,maximum)
        return value
    reasons={oid:'required_claim_premise' for oid in required}
    # First prove that the mandatory floor itself fits, without any sampling.
    pack=finalize(pack,reasons)
    optional=list(dict.fromkeys([oid for j in assigned for oid in j.get('observation_ids',[])]
        +[o['id'] for o in related_sources(eligible,[by_id[oid] for oid in required])]))
    for oid in optional:
        if oid in reasons or oid not in by_id or by_id[oid]['evidence_id'] not in evidence_ids:continue
        candidate=resolved(pack)
        candidate['observations'].append(compact_observation(by_id[oid],preserve_content=True))
        proposed={**reasons,oid:'bound_check_result_or_literal_path_context'}
        try:candidate=finalize(candidate,proposed)
        except InputBudgetError:continue
        pack=candidate;reasons=proposed
    validate_scope(controller,claim,pack)
    return pack


def validate_scope(controller,claim,pack):
    from .review_stream import resolved
    expected=pack['review_scope']['claim_snapshot']
    if claim_scope(controller.store.get(claim['id']))!=expected:
        raise InputBudgetError('Claim changed while its premises were being reviewed')
    required=pack['review_scope']['required_observation_ids']
    active={o['id']:o for o in controller.active_observations(claim['case_id'])}
    shown={o['id']:o for o in resolved(pack)['observations']}
    for oid in required:
        if oid not in active or oid not in shown:
            raise InputBudgetError('Claim review premise disconnected or not presented: '+oid)
        if shown[oid]!=compact_observation(active[oid],preserve_content=True):
            raise InputBudgetError('Claim review source view changed: '+oid)


def input_hash(pack):
    return hashlib.sha256(serialize(pack).encode()).hexdigest()
