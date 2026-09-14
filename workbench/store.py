import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

REPORT_SCOPE_KINDS=('case','evidence','coverage','task','observation','claim','judgment','dossier',
                    'dossier_batch','investigation_job','receipt','lineage','hypothesis')

def now():
    return datetime.now(timezone.utc).isoformat()


def uid(prefix):
    return f"{prefix}-{uuid4().hex[:12]}"


class Store:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS records (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, case_id TEXT NOT NULL,
          created_at TEXT NOT NULL, body TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_records_case_kind ON records(case_id,kind);
        CREATE TABLE IF NOT EXISTS fingerprints (
          case_id TEXT NOT NULL, fingerprint TEXT NOT NULL, task_id TEXT NOT NULL,
          PRIMARY KEY(case_id,fingerprint));
        PRAGMA user_version=1;
        ''')
        # Database triggers cover every connection, including disconnect/reconnect
        # transitions that would be invisible to a final content-only comparison.
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS ui_revisions (case_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
        CREATE TRIGGER IF NOT EXISTS ui_revision_insert AFTER INSERT ON records BEGIN
          INSERT INTO ui_revisions VALUES(NEW.case_id,1) ON CONFLICT(case_id) DO UPDATE SET revision=revision+1;
        END;
        CREATE TRIGGER IF NOT EXISTS ui_revision_update AFTER UPDATE ON records WHEN NEW.body<>OLD.body BEGIN
          INSERT INTO ui_revisions VALUES(NEW.case_id,1) ON CONFLICT(case_id) DO UPDATE SET revision=revision+1;
        END;
        CREATE TRIGGER IF NOT EXISTS ui_revision_delete AFTER DELETE ON records BEGIN
          INSERT INTO ui_revisions VALUES(OLD.case_id,1) ON CONFLICT(case_id) DO UPDATE SET revision=revision+1;
        END;
        ''')
        kinds=','.join("'"+kind+"'" for kind in REPORT_SCOPE_KINDS)
        self.db.executescript(f'''
        CREATE TABLE IF NOT EXISTS report_revisions (case_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
        CREATE TRIGGER IF NOT EXISTS report_revision_insert AFTER INSERT ON records
          WHEN NEW.kind IN ({kinds}) BEGIN
          INSERT INTO report_revisions VALUES(NEW.case_id,1)
          ON CONFLICT(case_id) DO UPDATE SET revision=revision+1;
        END;
        CREATE TRIGGER IF NOT EXISTS report_revision_update AFTER UPDATE ON records
          WHEN (OLD.kind IN ({kinds}) OR NEW.kind IN ({kinds})) AND (NEW.body<>OLD.body OR NEW.case_id<>OLD.case_id OR NEW.kind<>OLD.kind) BEGIN
          INSERT INTO report_revisions VALUES(OLD.case_id,1)
          ON CONFLICT(case_id) DO UPDATE SET revision=revision+1;
          INSERT INTO report_revisions SELECT NEW.case_id,1 WHERE NEW.case_id<>OLD.case_id
          ON CONFLICT(case_id) DO UPDATE SET revision=revision+1;
        END;
        CREATE TRIGGER IF NOT EXISTS report_revision_delete AFTER DELETE ON records
          WHEN OLD.kind IN ({kinds}) BEGIN
          INSERT INTO report_revisions VALUES(OLD.case_id,1)
          ON CONFLICT(case_id) DO UPDATE SET revision=revision+1;
        END;
        ''')

    def ui_revision(self,case_id):
        with self.lock:
            row=self.db.execute('SELECT revision FROM ui_revisions WHERE case_id=?',(case_id,)).fetchone()
            return row[0] if row else 0

    def report_revision(self,case_id):
        with self.lock:
            row=self.db.execute('SELECT revision FROM report_revisions WHERE case_id=?',(case_id,)).fetchone()
            return row[0] if row else 0

    @contextmanager
    def tx(self):
        with self.lock:
            self.db.execute('BEGIN IMMEDIATE')
            try:
                yield self
                self.db.execute('COMMIT')
            except BaseException:
                self.db.execute('ROLLBACK')
                raise

    def add(self, kind, case_id, **body):
        record = dict(id=uid(kind.upper()), kind=kind, case_id=case_id, created_at=now(), **body)
        with self.lock:
            self.db.execute('INSERT INTO records VALUES (?,?,?,?,?)', (record['id'],kind,case_id,record['created_at'],json.dumps(record,ensure_ascii=False)))
        return record

    def get(self, id, kind=None):
        with self.lock:
            row = self.db.execute('SELECT body FROM records WHERE id=?', (id,)).fetchone()
        if not row:
            raise ValueError('항목을 찾을 수 없습니다.')
        item = json.loads(row[0])
        if kind and item['kind'] != kind:
            raise ValueError('항목 종류가 일치하지 않습니다.')
        return item

    def list(self, kind, case_id=None):
        with self.lock:
            if case_id is None:
                rows = self.db.execute('SELECT body FROM records WHERE kind=? ORDER BY created_at,id', (kind,)).fetchall()
            else:
                rows = self.db.execute('SELECT body FROM records WHERE case_id=? AND kind=? ORDER BY created_at,id', (case_id,kind)).fetchall()
        return [json.loads(r[0]) for r in rows]

    def update(self, id, **changes):
        with self.lock:
            item = self.get(id)
            if item['kind'] in ('observation','receipt','report','audit','judgment','review_input','review_diagnostic'):
                raise ValueError('불변 기록은 수정할 수 없습니다.')
            item.update(changes)
            self.db.execute('UPDATE records SET body=?,case_id=? WHERE id=?', (json.dumps(item,ensure_ascii=False),item['case_id'],id))
            return item

    def audit(self, case_id, action, **detail):
        return self.add('audit',case_id,action=action,detail=detail)
