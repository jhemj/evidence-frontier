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


def test_ollama_only_driver_does_not_install_external_transport(tmp_path,monkeypatch):
    monkeypatch.setattr(driver,'install_assisted_provider',
        lambda *a,**kw:pytest.fail('Ollama-only must not install Luna'))
    monkeypatch.setattr('workbench.api.create_app',lambda *a,**kw:'local-app')
    calls=[]
    monkeypatch.setattr('uvicorn.run',lambda *a,**kw:calls.append((a,kw)))
    assert driver.main(['--data-root',str(tmp_path/'data'),'--analysis-root',str(tmp_path/'analysis'),
        '--worker-source','source','--worker-image','image','--model-digest','digest',
        '--model-url','http://127.0.0.1:11434','--ollama-only'])==0
    assert calls[0][0]==('local-app',)
    assert os.environ['FRONTIER_RELEASE'].startswith('TEST_ONLY-ollama-')


def test_only_judgment_is_diverted_and_receipt_manifest_are_test_only(tmp_path, monkeypatch):
    calls = []

    def original(self, question, pack, role="analyst"):
        calls.append(role)
        payload = {"messages": [{"role": "system", "content": "same prompt"}]}
        if role == "judgment":
            response = self.response("/api/chat", payload)
            assert response["message"]["content"] == "{\"summary\":\"ok\"}"
        return {"summary": "ok"}, {"model": "ollama-model", "role": role, "generation_settings": {"protocol": "ollama"},
                "prompt_budget": {"context_tokens_requested": 16384, "prompt_characters": 99,
                                  "estimated_input_tokens": 120, "estimated_headroom": 13764,
                                  "actual_input_tokens": 17, "exact_input_tokens": 17,
                                  "actual_output_tokens": 2, "actual_headroom": 100}}

    monkeypatch.setattr(Provider, "generate", original)
    monkeypatch.setattr(driver, "infer", lambda messages, schema, root: ("{\"summary\":\"ok\"}", {"usage": {"input_tokens": 17, "output_tokens": 2}}))
    identity = driver.install_assisted_provider(tmp_path)
    provider = object.__new__(Provider)
    provider.response = lambda path, payload: {"message": {"content": "never"}}
    judgment, receipt = provider.generate("question", {}, role="judgment")
    other, other_receipt = provider.generate("question", {}, role="investigator")
    assert calls == ["judgment", "investigator"]
    assert receipt["model"] == "gpt-6-luna" and receipt["test_only"] is True
    assert receipt["generation_settings"]["transport"] == "codex-luna-test-only"
    assert receipt["generation_settings"]["requested_ollama"]["protocol"] == "ollama"
    assert receipt["prompt_budget"]["requested_ollama"]["exact_input_tokens"] is None
    assert receipt["prompt_budget"]["requested_ollama"]["actual_input_tokens"] is None
    assert receipt["prompt_budget"]["requested_ollama"]["actual_output_tokens"] is None
    assert receipt["prompt_budget"]["requested_ollama"]["actual_headroom"] is None
    assert receipt["prompt_budget"]["requested_ollama"]["production_transport_executed"] is False
    assert receipt["prompt_budget"]["requested_ollama"]["estimated_input_tokens"] == 120
    assert receipt["prompt_budget"]["actual_luna_transport"]["input_tokens"] == 17
    assert receipt["prompt_budget"]["actual_luna_transport"]["output_tokens"] == 2
    assert receipt["prompt_budget"]["actual_luna_transport"]["token_count_basis"].startswith("Luna-reported")
    assert receipt["prompt_budget"]["actual_luna_transport"]["server_retained_full_input_verified"] is False
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


@pytest.mark.parametrize('role',driver.MODEL_ROLES)
def test_all_explicit_test_roles_keep_original_wire_schema(tmp_path,monkeypatch,role):
    captured=[]
    wire={'type':'object','properties':{'marker':{'type':'string'}},'required':['marker'],'additionalProperties':False}
    def original(self,question,pack,role='analyst'):
        self.response('/api/chat',{'messages':[],'format':wire})
        return {},{}
    monkeypatch.setattr(Provider,'generate',original)
    def infer(messages,schema,root):
        captured.append(schema);return '{"marker":"ok"}',{'usage':{}}
    monkeypatch.setattr(driver,'infer',infer)
    driver.install_assisted_provider(tmp_path,luna_roles=driver.MODEL_ROLES)
    p=object.__new__(Provider);p.response=lambda *a:pytest.fail('unexpected original transport')
    _,receipt=p.generate('test',{},role=role)
    assert captured==[wire]
    assert receipt['model']=='gpt-6-luna' and receipt['role']==role


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
    assert any(row.get("error_type") == "transport" and row.get("model") == "gpt-6-luna" for row in rows)


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
        driver.install_assisted_provider(tmp_path / "a", luna_roles=("unknown_role",))
    first = driver.install_assisted_provider(tmp_path / "b", luna_roles=("judgment",))
    data = json.loads(Path(first["manifest"]).read_text()); data["luna_roles"] = ["falsifier"]
    Path(first["manifest"]).write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="pin mismatch"):
        driver.install_assisted_provider(tmp_path / "b", luna_roles=("judgment",))


def test_actual_provider_luna_does_not_probe_unavailable_ollama(tmp_path,monkeypatch):
    import httpx
    monkeypatch.setenv('FRONTIER_MODEL_DIGEST','a'*64)
    calls=[]
    def infer(messages,schema,root):
        calls.append(messages)
        return json.dumps({'alternatives':['자료 범위 미확인'],'contradicting_observation_ids':[],
                           'missing_checks':['원문 확인']}),{'usage':{'output_tokens':25}}
    monkeypatch.setattr(driver,'infer',infer)
    driver.install_assisted_provider(tmp_path,luna_roles=('falsifier',))
    p=Provider({'base_url':'http://127.0.0.1:11434','model':'fixture'})
    p.client.close()
    p.client=httpx.Client(transport=httpx.MockTransport(lambda r:pytest.fail('Luna must not touch Ollama')))
    output,receipt=p.generate('synthetic',{'observations':[]},role='falsifier')
    assert len(calls)==1 and output['contradicting_observation_ids']==[]
    assert receipt['identity_check']['model']=='gpt-6-luna'
    assert receipt['identity_check']['requested_ollama_identity_checked'] is False
    assert p.client.is_closed


def test_preparation_failure_is_not_a_luna_request(tmp_path,monkeypatch):
    def original(*args,**kwargs):raise ValueError('input rejected before generation')
    monkeypatch.setattr(Provider,'generate',original)
    monkeypatch.setattr(driver,'infer',lambda *args:pytest.fail('not reached'))
    driver.install_assisted_provider(tmp_path)
    p=object.__new__(Provider)
    with pytest.raises(ValueError):p.generate('q',{},role='judgment')
    manifest=json.loads((tmp_path/'assisted-e2e-manifest.json').read_text())
    row=manifest['transports'][0]
    assert row['transport']=='request-preparation'
    assert row['request_attempted'] is False and row['request_sha256'] is None
