"""Explicit, independently selectable review policies; no case-name rules."""


def second_pass(task,output,pack,batch):
    if task.get('second_review_policy','always')=='always':return True
    # A single pass is allowed only for literal-bound background facts. Critical
    # findings, interpretive judgments, alternatives and changed results still
    # require comparison. This is scheduling, not confidence promotion.
    if (batch.get('job_ids') or batch.get('deferred_checks') or pack.get('open_objections')
            or pack.get('review_stream') or output.get('explanation_proposals')):return True
    return any(f.get('timeline_role')=='핵심'
        or (f.get('incident_relevance') or {}).get('level','undetermined') in ('direct','indirect','undetermined')
        or f.get('judgment')=='유력' or f.get('basis')=='absence'
        or f.get('alternatives') or f.get('remaining_checks') or f.get('open_objections')
        or not f.get('fact_assertions')
        for f in output.get('findings',[]))


def returned_rank(batch,intents):
    """Optional priority for real returned, unassessed logical contracts.

    Their count is not independent physical results or semantic importance.
    """
    jobs=set(batch.get('job_ids',[]))
    returned=[i for i in intents if i.get('status') in ('complete','partial')
        and i.get('assessment_status')!='assessed' and (i.get('result_scope') or {}).get('job_id') in jobs]
    return 0 if returned else 1
