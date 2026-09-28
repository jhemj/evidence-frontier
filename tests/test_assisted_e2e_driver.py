import json
import os
from pathlib import Path

import pytest

from scripts import run_assisted_e2e as driver
from workbench.provider import Provider
from workbench.models import Falsification


@pytest.fixture(autouse=True)
def isolate_driver_process_state():
    """The driver intentionally patches process-global Provider/env state."""
    environment = dict(os.environ)
    generate = Provider.generate
    response = Provider.response
    yield
    os.environ.clear()
    os.environ.update(environment)
    Provider.generate = generate
    Provider.response = response


def test_environment_pins_transport_and_unsets_relay(tmp_path, monkeypatch):
    args = driver.build_parser().parse_args([
        "--data-root", str(tmp_path / "data"), "--analysis-root", str(tmp_path / "analysis"),
        "--worker-source", "source-pin", "--worker-image", "image-pin",
        "--model-digest", "model-pin", "--model-url", "http://127.0.0.1:11434",
    ])
    monkeypatch.setenv("MODEL_RELAY_URL", "http://relay.invalid")
    values = driver.configure_environment(args)
    assert values["WORKER_URL"] == "http://127.0.0.1:8776"
    assert values["FRONTIER_WORKER_SOURCE_SHA256"] == "source-pin"
    assert values["FRONTIER_WORKER_IMAGE"] == "image-pin"
    assert values["FRONTIER_MODEL_DIGEST"] == "model-pin"
    assert "MODEL_RELAY_URL" not in __import__("os").environ


def test_only_judgment_is_diverted_and_receipt_manifest_are_test_only(tmp_path, monkeypatch):
    calls = []

    def original(self, question, pack, role="analyst"):
        calls.append(role)
        payload = {"messages": [{"role": "system", "content": "same prompt"}]}
        if role == "judgment":
            response = self.response("/api/chat", payload)
            assert response["message"]["content"] == "{\"summary\":\"ok\"}"
        return {"summary": "ok"}, {"model": "ollama-model", "role": role, "generation_settings": {"protocol": "ollama"}}

    monkeypatch.setattr(Provider, "generate", original)
    monkeypatch.setattr(driver, "infer", lambda messages, schema, root: ("{\"summary\":\"ok\"}", {"usage": {"output_tokens": 2}}))
    identity = driver.install_assisted_provider(tmp_path)
    provider = object.__new__(Provider)
    provider.response = lambda path, payload: {"message": {"content": "never"}}
    judgment, receipt = provider.generate("question", {}, role="judgment")
    other, other_receipt = provider.generate("question", {}, role="investigator")
    assert calls == ["judgment", "investigator"]
    assert receipt["model"] == "gpt-5.6-luna" and receipt["test_only"] is True
    assert receipt["generation_settings"]["transport"] == "codex-luna-test-only"
    assert receipt["generation_settings"]["requested_ollama"]["protocol"] == "ollama"
    assert other_receipt["model"] == "ollama-model"
    manifest = json.loads(Path(identity["manifest"]).read_text())
    assert manifest["test_only"] is True and len(manifest["transports"]) == 1
    assert manifest["transports"][0]["driver_sha256"] == identity["driver_sha256"]
    assert list((tmp_path / "luna-transport-receipts").glob("*.json"))


def test_luna_failure_does_not_fallback_to_ollama(tmp_path, monkeypatch):
    calls = []

    def original(self, question, pack, role="analyst"):
        calls.append("ollama")
        self.response("/api/chat", {"messages": []})
        return {}, {}

    monkeypatch.setattr(Provider, "generate", original)
    def fail(*args, **kwargs):
        raise RuntimeError("Luna unavailable")
    monkeypatch.setattr(driver, "infer", fail)
    driver.install_assisted_provider(tmp_path)
    provider = object.__new__(Provider)
    provider.response = lambda path, payload: {"message": {"content": "unused"}}
    try:
        provider.generate("question", {}, role="judgment")
    except RuntimeError as exc:
        assert "Luna unavailable" in str(exc)
    else:
        raise AssertionError("expected Luna failure")
    assert calls == ["ollama"]


def test_restart_requires_same_adapter_pin_and_preserves_manifest(tmp_path, monkeypatch):
    first = driver.install_assisted_provider(tmp_path)
    before = json.loads(Path(first["manifest"]).read_text())
    second = driver.install_assisted_provider(tmp_path)
    after = json.loads(Path(second["manifest"]).read_text())
    assert after == before
    after["adapter_sha256"] = "changed"
    Path(first["manifest"]).write_text(json.dumps(after), encoding="utf-8")
    try:
        driver.install_assisted_provider(tmp_path)
    except ValueError as exc:
        assert "pin mismatch" in str(exc)
    else:
        raise AssertionError("pin mismatch must fail closed")


def test_luna_failure_is_audited_without_fallback(tmp_path, monkeypatch):
    def original(self, question, pack, role="analyst"):
        self.response("/api/chat", {"messages": []})
        return {}, {}

    monkeypatch.setattr(Provider, "generate", original)
    monkeypatch.setattr(driver, "infer", lambda *args: (_ for _ in ()).throw(RuntimeError("offline")))
    driver.install_assisted_provider(tmp_path)
    provider = object.__new__(Provider)
    provider.response = lambda path, payload: {"message": {"content": "unused"}}
    try:
        provider.generate("q", {}, role="judgment")
    except RuntimeError:
        pass
    rows = [json.loads(p.read_text()) for p in (tmp_path / "luna-transport-receipts").glob("*.json")]
    assert any(row.get("error_type") == "transport" and row.get("model") == "gpt-5.6-luna" for row in rows)


def test_pydantic_gate_failure_is_audited(tmp_path, monkeypatch):
    class GateError(ValueError):
        category = "output_schema"

    def original(self, question, pack, role="analyst"):
        self.response("/api/chat", {"messages": []})
        raise GateError("schema rejected")

    monkeypatch.setattr(Provider, "generate", original)
    monkeypatch.setattr(driver, "infer", lambda *args: ("{}", {"usage": {"output_tokens": 1}}))
    driver.install_assisted_provider(tmp_path)
    provider = object.__new__(Provider)
    provider.response = lambda path, payload: {"message": {"content": "unused"}}
    try:
        provider.generate("q", {}, role="judgment")
    except GateError:
        pass
    rows = [json.loads(p.read_text()) for p in (tmp_path / "luna-transport-receipts").glob("*.json")]
    assert any(row.get("error_type") == "pydantic_gate" and row.get("reasoning_effort") == "low" for row in rows)


def test_optional_falsifier_uses_falsification_schema_and_separate_transport_root(tmp_path, monkeypatch):
    seen = []
    def original(self, question, pack, role="analyst"):
        response = self.response("/api/chat", {"messages": []})
        return {}, {"model": "ollama", "role": role, "generation_settings": {}}
    monkeypatch.setattr(Provider, "generate", original)
    monkeypatch.setattr(driver, "infer", lambda messages, schema, root: (seen.append((schema, root)) or ("{}", {"usage": {}})))
    transport = tmp_path / "transport"
    shared = tmp_path / "analysis"
    shared.mkdir()
    old_manifest = shared / "assisted-e2e-manifest.json"
    old_manifest.write_text('{"prior_iteration": true}')
    identity = driver.install_assisted_provider(shared, luna_roles=("judgment", "falsifier"), transport_root=transport)
    provider = object.__new__(Provider); provider.response = lambda *args: {"message": {"content": "unused"}}
    provider.generate("q", {}, role="falsifier")
    assert seen and seen[0][0] == Falsification.model_json_schema() and seen[0][1] == transport.resolve()
    assert old_manifest.read_text() == '{"prior_iteration": true}'
    assert identity['manifest'] == transport / 'assisted-e2e-manifest.json'
    manifest = json.loads(identity['manifest'].read_text())
    assert manifest["luna_roles"] == ["falsifier", "judgment"]
    assert list((transport / "luna-transport-receipts").glob("*.json"))


def test_invalid_luna_role_and_manifest_role_pin_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        driver.install_assisted_provider(tmp_path / "a", luna_roles=("investigator",))
    first = driver.install_assisted_provider(tmp_path / "b", luna_roles=("judgment",))
    data = json.loads(Path(first["manifest"]).read_text()); data["luna_roles"] = ["falsifier"]
    Path(first["manifest"]).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="pin mismatch"):
        driver.install_assisted_provider(tmp_path / "b", luna_roles=("judgment",))
