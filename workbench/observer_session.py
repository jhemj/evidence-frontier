"""Bounded read-only observer sampling, independent of the investigation controller.

The caller supplies a query-only capture function. No model, Store or controller
is imported. Cache contains only an identity/sequence journal, not source data.
"""
from collections import OrderedDict, deque
import json
import os
from pathlib import Path
import tempfile
import threading
import time

from .observer_view import digest, validate


class LiveObserver:
    def __init__(self, capture, *, case_id, run_id, source_binding, cache_dir,
                 interval=30, clock=time.monotonic):
        if interval < 10 or not source_binding:
            raise ValueError('A binding and sampling interval >= 10 seconds are required')
        self.capture = capture
        self.identity = {'case_id': case_id, 'run_id': run_id, 'data_mode': 'live', 'source_binding': source_binding}
        self.interval, self.clock = interval, clock
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.journal = self.cache / 'sequence.json'
        self.sequence = 0
        if self.journal.exists():
            record = json.loads(self.journal.read_text())
            if record['identity'] != self.identity:
                raise ValueError('Observer cache belongs to a different source')
            self.sequence = record['sequence']
        self.views, self.costs = OrderedDict(), deque(maxlen=120)
        self.activity = None
        self.semantic_hash = None
        self.last_success = None
        self.last_success_clock = None
        self.error = None
        self.lease_until = clock() + interval * 2
        self.lock, self.sample_lock = threading.Lock(), threading.Lock()
        self.stop_event, self.thread = threading.Event(), None
        self.source_position = None

    def _persist_sequence(self, sequence):
        # Isolated observer cache only; never touch the source DB or saved reports.
        fd, name = tempfile.mkstemp(prefix='.sequence-', dir=self.cache)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump({'identity': self.identity, 'sequence': sequence}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.journal)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def sample(self):
        if not self.sample_lock.acquire(blocking=False):
            return False
        start, cpu = self.clock(), time.thread_time()
        try:
            view = validate(self.capture(self.sequence + 1))
            if any(view['envelope'].get(k) != v for k, v in self.identity.items()):
                raise ValueError('Live source identity changed')
            # Working-state changes alone must not invalidate the judgment snapshot.
            semantic = digest({k: v for k, v in view.items() if k not in ('envelope', 'activity')})
            payload_bytes = len(json.dumps(view, ensure_ascii=False).encode())
            changed = semantic != self.semantic_hash
            if changed:
                self._persist_sequence(view['envelope']['sequence'])
            with self.lock:
                if changed:
                    self.sequence = view['envelope']['sequence']
                    self.semantic_hash = semantic
                    self.views[view['envelope']['projection_revision']] = view
                    while len(self.views) > 6:
                        self.views.popitem(last=False)
                self.activity = {**view['activity'], **self.identity, 'source_revision': view['envelope']['source_revision']}
                self.source_position = view['envelope']['ledger_position']
                self.last_success = view['envelope']['captured_at']
                self.last_success_clock = self.clock()
                self.error = None
                self.costs.append({'wall_ms': round((self.clock()-start)*1000, 2),
                                   'thread_cpu_ms': round((time.thread_time()-cpu)*1000, 2),
                                   'snapshot_bytes': payload_bytes,
                                   'judgment_changed': changed})
            return True
        except Exception as exc:
            with self.lock:
                # No raw exception (potential source path/text) in public status.
                self.error = type(exc).__name__
            return False
        finally:
            self.sample_lock.release()

    def session(self):
        with self.lock:
            now = self.clock()
            self.lease_until = now + self.interval * 2.5
            views = list(self.views.values())
            stale = self.last_success_clock is None or now-self.last_success_clock > self.interval*2+5
            return {'mode': 'read_only_live', 'latest': views[-1]['envelope'] if views else None,
                    'snapshots': [v['envelope'] for v in views], 'activity': self.activity,
                    'source_status': 'unavailable' if not views else 'stale' if self.error or stale else 'observed',
                    'observed_at': self.last_success, 'source_position': self.source_position,
                    'error_kind': self.error, 'poll_seconds': self.interval,
                    'automatic_model_calls': 0, 'cost_samples': list(self.costs)[-10:],
                    'history_scope': '최근 판단 스냅샷 최대 6개 · 원장 이력을 대체하지 않음'}

    def snapshot(self, revision):
        with self.lock:
            return self.views.get(revision)

    def all_views(self):
        with self.lock:
            return list(self.views.values())

    def start(self):
        if self.thread:
            raise RuntimeError('Observer already started')
        # Enforce one sampler per cache across processes (Linux observer host).
        import fcntl
        self.process_lock = (self.cache / 'sampler.lock').open('a')
        try:
            fcntl.flock(self.process_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            self.process_lock.close()
            raise
        if self.journal.exists():
            record = json.loads(self.journal.read_text())
            if record['identity'] != self.identity:
                self.process_lock.close()
                raise ValueError('Observer cache source changed')
            self.sequence = max(self.sequence, record['sequence'])

        def run():
            while not self.stop_event.is_set():
                if self.clock() < self.lease_until:
                    self.sample()
                self.stop_event.wait(self.interval)
        self.thread = threading.Thread(target=run, daemon=True, name='read-only-observer')
        self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=5)
            if not self.thread.is_alive():
                self.process_lock.close()
