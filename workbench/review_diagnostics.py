"""Stable, actionable failure reasons without weakening evidence validation."""
import json
from copy import deepcopy


FEEDBACK_LIMIT = 4096


def smaller_input_budget(maximum, metadata):
    """Monotone retry after measured provider pressure, never a context increase."""
    budget=metadata.get('prompt_budget',{})
    actual=budget.get('exact_input_tokens')
    context=budget.get('context_tokens_requested')
    output=budget.get('output_tokens_reserved')
    ratio=0.75
    if all(isinstance(v,(int,float)) and not isinstance(v,bool) for v in (actual,context,output)) and actual>0:
        ratio=min(ratio,max(0.1,(context-output)/actual*0.8))
    return max(1,int(maximum*ratio))


def repair_feedback(error, issues, diagnostic_id):
    """Bound non-evidentiary repair hints; the complete failure stays immutable.

    The page compiler reserves this same envelope. Never truncate source rows,
    predicates or citations to make room for a model's malformed response.
    """
    category=classify(error,issues)
    result={'diagnostic_id':diagnostic_id,
        'errors':deepcopy(issues) or [{'code':category,'detail':str(error)[:2000]}],
        'instruction':'Rejected output is not evidence. Correct schema errors using the published bounds. Reassess from the provided observations. Only allowed_observation_ids may be cited. Never substitute IDs or remove citations merely to pass validation.'}
    size=lambda:len(json.dumps(result,ensure_ascii=False,separators=(',',':')))
    if size()>FEEDBACK_LIMIT:
        result['errors']=[{'code':code} for code in sorted({i['code'] for i in result['errors']})]
        result['detail_scope']='Error details omitted from this repair hint only; full errors, rejected output and original input remain in the referenced immutable diagnostic. Current source/contract allowlists are unchanged.'
    if size()>FEEDBACK_LIMIT:
        # A future unbounded vocabulary cannot consume the evidence budget.
        result['errors']=[{'code':category}]
    return result


def classify(error, issues=()):
    codes={i['code'] for i in issues}
    if codes & {'static_facts_not_behavior','static_content_not_invocation','absence_preconditions_unverified','missing_claim_stages'}:
        return 'unsupported_inference'
    if codes & {'unknown_observation','unrelated_observation','stage_citation_scope','check_citation_scope','missing_support','synthesis_citation_scope','synthesis_missing_support','synthesis_cannot_resolve_foreign_objection','objection_resolution_citation_scope','evidence_span_citation_scope'}:
        return 'citation_scope'
    if codes:return 'output_contract'
    if getattr(error,'category',None):return error.category
    if 'timeout' in type(error).__name__.lower():return 'timeout'
    if 'transport' in type(error).__name__.lower() or 'connect' in type(error).__name__.lower():return 'transport'
    return 'schema_or_provider'
