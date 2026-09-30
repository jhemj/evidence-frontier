"""Opt-in retrieval of *issued* worker jobs, never a new-job scheduler.

Worker I/O is outside both the model lock and Store transactions. A bounded
number of daemon readers accommodates a slow transport without blocking the
controller; timed-out readers are retained, not duplicated. BEGIN IMMEDIATE and
the job's immutable collection identity fence ingestion across Store instances.
Raw collection does not assess a test, close a question, or change a hypothesis.
"""
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import json
import re
import threading
import time

from .evidence_access import worker_request
from .models import WorkerJobRequest
from .retrieval import tool_scope, fingerprint_scope
from .store import now

VERSION = 'issued-worker-collector-1'
STATUS_VERSION = 'worker-job-status-2'
HEX64 = re.compile(r'^[a-f0-9]{64}$')
HEX32 = re.compile(r'^[a-f0-9]{32}$')
RUN = re.compile(r'^RUN-[a-f0-9]{32}$')
TERMINAL = {'succeeded', 'failed', 'execution_unknown'}
RESERVED_EVENT_FIELDS = {'id', 'kind', 'case_id', 'created_at', 'evidence_id',
                         'receipt_id', 'cell_id', 'digest'}


def digest(value):
    # Exact existing worker result encoding, not a new compact hash convention.
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def request_body(job, evidence, case):
    """Use the existing dispatch adapter, including all model defaults."""
    body = WorkerJobRequest.model_validate({
        'job_key': job['fingerprint'], 'signature': evidence['signature'],
        'action': 'investigation_tool', 'path': evidence['path'],
        'investigation': {'evidence_path': evidence['path'], 'run_id': job['source_run'],
                          'request': tool_scope(job['request']),
                          'target_os': case.get('target_os', 'linux')}}).model_dump()
    body.pop('job_key')
    return body


def request_digest(body):
    # WorkerJobs.submit uses default ensure_ascii=True and spaced JSON.
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def _issued_in_epoch(job, epoch):
    try:
        dispatched = datetime.fromisoformat(job['dispatched_at'])
        started = datetime.fromisoformat(epoch['started_at'])
        return bool(dispatched.tzinfo and started.tzinfo and dispatched >= started)
    except (KeyError, TypeError, ValueError):
        return False


@dataclass
class _Read:
    scope: dict
    started: float
    done: threading.Event = field(default_factory=threading.Event)
    reply: object = None
    error: object = None


class ResultCollector:
    def __init__(self, controller, *, enabled=False, fetch=None,
                 wallclock_budget=.1, poll_timeout=1., interval=.5,
                 max_inflight=2, max_jobs=8):
        if not 0 < wallclock_budget <= 5 or not 0 < poll_timeout <= 30:
            raise ValueError('collector polling bounds must be positive and bounded')
        if not 1 <= max_inflight <= 4 or not 1 <= max_jobs <= 32:
            raise ValueError('collector concurrency bounds exceeded')
        self.controller = controller
        self.store = controller.store
        self.enabled = bool(enabled)
        self.fetch = fetch or worker_request
        self.wallclock_budget = wallclock_budget
        self.poll_timeout = poll_timeout
        self.interval = max(.05, interval)
        self.max_inflight = max_inflight
        self.max_jobs = max_jobs
        self.stop = threading.Event()
        self.thread = None
        self._reads = {}
        self._cycle_lock = threading.Lock()
        self._cursor = ''
        self.last_cycle = None
        self.last_error = None

    def start(self):
        if not self.enabled or self.thread is not None:
            return
        self.thread = threading.Thread(target=self._loop, name='issued-result-collector', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(self.wallclock_budget + .1)
        # A GET reader has no dispatch power. Worker output remains durable if
        # this process exits before a late reader finishes.

    def _loop(self):
        while not self.stop.is_set() and not self.controller.stop.is_set():
            try:
                self.collect_once()
            except Exception as ex:
                # Collector availability is not permission to pause/restart an
                # investigation or spend model/retry budgets.
                self.last_error = {'phase':'collector_cycle', 'category':type(ex).__name__,
                                   'observed_at':now(), 'interpretation':'collection_not_evaluation'}
            self.stop.wait(self.interval)

    def _source(self, case_id, evidence_id, source_run):
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT body FROM records WHERE case_id=? AND "
                "json_extract(body,'$.evidence_id')=? AND ((kind='receipt' AND "
                "json_extract(body,'$.result.run_id')=?) OR (kind='observation' AND "
                "json_extract(body,'$.type') IN ('linux_environment','windows_environment') "
                "AND json_extract(body,'$.fields.run_id')=?)) ORDER BY created_at,id LIMIT 1",
                (case_id, evidence_id, source_run, source_run)).fetchone()
        if row:
            record = json.loads(row[0])
            return {'id': record['id'], 'digest': digest(record)}
        return None

    def _source_head(self, case_id, evidence_id):
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT body FROM records WHERE case_id=? AND json_extract(body,'$.evidence_id')=? "
                "AND ((kind='receipt' AND json_extract(body,'$.result.run_id') IS NOT NULL) "
                "OR (kind='observation' AND json_extract(body,'$.type') "
                "IN ('linux_environment','windows_environment') AND json_extract(body,'$.fields.run_id') IS NOT NULL)) "
                "ORDER BY created_at DESC,id DESC LIMIT 1", (case_id,evidence_id)).fetchone()
        if not row:return None
        record=json.loads(row[0])
        source_run=(record.get('result') or {}).get('run_id') if record['kind']=='receipt' else (record.get('fields') or {}).get('run_id')
        task_id=record.get('task_id')
        if not task_id and record.get('receipt_id'):
            try:task_id=self.store.get(record['receipt_id'],'receipt').get('task_id')
            except ValueError:pass
        active=True
        if task_id:
            try:
                owner=self.store.get(task_id,'task')
                active=(owner.get('case_id')==case_id and owner.get('evidence_id')==evidence_id
                        and not owner.get('superseded'))
            except ValueError:active=False
        return {'id':record['id'],'digest':digest(record),'source_run':source_run,
                'source_owner_active':bool(active)}

    def _candidates(self):
        # Bound materialization as well as HTTP reader count. A rotating cursor
        # means an old unverified job cannot starve later issued jobs.
        with self.store.lock:
            rows = self.store.db.execute(
                "SELECT id,body FROM records WHERE kind='investigation_job' AND id>? "
                "AND json_extract(body,'$.dispatched_at') IS NOT NULL "
                "AND json_extract(body,'$.status')<>'ingested' "
                "AND json_extract(body,'$.collector_terminal_receipt_id') IS NULL "
                "ORDER BY id LIMIT ?", (self._cursor, self.max_jobs)).fetchall()
        self._cursor = rows[-1][0] if rows else ''
        return [json.loads(row[1]) for row in rows]

    def _scope(self, job):
        """Capture canonical owner and dispatch scope; no filesystem lookup."""
        case = self.store.get(job['case_id'], 'case')
        task = self.store.get(job['task_id'], 'task')
        evidence = self.store.get(job['evidence_id'], 'evidence')
        if task['case_id'] != case['id'] or evidence['case_id'] != case['id']:
            raise ValueError('foreign owner')
        if task.get('evidence_id') != evidence['id']:
            raise ValueError('foreign task evidence')
        if type(job.get('generation', 0)) is not int or job.get('generation', 0)<0:
            raise ValueError('invalid job generation')
        if not HEX64.fullmatch(str(job.get('fingerprint', ''))):
            raise ValueError('untrusted worker job key')
        if not RUN.fullmatch(str(job.get('source_run', ''))):
            raise ValueError('source run unavailable')
        source = self._source(case['id'], evidence['id'], job['source_run'])
        if source is None:
            raise ValueError('source run not in this evidence ledger')
        epoch = self.store.get(case['epoch_id'], 'epoch') if case.get('epoch_id') else {}
        source_head=self._source_head(case['id'],evidence['id'])
        binding = task.get('runtime_binding') or case.get('runtime_binding') or {}
        scope = {'version': VERSION, 'job_id': job['id'], 'case_id': case['id'],
                 'task_id': task['id'], 'evidence_id': evidence['id'],
                 'generation': job.get('generation', 0), 'source_run': job['source_run'],
                 'source_ref': source, 'worker_job_key': job['fingerprint'],
                 'source_head':source_head,
                 'request_sha256': request_digest(request_body(job, evidence, case)),
                 'logical_request_digest': digest(job['request']),
                 'signature': evidence['signature'], 'path': evidence['path'],
                 'dispatched_at': job.get('dispatched_at'),
                 'epoch_id': epoch.get('id'), 'dispatch_scope_verified': _issued_in_epoch(job, epoch),
                 'runtime_fingerprint': binding.get('fingerprint'),
                 'worker_runtime_code': binding.get('worker_source')}
        scope['scope_digest'] = digest(scope)
        return scope

    def _pin(self, job):
        # Pin once, so ending/restarting a scope cannot reclassify a late result
        # as a fresh result. This transaction has no I/O/model calls.
        if job.get('collector_scope'):
            return job['collector_scope']
        scope = self._scope(job)
        with self.store.tx():
            current = self.store.get(job['id'], 'investigation_job')
            if current.get('collector_scope'):
                return current['collector_scope']
            if current.get('status') == 'ingested' or current.get('collector_terminal_receipt_id'):
                return None
            if digest(current.get('request')) != scope['logical_request_digest']:
                return None
            if any(current.get(k) != job.get(k) for k in
                   ('case_id', 'task_id', 'evidence_id', 'generation', 'source_run', 'fingerprint')):
                return None
            self.store.update(job['id'], collector_scope=scope)
        return scope

    def _current(self, scope, job):
        """Fail closed if any exact owner/version fence changed."""
        try:
            current = self._scope(job)
            case = self.store.get(scope['case_id'], 'case')
            task = self.store.get(scope['task_id'], 'task')
            evidence = self.store.get(scope['evidence_id'], 'evidence')
            epoch = self.store.get(scope['epoch_id'], 'epoch') if scope.get('epoch_id') else {}
            actual = self.controller.runtime_binding()
        except (ValueError, KeyError, TypeError):
            return False
        return bool(current == scope and scope.get('dispatch_scope_verified')
                    and (scope.get('source_head') or {}).get('source_run')==scope['source_run']
                    and (scope.get('source_head') or {}).get('source_owner_active') is True
                    and case.get('status') == 'running' and epoch.get('status') == 'running'
                    and task.get('status') in {'queued', 'running'} and not task.get('superseded')
                    and job.get('status') not in {'cancelled', 'canceled', 'failed'}
                    and evidence.get('connected', True)
                    and type(task.get('retry_generation', 0)) is int
                    and task.get('retry_generation', 0) == scope['generation']
                    and actual.get('fingerprint') == scope.get('runtime_fingerprint'))

    def _read(self, pending):
        try:
            pending.reply = self.fetch('GET', '/jobs/' + pending.scope['worker_job_key'],
                                       timeout=self.poll_timeout)
        except Exception as ex:
            pending.error = ex
        finally:
            pending.done.set()

    def _validate(self, reply, scope):
        if not isinstance(reply, dict) or reply.get('version') != STATUS_VERSION:
            return 'worker_status_provenance_unavailable'
        if reply.get('id') != scope['worker_job_key'] or reply.get('request_sha256') != scope['request_sha256']:
            return 'worker_request_mismatch'
        if reply.get('source_run') != scope['source_run']:
            return 'worker_source_run_mismatch'
        expected = scope.get('worker_runtime_code')
        if not HEX64.fullmatch(str(expected or '')) or reply.get('runtime_code') != expected:
            return 'worker_runtime_unverified'
        if not HEX32.fullmatch(str(reply.get('attempt_id', ''))):
            return 'worker_attempt_unverified'
        if type(reply.get('attempts')) is not int or reply['attempts'] < 1:
            return 'worker_attempt_unverified'
        payload = {k: v for k, v in reply.items() if k != 'status_sha256'}
        if reply.get('status_sha256') != digest(payload):
            return 'worker_status_hash_mismatch'
        if reply.get('status') == 'succeeded':
            result = reply.get('result')
            if not isinstance(result, dict) or reply.get('result_sha256') != digest(result):
                return 'worker_result_hash_mismatch'
            events = result.get('observations')
            if (result.get('status') not in {'covered', 'covered_zero', 'partial', 'failed', 'unsupported'}
                    or type(result.get('complete')) is not bool or not isinstance(events, list)
                    or len(events) > 128 or len(json.dumps(result)) > 2 * 1024 * 1024
                    or any(not isinstance(e, dict) or RESERVED_EVENT_FIELDS.intersection(e) for e in events)):
                return 'worker_result_contract_invalid'
        return None

    def _intent_error(self, job, scope):
        ids = job.get('test_intent_ids', [])
        if not isinstance(ids, list) or len(ids)>128 or len(set(ids))!=len(ids):
            return 'logical_intent_scope_mismatch'
        for ident in ids:
            try:
                intent = self.store.get(ident, 'test_intent')
                target = intent.get('scope') or {}
                if (intent['case_id'] != scope['case_id']
                        or target.get('task_id') != scope['task_id']
                        or target.get('evidence_id') != scope['evidence_id']
                        or type(target.get('generation', 0)) is not int
                        or target.get('generation', 0) != scope['generation']
                        or target.get('source_run') != scope['source_run']
                        or target.get('signature') != scope['signature']
                        or target.get('request') != fingerprint_scope(job['request'])):
                    return 'logical_intent_scope_mismatch'
            except (ValueError, KeyError, TypeError):
                return 'logical_intent_scope_mismatch'
        return None

    def _ingest(self, pending, reply):
        scope = pending.scope
        invalid = self._validate(reply, scope)
        collection_key = digest([scope['scope_digest'], reply.get('attempt_id'),
                                 reply.get('result_sha256'), reply.get('status_sha256')])
        observed_at = now()
        with self.store.tx():
            # SQLite's writer fence, not an in-process RLock alone, makes two
            # collectors and crash recovery idempotent.
            job = self.store.get(scope['job_id'], 'investigation_job')
            if job.get('collector_terminal_receipt_id') or job.get('status') == 'ingested':
                return 'duplicate'
            existing = self.store.db.execute(
                "SELECT id FROM records WHERE kind='receipt' AND case_id=? "
                "AND json_extract(body,'$.collection_key')=? LIMIT 1",
                (scope['case_id'], collection_key)).fetchone()
            if existing:
                return 'duplicate'
            if (job.get('collector_worker_attempt_id')
                    and job['collector_worker_attempt_id'] != reply.get('attempt_id')):
                invalid = 'worker_attempt_changed'
            invalid = invalid or self._intent_error(job, scope)
            current = not invalid and self._current(scope, job)
            disposition = ('quarantined' if invalid else 'reconciliation_required'
                           if reply.get('status') == 'execution_unknown' else
                           'ingested' if current else 'late_preserved')
            receipt = self.store.add('receipt', scope['case_id'],
                receipt_type='worker_result_collection', collector_version=VERSION,
                task_id=scope['task_id'], evidence_id=scope['evidence_id'],
                generation=scope['generation'], job_id=scope['job_id'],
                collection_key=collection_key, scope=scope,
                worker_status={k: v for k, v in reply.items() if k != 'result'},
                result=reply.get('result'), first_terminal_observed_at=observed_at,
                poll_elapsed_seconds=max(0., time.monotonic() - pending.started),
                disposition=disposition, failure_code=invalid,
                evaluation_status='unassessed',
                timing_basis='local_monotonic_poll_only_no_cross_host_latency')
            fields = {'collector_terminal_receipt_id': receipt['id'],
                      'collector_disposition': disposition}
            if disposition == 'ingested':
                result = reply.get('result') or {'status': 'failed', 'complete': False,
                    'observations': [], 'failure_code': 'worker_failed', 'error': reply.get('error')}
                from .investigation import store_tool_result
                task = self.store.get(scope['task_id'], 'task')
                evidence = self.store.get(scope['evidence_id'], 'evidence')
                ids = store_tool_result(self.controller, scope['case_id'], evidence, task, result, job['request'], bounded_lookup=True)
                fields.update(status='ingested', observation_ids=ids, result=None,
                              result_status=result['status'],
                              result_scope={k: v for k, v in result.items() if k != 'observations'},
                              ended_at=observed_at, ingested_at=now(), error=result.get('error'))
            self.store.update(job['id'], **fields)
            if disposition == 'ingested':
                from .question_engine import finish_intents
                finish_intents(self.store, scope['case_id'], self.store.get(job['id']))
        if disposition == 'ingested':
            self.controller.wake.set()
        return disposition

    def _received(self, pending, counts):
        if pending.error is not None:
            counts['poll_errors'] += 1
            return
        reply = pending.reply
        if not isinstance(reply, dict):
            counts['poll_errors'] += 1
            return
        if reply.get('status') in TERMINAL:
            counts[self._ingest(pending, reply)] += 1
        elif reply.get('status') == 'running' and not self._validate(reply, pending.scope):
            # A pinned active attempt cannot be silently replaced by a later
            # attempt, even if a restarted worker returns another valid hash.
            with self.store.tx():
                job = self.store.get(pending.scope['job_id'], 'investigation_job')
                if (job.get('collector_scope') == pending.scope
                        and not job.get('collector_worker_attempt_id')
                        and not job.get('collector_terminal_receipt_id')):
                    self.store.update(job['id'], collector_worker_attempt_id=reply['attempt_id'])

    def ingest_status(self, job, reply):
        """Shared opt-in boundary for a terminal reply obtained by legacy polling.

        It performs no network/model operation. The same writer fence handles
        a collector/legacy race, and late/quarantined results are not adopted by
        the old polling path after this method returns.
        """
        if not self.enabled or not isinstance(reply, dict) or reply.get('status') not in TERMINAL:
            return 'unverified_scope'
        try:
            scope = self._pin(job)
        except (ValueError, KeyError, TypeError):
            return 'unverified_scope'
        if scope is None:
            return 'duplicate'
        return self._ingest(_Read(scope=scope, started=time.monotonic()), reply)

    def collect_once(self):
        counts = {'started': 0, 'ingested': 0, 'late_preserved': 0,
                  'quarantined': 0, 'reconciliation_required': 0,
                  'duplicate': 0, 'poll_errors': 0, 'inflight': 0}
        if not self.enabled or self.stop.is_set() or not self._cycle_lock.acquire(blocking=False):
            return counts
        started = time.monotonic()
        deadline = started + self.wallclock_budget
        try:
            # First drain completed readers. Never wait for their worker job.
            for job_id, pending in list(self._reads.items()):
                if time.monotonic() >= deadline:
                    break
                if not pending.done.is_set():
                    continue
                self._reads.pop(job_id)
                self._received(pending, counts)
            for job in self._candidates():
                if time.monotonic() >= deadline or counts['started'] >= self.max_jobs:
                    break
                if len(self._reads) >= self.max_inflight:
                    break
                if (job['id'] in self._reads or not job.get('dispatched_at')
                        or job.get('status') == 'ingested' or job.get('collector_terminal_receipt_id')):
                    continue
                try:
                    scope = self._pin(job)
                except (ValueError, KeyError, TypeError):
                    continue
                if scope is None:
                    continue
                pending = _Read(scope=scope, started=time.monotonic())
                self._reads[job['id']] = pending
                threading.Thread(target=self._read, args=(pending,), daemon=True,
                                 name='issued-worker-status-read').start()
                counts['started'] += 1
            # Small bounded wait allows an immediate completed worker reply to
            # be ingested in this cycle. The Store/model locks are not held.
            while self._reads and time.monotonic() < deadline:
                completed = [(jid, p) for jid, p in self._reads.items() if p.done.is_set()]
                if not completed:
                    self.stop.wait(min(.005, max(0., deadline - time.monotonic())))
                    continue
                for jid, pending in completed:
                    if time.monotonic() >= deadline:
                        break
                    self._reads.pop(jid)
                    self._received(pending, counts)
            counts['inflight'] = len(self._reads)
            return counts
        finally:
            self.last_cycle = {**counts, 'version':VERSION, 'observed_at':now(),
                               'elapsed_seconds':max(0.,time.monotonic()-started),
                               'wallclock_budget_seconds':self.wallclock_budget,
                               'evaluation_status':'not_performed_by_collector'}
            self._cycle_lock.release()
