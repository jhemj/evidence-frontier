"""Compiler-aware, finite planner selection; no transport or evidence edits.

Only whole optional observations can move to the unreviewed lane. Source
bytes, mandatory question/owner/test context and output/schema budgets remain
unchanged. Lossless metadata factoring and final V2 rebinding are measured
against the same native request compiler used by the Provider.
"""
from copy import deepcopy
import hashlib

from .review_context import InputBudgetError, fit_metadata_only, model_view_size, serialize
from .request_compiler import compile_spec, request_spec

VERSION='planner-compiled-input-1'


class PlannerInputBlocked(InputBudgetError):
    category='input_budget'
    def __init__(self,compiled,*,protected_ids,deferred_ids,material_binding,reason):
        super().__init__('Planner minimum input cannot fit without dropping protected evidence or obligations; retained scope requires paging or changed input.')
        self.metadata={'failure_category':'input_budget','phase':'input_preparation',
            'request_attempted':False,'delivery_state':'not_sent','reason':reason,
            'prompt_budget':compiled.budget,'compiled_request':compiled.identity,
            'material_binding':material_binding,'protected_observation_ids':list(protected_ids),
            'deferred_observation_ids':list(deferred_ids),'automatic_same_input_retry':False}


def material_binding(spec,pack):
    """Feedback/counters alone are not a changed source/question scope."""
    selected={k:pack[k] for k in ('target_os','available_tools','tool_capabilities',
        'test_contract_policy','observations','hypotheses',
        'dynamic_hypotheses','question_memory','test_contract_context','accepted_claim_refs',
        'completed_result_memory','completed_tools','coverage_checklist','open_objections') if k in pack}
    return hashlib.sha256(serialize([spec,selected]).encode()).hexdigest()


def fit_planner_input(pack,config,question,*,rebind,audit,claims,protected_ids=(),
                      rejected_material_bindings=(),maximum=36000):
    """Monotone whole-record reselection, bounded by the candidate count.

    A protected record that exceeds the minimum floor is a typed preparation
    block, not permission to truncate it or repeatedly request the same input.
    Ordinary omissions remain explicit and uncitable; they are not negatives.
    """
    remaining=deepcopy(pack);original=[o['id'] for o in remaining.get('observations',[])]
    protected=set(protected_ids);deferred=[];spec=request_spec(config,question,'investigator')
    rejected=set(rejected_material_bindings)
    for attempt in range(len(original)+1):
        candidate=deepcopy(remaining)
        candidate['accepted_claim_refs']=claims(candidate)
        rebind(candidate)
        binding=material_binding(spec,candidate)
        candidate['planner_input_projection']={'version':VERSION,'material_binding':binding,
            'selected_observation_ids':[o['id'] for o in candidate.get('observations',[])],
            'deferred_observation_ids':list(deferred),'deferred_count':len(deferred),
            'selection_attempts':attempt+1,
            'scope':'Whole-record presentation selection only. Deferred records remain unreviewed in the ledger, are not citable in this request, and are not absent or refuted.'}
        compiled=None
        try:
            compiled=fit_metadata_only(candidate,maximum,request_spec=spec)
            # Metadata factoring can change the presented layout. Rebind to
            # actual visible bodies, then compile the FINAL exact bytes again.
            rebind(candidate)
            compiled=compile_spec(spec,candidate)
            compiled.assert_fits()
            if len(serialize(candidate))>maximum and model_view_size(candidate)>maximum:
                raise InputBudgetError('Final planner presentation exceeded the unchanged character bound')
            if binding not in rejected:return candidate,compiled
            reason='unchanged_rejected_material'
        except InputBudgetError:
            compiled=compile_spec(spec,candidate)
            reason='minimum_compiled_envelope'
        remove_at=next((i for i in range(len(remaining.get('observations',[]))-1,-1,-1)
            if remaining['observations'][i]['id'] not in protected),None)
        if remove_at is None:
            raise PlannerInputBlocked(compiled,protected_ids=[i for i in original if i in protected],
                deferred_ids=deferred,material_binding=binding,reason=reason)
        deferred.append(remaining['observations'].pop(remove_at)['id'])
        remaining['included_observations']=len(remaining['observations'])
        remaining['selection_is_partial']=True
        remaining['selection_audit']=audit(remaining)
    raise AssertionError('Whole-record planner selection must terminate')
