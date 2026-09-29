import hashlib
import json
import sqlite3

import pytest

from scripts.prepare_assisted_worker import import_verified_proof


def make_baseline(tmp_path, *, status='succeeded', image='Synthetic 7.e01', tamper=None):
    baseline = tmp_path / 'baseline'
    jobs = baseline / 'jobs'
    jobs.mkdir(parents=True)
    with sqlite3.connect(jobs / 'jobs.sqlite3') as db:
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, request TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL, updated_at TEXT NOT NULL, error TEXT)')
        request = json.dumps({'action': 'integrity', 'path': image}, sort_keys=True)
        job_id = 'a' * 64
        db.execute('INSERT INTO jobs VALUES (?,?,?,?,?,?)', (job_id, request, status, 1, '2026-09-28T00:00:00Z', None))
        db.commit()
    result = {
        'status': 'covered', 'complete': True, 'truncated': False,
        'tool': 'libewf ewfverify', 'version': 'ewfverify test', 'verification': 'ewfverify SUCCESS',
        'manifest': [{'name': 'Synthetic 7.e01', 'sha256': '0' * 64, 'size': 1}],
        'error': None,
    }
    envelope = {
        'result': result,
        'result_sha256': hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
        'request_sha256': hashlib.sha256(request.encode()).hexdigest(),
        'runtime_code': 'test-runtime',
    }
    if tamper == 'result_sha256': envelope['result_sha256'] = 'f' * 64
    if tamper == 'request_sha256': envelope['request_sha256'] = 'f' * 64
    (jobs / (job_id + '.json')).write_text(json.dumps(envelope), encoding='utf-8')
    return baseline


def test_imports_only_a_completed_matching_proof(tmp_path):
    baseline = make_baseline(tmp_path)
    destination = tmp_path / 'destination'

    imported = import_verified_proof(baseline, destination, 'Synthetic 7.e01')

    assert imported['test_only_import'] is True
    assert imported['prior_job_id'] == 'a' * 64
    assert (destination / 'integrity-proofs').is_dir()
    assert (destination / 'proof-import.json').is_file()


@pytest.mark.parametrize('status', ['running', 'failed'])
def test_rejects_non_succeeded_jobs(tmp_path, status):
    with pytest.raises(ValueError, match='No successful baseline integrity job'):
        import_verified_proof(make_baseline(tmp_path, status=status), tmp_path / 'destination', 'Synthetic 7.e01')


@pytest.mark.parametrize('tamper', ['result_sha256', 'request_sha256'])
def test_rejects_tampered_envelope_digests(tmp_path, tamper):
    with pytest.raises(ValueError, match='digest mismatch'):
        import_verified_proof(make_baseline(tmp_path, tamper=tamper), tmp_path / 'destination', 'Synthetic 7.e01')


def test_rejects_a_job_for_a_different_image(tmp_path):
    baseline = make_baseline(tmp_path, image='Other.e01')

    with pytest.raises(ValueError, match='No successful baseline integrity job'):
        import_verified_proof(baseline, tmp_path / 'destination', 'Synthetic 7.e01')


def test_rejects_incomplete_or_failed_verification_result(tmp_path):
    baseline = make_baseline(tmp_path)
    path = baseline / 'jobs' / ('a' * 64 + '.json')
    envelope = json.loads(path.read_text())
    envelope['result']['complete'] = False
    envelope['result_sha256'] = hashlib.sha256(json.dumps(envelope['result'], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    path.write_text(json.dumps(envelope), encoding='utf-8')

    with pytest.raises(ValueError, match='completed successful EWF verification'):
        import_verified_proof(baseline, tmp_path / 'destination', 'Synthetic 7.e01')
