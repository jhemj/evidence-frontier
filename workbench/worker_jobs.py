"""Single-worker durable job ledger. HTTP request lifetime is not job lifetime.

The deployment intentionally runs one worker process. Completed manifests are
published atomically; a retry reuses the stable job key and cannot publish twice.
Interrupted read-only jobs may retry, but are never reported as completed.
"""
import hashlib
import json
import os
import sqlite3
import threading
import subprocess
import sys
from pathlib import Path
from .store import now
from .worker import execute
from .evidence_access import metadata
from .models import InvestigationToolRequest
from uuid import uuid4


class WorkerJobs:
    def __init__(self, root, evidence_root):
        from .runtime_contract import code_identity
        self.runtime_code=code_identity()
        self.root = Path(root) / 'jobs'; self.root.mkdir(parents=True, exist_ok=True)
        self.process_lock = (self.root/'worker.lock').open('a+b')
        if os.name == 'nt':
            import msvcrt
            self.process_lock.seek(0); self.process_lock.write(b'0'); self.process_lock.flush(); self.process_lock.seek(0)
            msvcrt.locking(self.process_lock.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.process_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.evidence_root = evidence_root
        self.lock = threading.RLock(); self.wake = threading.Event(); self.stop = threading.Event()
        self.db = sqlite3.connect(self.root / 'jobs.sqlite3', check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL, updated_at TEXT NOT NULL, error TEXT); CREATE TABLE IF NOT EXISTS attempts (id TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner TEXT NOT NULL, started_at TEXT NOT NULL);')
        self.owner = f'{os.getenv("HOSTNAME", "local")}:{os.getpid()}:{uuid4().hex}'
        self.db.execute('CREATE TABLE IF NOT EXISTS active_attempts (job_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL)')
        # A vanished HTTP process is not proof that its child tool stopped.
        # Recover a committed envelope; otherwise fail closed, never auto-rerun.
        for row in self.db.execute("SELECT * FROM jobs WHERE status='running'").fetchall():
            output = self.root / (row['id'] + '.json')
            try:
                envelope = json.loads(output.read_bytes())
                valid = envelope['request_sha256'] == hashlib.sha256(row['request'].encode()).hexdigest()
                valid &= envelope['result_sha256'] == self.digest(envelope['result'])
                attempt = self.db.execute('SELECT attempt_id FROM active_attempts WHERE job_id=?',(row['id'],)).fetchone()
                valid &= bool(attempt and attempt[0] == envelope.get('attempt_id'))
                valid &= envelope.get('runtime_code')==self.runtime_code
            except (OSError, ValueError, KeyError): valid = False
            self.db.execute('UPDATE jobs SET status=?,error=? WHERE id=?',
                            ('succeeded' if valid else 'execution_unknown', None if valid else '작업자 재시작: 이전 실행 생존 여부 확인 필요. 자동 재실행하지 않음', row['id']))
        self.thread = threading.Thread(target=self.loop, daemon=True); self.thread.start()

    def submit(self, body):
        request = body.model_dump(); identity = request.pop('job_key')
        info = metadata(self.evidence_root, request['path'])
        if info['signature'] != request['signature']: raise ValueError('원본 구성이 변경되었습니다.')
        with self.lock:
            old = self.db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone()
            encoded = json.dumps(request, sort_keys=True)
            if old and old['request'] != encoded: raise ValueError('동일 작업 키에 다른 요청이 지정되었습니다.')
            if not old:
                self.db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,NULL)', (identity, encoded, 'queued', 0, now()))
        self.wake.set(); return self.status(identity)

    @staticmethod
    def digest(value):
        return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def status(self, identity):
        with self.lock: row = self.db.execute('SELECT * FROM jobs WHERE id=?', (identity,)).fetchone()
        if not row: raise ValueError('작업을 찾지 못했습니다.')
        state = {k: row[k] for k in ('id', 'status', 'attempts', 'updated_at', 'error')}
        output = self.root / (identity + '.json')
        if row['status'] == 'succeeded':
            envelope = json.loads(output.read_bytes())
            if envelope.get('runtime_code')!=self.runtime_code:raise ValueError('실행 조합이 다른 worker 결과 재사용 차단')
            if envelope['result_sha256'] != self.digest(envelope['result']): raise ValueError('작업 결과 해시 불일치')
            state.update(result=envelope['result'], result_sha256=envelope['result_sha256'])
        return state

    def loop(self):
        while not self.stop.is_set():
            with self.lock:
                row = self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY updated_at LIMIT 1").fetchone()
                if row:
                    attempt_id = uuid4().hex
                    self.db.execute('BEGIN IMMEDIATE')
                    self.db.execute("UPDATE jobs SET status='running',attempts=attempts+1,updated_at=? WHERE id=?", (now(), row['id']))
                    self.db.execute('INSERT INTO attempts VALUES (?,?,?,?)', (attempt_id, row['id'], self.owner, now()))
                    self.db.execute('INSERT OR REPLACE INTO active_attempts VALUES (?,?)', (row['id'],attempt_id))
                    self.db.execute('COMMIT')
            if not row:
                self.wake.wait(1); self.wake.clear(); continue
            identity = row['id']; body = json.loads(row['request'])
            try:
                if metadata(self.evidence_root, body['path'])['signature'] != body['signature']: raise ValueError('원본 구성이 변경되었습니다.')
                if body['action'] == 'investigation_tool':
                    request = InvestigationToolRequest(**body['investigation'])
                    if request.evidence_path != body['path']: raise ValueError('작업 증거 경로 불일치')
                    input_path=self.root/(attempt_id+'.request.json'); output_path=self.root/(attempt_id+'.output.json')
                    input_path.write_text(request.model_dump_json(),encoding='utf-8')
                    process=subprocess.run([sys.executable,'-m','workbench.tool_runner',str(input_path),str(output_path)],
                        capture_output=True,timeout=180,env={**os.environ,'EVIDENCE_ROOT':str(self.evidence_root),'ANALYSIS_ROOT':str(self.root.parent)})
                    if process.returncode:raise ValueError(process.stderr.decode(errors='replace')[-1500:])
                    result=json.loads(output_path.read_bytes())
                else: result = execute(self.evidence_root, body['action'], body['path'])
                if metadata(self.evidence_root, body['path'])['signature'] != body['signature']: raise ValueError('실행 중 원본 구성이 변경되었습니다.')
                envelope = {'result': result, 'result_sha256': self.digest(result),
                            'runtime_code':self.runtime_code,
                            'attempt_id':attempt_id,
                            'request_sha256': hashlib.sha256(row['request'].encode()).hexdigest()}
                content = json.dumps(envelope, ensure_ascii=False).encode()
                temp = self.root / (identity + '.tmp')
                with temp.open('wb') as stream: stream.write(content); stream.flush(); os.fsync(stream.fileno())
                temp.replace(self.root / (identity + '.json'))
                with self.lock:
                    current = self.db.execute('SELECT attempt_id FROM active_attempts WHERE job_id=?',(identity,)).fetchone()
                    if not current or current[0] != attempt_id: raise ValueError('이전 실행의 결과 채택 차단')
                    self.db.execute("UPDATE jobs SET status='succeeded',updated_at=?,error=NULL WHERE id=?", (now(), identity))
            except Exception as ex:
                with self.lock: self.db.execute("UPDATE jobs SET status='failed',updated_at=?,error=? WHERE id=?", (now(), str(ex), identity))
