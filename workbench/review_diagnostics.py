"""Stable, actionable failure reasons without weakening evidence validation."""


def classify(error, issues=()):
    codes={i['code'] for i in issues}
    if codes & {'static_facts_not_behavior','static_content_not_invocation','absence_preconditions_unverified','missing_claim_stages'}:
        return 'unsupported_inference'
    if codes & {'unknown_observation','unrelated_observation','stage_citation_scope','check_citation_scope','missing_support','synthesis_citation_scope','synthesis_missing_support'}:
        return 'citation_scope'
    if codes:return 'output_contract'
    if getattr(error,'category',None):return error.category
    if 'timeout' in type(error).__name__.lower():return 'timeout'
    if 'transport' in type(error).__name__.lower() or 'connect' in type(error).__name__.lower():return 'transport'
    return 'schema_or_provider'
