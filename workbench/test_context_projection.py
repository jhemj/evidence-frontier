"""Controller projection of exact V2 inputs; no new planning or tool authority.

Legacy inputs are byte-for-byte unchanged. V2 views are rebuilt after fitting or
paging so a retained body is never counted as a body actually shown to a model.
"""
from .test_contract_v2 import POLICY, build_manifest


def enabled(task):
    return task.get('test_contract_policy', 'legacy') == POLICY


def source_run_id(observations):
    return next((o.get('fields', {}).get('run_id') for o in reversed(list(observations))
        if o.get('type') in ('linux_environment', 'windows_environment')
        and o.get('fields', {}).get('run_id')), None)


def attach(pack, store, cid, task, evidence, source_run):
    if not enabled(task):
        return pack
    from .review_stream import resolved
    shown = resolved(pack)
    questions = shown.get('question_context', shown.get('question_memory', {})).get('questions', [])
    owners = shown.get('required_dossiers') or questions
    jobs=[]
    for item in shown.get('executed_checks',[]):
        try:jobs.append(store.get(item['id'],'investigation_job'))
        except (ValueError,KeyError,TypeError):continue
    pack['test_contract_policy'] = POLICY
    pack['test_contract_context'] = build_manifest(store, cid, task, evidence, source_run,
        owners=owners, questions=questions, observations=shown.get('observations', []),
        jobs=jobs)
    return pack


def fit_presented(pack, fit, store, cid, task, evidence, source_run, *args, **kwargs):
    """Finite rebind only, under the existing caller's exact budget policy."""
    if not enabled(task):
        return fit(pack, *args, **kwargs)
    from .review_context import InputBudgetError
    for _ in range(3):
        attach(pack, store, cid, task, evidence, source_run)
        offered = pack['test_contract_context']
        result = fit(pack, *args, **kwargs)
        attach(pack, store, cid, task, evidence, source_run)
        if offered == pack['test_contract_context']:
            return result
    raise InputBudgetError('V2 actual presentation manifest did not converge within three bindings')
