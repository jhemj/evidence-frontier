"""Explicit, independently selectable review policies; no case-name rules."""

from .judgment_snapshot import digest
from .review_contracts import contract, contracts
from contextlib import nullcontext

RETURNED_STREAK_LIMIT = 3
WORKER_POLL_INTERVAL = 8


def result_revision(result):
    """Version the retained physical result, not its display or receipt time."""
    return digest(result)


def logical_owner(intent, job):
    """Resolve an exact logical contract; a shared physical job is not an owner.

    New intents retain the caller's dossier and condition fingerprint. Legacy
    intents can be recovered only when their conditions name one unique owner
    among the job's contracts. Identical conditions for two owners are unknown.
    """
    conditions = intent.get('conditions') or {}
    explicit = (intent.get('scope') or {}).get('logical_contract')
    if explicit:
        did = explicit.get('dossier_id')
        if not did:
            return None
        if explicit.get('contract_version')==2:
            expected=contract({'hypothesis_id':did,'question_id':explicit.get('question_id'),
                'test_design':explicit.get('test_design'),
                '_controller_owner_lineage':explicit.get('controller_owner_lineage'),**conditions})
        else:expected = contract({'hypothesis_id': did, **conditions})
        if explicit.get('contract_id') != expected['contract_id']:
            return None
        return expected if expected in contracts(job) else None
    candidates = [c for c in contracts(job) if
        contract({'hypothesis_id': c['dossier_id'], **conditions}) == c]
    owners = {(c['dossier_id'], c['contract_id']) for c in candidates}
    return candidates[0] if len(owners) == 1 else None


def _same_scope(row, batch, *, nested=False):
    scope = (row.get('scope') or {}) if nested else row
    return (bool(batch.get('case_id') and batch.get('task_id') and batch.get('evidence_id')) and
        row.get('case_id') == batch.get('case_id') and
        all(scope.get(k) == batch.get(k) for k in ('task_id', 'evidence_id')) and
        scope.get('generation', 0) == batch.get('generation', 0))


def current_assessment(intent, job, owner):
    """A retained old assessment never satisfies a changed result or owner."""
    assessment = intent.get('latest_assessment') or {}
    if intent.get('assessment_status') != 'assessed':
        return False
    if (assessment.get('check_id') != job['id'] or
        assessment.get('dossier_id') != owner['dossier_id'] or
        assessment.get('contract_id') != owner['contract_id'] or
        assessment.get('evaluation_status') == 'unassessed'):
        return False
    revision = intent.get('assessment_result_revision')
    # Legacy writers reset assessment_status when finish_intents receives a new
    # result. Its exact retained snapshot must still equal the current job.
    actual = {'job_id': job['id'], 'observation_ids': job.get('observation_ids', []),
              'scope': job.get('result_scope') or {}}
    return (intent.get('result_scope') == actual and
            (revision is None or revision == result_revision(actual)))


def batch_readiness(batch, jobs, *, available_observation_ids=None, intents=()):
    """Scheduling feasibility, not semantic importance or a hypothesis verdict."""
    indexed = {j['id']: j for j in jobs}
    linked = [indexed.get(i) for i in batch.get('job_ids', [])]
    if any(j is None or not _same_scope(j, batch) for j in linked):
        return False, 'missing_or_inactive_job_scope'
    if batch.get('status') == 'await_checks':
        if any(j.get('status') != 'ingested' for j in linked):
            # New tool dispatch is actionable; repeatedly polling a queued or
            # running worker is not progress and must yield to ready reviews.
            if any(j.get('status') == 'admitted' for j in linked):
                return True, 'tool_dispatch_required'
            return False, 'worker_result_pending'
    elif batch.get('status') != 'pending':
        return False, 'batch_not_reviewable'
    elif any(j.get('status') != 'ingested' for j in linked):
        return False, 'worker_result_pending'
    if available_observation_ids is not None:
        required = set()
        owners = set(batch.get('dossier_ids', []))
        for intent in intents:
            if not _same_scope(intent, batch, nested=True):
                continue
            job = indexed.get((intent.get('result_scope') or {}).get('job_id'))
            owner = logical_owner(intent, job) if job else None
            if owner and owner['dossier_id'] in owners:
                design = (intent.get('admission') or {}).get('design') or (intent.get('scope') or {}).get('test_design') or {}
                required.update(design.get('required_observation_ids', []))
                required.update(design.get('baseline_observation_ids', []))
                if design.get('version')==2:
                    required.update(r['ref']['id'] for r in design.get('required_inputs',[]))
        if required - set(available_observation_ids):
            return False, 'required_context_unavailable'
    return True, 'review_ready'


def returned_obligations(batch, intents, jobs, *, task=None, evidence=None,
                         available_observation_ids=None, dossiers=None):
    """Real returned, unassessed contracts belonging to this active batch.

    Incomplete searches and typed failures can be reviewed as limited or
    inconclusive results. They never acquire support/absence semantics here.
    """
    if task is not None and (task.get('superseded') or task['id'] != batch.get('task_id') or
        task.get('evidence_id') != batch.get('evidence_id') or
        task.get('case_id', batch.get('case_id')) != batch.get('case_id') or
        task.get('retry_generation', 0) != batch.get('generation', 0)):
        return []
    if evidence is not None and (not evidence.get('connected', True) or
        evidence['id'] != batch.get('evidence_id')):
        return []
    indexed = {j['id']: j for j in jobs}
    ready, waiting_reason = batch_readiness(batch, jobs,
        available_observation_ids=available_observation_ids, intents=intents)
    owners = set(batch.get('dossier_ids', []))
    result = []
    for intent in intents:
        if not _same_scope(intent, batch, nested=True) or intent.get('status') not in ('complete', 'partial'):
            continue
        returned = intent.get('result_scope') or {}
        jid = returned.get('job_id')
        job = indexed.get(jid)
        if (jid not in batch.get('job_ids', []) or not job or not _same_scope(job, batch) or
            intent.get('id') not in job.get('test_intent_ids', []) or job.get('status') != 'ingested'):
            continue
        scope = intent.get('scope') or {}
        if scope.get('source_run') is not None and scope.get('source_run') != job.get('source_run'):
            continue
        if evidence is not None and scope.get('signature') is not None and scope['signature'] != evidence.get('signature'):
            continue
        owner = logical_owner(intent, job)
        if not owner or owner['dossier_id'] not in owners:
            continue
        if dossiers is not None:
            target = dossiers.get(owner['dossier_id'])
            if not target or not _same_scope(target, batch):
                continue
        if current_assessment(intent, job, owner):
            continue
        actual = {'job_id': jid, 'observation_ids': job.get('observation_ids', []),
                  'scope': job.get('result_scope') or {}}
        physical_scope = actual['scope']
        ids = set(actual['observation_ids'])
        reason = ('worker_result_pending' if waiting_reason == 'tool_dispatch_required'
                  else waiting_reason if not ready else None)
        if returned != actual:
            reason = 'returned_scope_changed'
        elif not physical_scope or job.get('result_status', physical_scope.get('status')) not in (
            'covered', 'complete', 'partial', 'failed', 'error'):
            reason = 'result_scope_unavailable'
        elif available_observation_ids is not None and ids - set(available_observation_ids):
            reason = 'returned_sources_unavailable'
        result.append({'intent_id': intent['id'], 'job_id': jid, **owner,
            'result_revision': result_revision(actual), 'can_advance_now': reason is None,
            'waiting_reason': reason, 'completed_at': intent.get('completed_at')})
    return result


def second_pass(task,output,pack,batch):
    if task.get('second_review_policy','always')!='conditional-v1':return True
    # A single pass is allowed only for literal-bound background facts. Critical
    # findings, interpretive judgments, alternatives and changed results still
    # require comparison. This is scheduling, not confidence promotion.
    if (batch.get('job_ids') or batch.get('deferred_checks') or pack.get('open_objections')
            or pack.get('review_stream') or output.get('explanation_proposals')
            or output.get('objection_assessments') or not output.get('findings')):return True
    return any(f.get('timeline_role')=='핵심'
        or (f.get('incident_relevance') or {}).get('level','undetermined') in ('direct','indirect','undetermined')
        or f.get('judgment')=='유력' or f.get('basis')=='absence'
        or f.get('alternatives') or f.get('remaining_checks') or f.get('open_objections')
        # A literal configuration fact is eligible; behavioral/intent stages
        # and tentative stages are interpretations even on a background card.
        or any(s.get('stage')!='configuration' or s.get('judgment')!='확인' for s in f.get('stages',[]))
        or not f.get('fact_assertions')
        for f in output.get('findings',[]))


def returned_rank(batch,intents,jobs=(),**context):
    """Optional priority for real returned, unassessed logical contracts.

    Their count is not independent physical results or semantic importance.
    """
    return 0 if any(r['can_advance_now'] for r in
        returned_obligations(batch,intents,jobs,**context)) else 1


def order_queue(batches, intents, jobs, task, priority, *, evidence=None,
                available_observation_ids=None, dossiers=None):
    """Opt-in selection with bounded return streaks and worker-poll fairness.

    The baseline queue remains unchanged at the caller. Limits count actual
    selections, not model requests, importance, probability or investigation
    completion. Each row carries an ephemeral selection explanation.
    """
    seq = task.get('review_selection_seq', 0)
    streak = task.get('returned_selection_streak', 0)
    rows = []
    for batch in batches:
        obligations = returned_obligations(batch, intents, jobs, task=task,
            evidence=evidence, available_observation_ids=available_observation_ids, dossiers=dossiers)
        ready, waiting = batch_readiness(batch, jobs,
            available_observation_ids=available_observation_ids, intents=intents)
        returned = [r for r in obligations if r['can_advance_now']]
        poll_due = waiting == 'worker_result_pending' and (
            seq - batch.get('last_review_selection_seq', 0) >= WORKER_POLL_INTERVAL)
        rank = 0 if returned else 1 if ready or poll_due else 2
        reason = ('returned_contract_ready' if returned else 'worker_poll_fairness' if poll_due
                  else 'tool_dispatch_required' if waiting == 'tool_dispatch_required'
                  else 'question_priority_ready' if ready else waiting)
        row = {**batch, '_review_selection': {'policy': 'returned-first-v1',
            'reason': reason, 'can_advance_now': bool(returned) or ready,
            'waiting_reason': None if ready else waiting,
            'returned_obligations': obligations,
            'streak_limit': RETURNED_STREAK_LIMIT, 'worker_poll_interval': WORKER_POLL_INTERVAL}}
        rows.append((rank, priority(batch), row))
    # One baseline-ready selection after a finite returned streak. A blocked
    # batch never wins this fairness slot merely because its result returned.
    if streak >= RETURNED_STREAK_LIMIT:
        normal = [r for r in rows if r[0] == 1]
        if normal:
            chosen = min(normal, key=lambda r: r[1])[2]['id']
            rows = [(-1 if r[2]['id'] == chosen else r[0], r[1],
                {**r[2], '_review_selection': {**r[2]['_review_selection'],
                 'reason': 'finite_returned_streak'}} if r[2]['id'] == chosen else r[2]) for r in rows]
    return [r[2] for r in sorted(rows, key=lambda r: (r[0], r[1]))]


def record_selection(store, cid, task, batch):
    """Record an actual selected unit; preparing or displaying a queue is inert."""
    decision = batch.get('_review_selection')
    if not decision or task.get('review_queue_policy') != 'returned-first-v1':
        return
    with store.lock, (nullcontext() if store.db.in_transaction else store.tx()):
        current = store.get(task['id'])
        if (current.get('superseded') or current.get('review_queue_policy') != 'returned-first-v1'
            or current.get('retry_generation', 0) != batch.get('generation', 0)):
            return
        seq = current.get('review_selection_seq', 0) + 1
        streak = current.get('returned_selection_streak', 0) + 1 if decision['reason'] == 'returned_contract_ready' else 0
        store.update(task['id'], review_selection_seq=seq, returned_selection_streak=streak)
        store.update(batch['id'], last_review_selection_seq=seq)
        store.add('receipt', cid, task_id=task['id'], evidence_id=batch.get('evidence_id'),
            generation=batch.get('generation', 0), batch_id=batch['id'], receipt_type='review_selection',
            selection_seq=seq, **decision,
            scope='Scheduling decision only; returned logical contracts are not independent evidence or incident confidence.')
