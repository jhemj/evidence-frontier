import hashlib
import json

from workbench.integrity_cache import load_proof, proof_cache_key, save_proof


def manifest(data: bytes = b"segment"):
    return [{"name": "image.E01", "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}]


def test_reuses_good_proof_and_labels_previous_validation(tmp_path):
    saved = save_proof(tmp_path, manifest(), "ewfverify 2025.01", "contract-1", "verification ok\n")
    loaded = load_proof(tmp_path, manifest(), "ewfverify 2025.01", "contract-1")
    assert loaded is not None
    assert loaded["proof_sha256"] == saved["proof_sha256"]
    assert loaded["reuse"]["label"] == "previous validation; not a new independent check"


def test_physical_digest_tool_and_contract_each_miss(tmp_path):
    save_proof(tmp_path, manifest(), "tool-1", "contract-1", "ok")
    assert load_proof(tmp_path, manifest(b"changed"), "tool-1", "contract-1") is None
    assert load_proof(tmp_path, manifest(), "tool-2", "contract-1") is None
    assert load_proof(tmp_path, manifest(), "tool-1", "contract-2") is None


def test_missing_and_corrupt_cache_are_misses(tmp_path):
    assert load_proof(tmp_path, manifest(), "tool", "contract") is None
    save_proof(tmp_path, manifest(), "tool", "contract", "ok")
    path = tmp_path / f"ewf-proof-{proof_cache_key(manifest(), 'tool', 'contract')}.json"
    path.write_text("{not-json", encoding="utf-8")
    assert load_proof(tmp_path, manifest(), "tool", "contract") is None


def test_proof_digest_tampering_is_a_miss(tmp_path):
    save_proof(tmp_path, manifest(), "tool", "contract", "ok")
    path = tmp_path / f"ewf-proof-{proof_cache_key(manifest(), 'tool', 'contract')}.json"
    record = json.loads(path.read_text())
    record["verification"]["stdout"] = "tampered"
    path.write_text(json.dumps(record), encoding="utf-8")
    assert load_proof(tmp_path, manifest(), "tool", "contract") is None


def test_symlink_and_oversized_cache_are_misses(tmp_path):
    save_proof(tmp_path, manifest(), "tool", "contract", "ok")
    path = tmp_path / f"ewf-proof-{proof_cache_key(manifest(), 'tool', 'contract')}.json"
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    path.unlink()
    path.symlink_to(outside)
    assert load_proof(tmp_path, manifest(), "tool", "contract") is None
    path.unlink()
    path.write_bytes(b"{" + b"x" * (2 * 1024 * 1024) + b"}")
    assert load_proof(tmp_path, manifest(), "tool", "contract") is None


def test_worker_e01_rehashes_each_run_and_reuses_successful_proof(tmp_path, monkeypatch):
    from workbench import worker

    image = tmp_path / "fixture.E01"
    image.write_bytes(b"inert EWF-like header and physical bytes")
    analysis = tmp_path / "analysis"
    monkeypatch.setenv("ANALYSIS_ROOT", str(analysis))
    original_sha = worker.sha
    hash_calls = []
    verify_calls = []

    def counted_sha(path, progress=None):
        hash_calls.append(path.name)
        return original_sha(path, progress)

    def fake_command(args, timeout=300):
        if args[:2] == ["ewfverify", "-V"]:
            return "ewfverify 9.9.9\n"
        if args[:2] == ["ewfverify", "-d"]:
            verify_calls.append(args)
            return "verification succeeded\n"
        raise AssertionError(args)

    monkeypatch.setattr(worker, "sha", counted_sha)
    monkeypatch.setattr(worker, "command", fake_command)
    first = worker.execute(tmp_path, "integrity", image.name)
    second = worker.execute(tmp_path, "integrity", image.name)
    assert first["status"] == second["status"] == "covered"
    assert hash_calls == [image.name, image.name]
    assert len(verify_calls) == 1
    assert second["verification_reuse"]["reused"] is True
    assert "previous validation" in second["verification_reuse"]["label"]


def test_worker_e01_changed_physical_bytes_forces_verification(tmp_path, monkeypatch):
    from workbench import worker

    image = tmp_path / "fixture.E01"
    image.write_bytes(b"first inert physical bytes")
    monkeypatch.setenv("ANALYSIS_ROOT", str(tmp_path / "analysis"))
    verify_calls = []

    def fake_command(args, timeout=300):
        if args[:2] == ["ewfverify", "-V"]:
            return "ewfverify fixed\n"
        if args[:2] == ["ewfverify", "-d"]:
            verify_calls.append(args)
            return "verification succeeded\n"
        raise AssertionError(args)

    monkeypatch.setattr(worker, "command", fake_command)
    worker.execute(tmp_path, "integrity", image.name)
    image.write_bytes(b"second inert physical bytes")
    changed = worker.execute(tmp_path, "integrity", image.name)
    assert changed["status"] == "covered"
    assert len(verify_calls) == 2
    assert "verification_reuse" not in changed


def test_worker_failed_verification_does_not_publish_a_proof(tmp_path, monkeypatch):
    from workbench import worker

    image = tmp_path / "fixture.E01"
    image.write_bytes(b"inert bytes")
    analysis = tmp_path / "analysis"
    monkeypatch.setenv("ANALYSIS_ROOT", str(analysis))

    def failing_command(args, timeout=300):
        if args[:2] == ["ewfverify", "-V"]:
            return "ewfverify fixed\n"
        if args[:2] == ["ewfverify", "-d"]:
            raise RuntimeError("verification failed")
        raise AssertionError(args)

    monkeypatch.setattr(worker, "command", failing_command)
    failed = worker.execute(tmp_path, "integrity", image.name)
    assert failed["status"] == "failed"
    assert not list((analysis / "integrity-proofs").glob("*.json"))
