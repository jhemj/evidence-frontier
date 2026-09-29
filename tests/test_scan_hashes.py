import hashlib

from workbench.linux_scan import hash_run_files


def test_hash_run_files_streams_large_ndjson_without_read_bytes(tmp_path, monkeypatch):
    run = tmp_path / 'RUN-test'
    run.mkdir()
    payload = b'{"event":"x"}\n' * 500_000
    artifact = run / 'events.ndjson'
    artifact.write_bytes(payload)
    (run / 'progress.tmp').write_bytes(b'ignore')

    def forbidden_read_bytes(self):
        raise AssertionError('large artifact must be hashed incrementally')

    monkeypatch.setattr(type(artifact), 'read_bytes', forbidden_read_bytes)

    hashes = hash_run_files(run)

    assert hashes == {'events.ndjson': hashlib.sha256(payload).hexdigest()}
