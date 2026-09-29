import json

import pytest

from scripts import luna_test_provider as luna


class FakeProcess:
    def __init__(self, output, returncode=0, error=None):
        self.output = output
        self.returncode = returncode
        self.error = error

    def communicate(self, input=None, timeout=None):
        if self.error:
            error, self.error = self.error, None
            raise error
        # stdout is assigned by the factory below.
        self.stdout.write(self.output)
        return b"", b""

    def kill(self):
        self.returncode = -9


def fake_popen_factory(output, returncode=0, error=None, seen=None):
    def factory(args, **kwargs):
        if seen is not None:
            seen.append((args, kwargs))
        proc = FakeProcess(output, returncode, error)
        proc.stdout = kwargs["stdout"]
        return proc

    return factory


def test_valid_jsonl_returns_final_message_and_receipt(tmp_path, monkeypatch):
    seen = []
    output = b'{"type":"thread.started"}\n{"type":"agent_message","text":"Luna result","usage":{"output_tokens":7}}\n'
    monkeypatch.setattr(luna, "_cli_version", lambda: "codex 1.2.3")
    monkeypatch.setattr(luna.subprocess, "Popen", fake_popen_factory(output, seen=seen))
    content, receipt = luna.infer([{"role": "user", "content": "hello"}], {"type": "object"}, tmp_path)
    assert content == "Luna result"
    assert receipt["test_only"] is True
    assert receipt["actual_model"] == "gpt-6-luna"
    assert receipt["cli_version"] == "codex 1.2.3"
    assert receipt["usage"] == {"output_tokens": 7}
    args, kwargs = seen[0]
    assert kwargs["shell"] is False and kwargs["cwd"] != str(tmp_path)
    assert "--sandbox" in args and "read-only" in args
    assert "--disable" in args and "shell_tool" in args
    assert "--json" in args
    assert args[-1] == "-"
    assert kwargs["stderr"] is not luna.subprocess.STDOUT


@pytest.mark.parametrize(
    "output",
    [
        b'{"type":"tool_call","name":"shell"}\n',
        b"not json\n",
    ],
)
def test_tool_attempt_or_malformed_output_is_rejected(tmp_path, monkeypatch, output):
    monkeypatch.setattr(luna, "_cli_version", lambda: "codex test")
    monkeypatch.setattr(luna.subprocess, "Popen", fake_popen_factory(output))
    with pytest.raises(luna.LunaProviderError):
        luna.infer([], {}, tmp_path)


def test_nonzero_exit_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(luna, "_cli_version", lambda: "codex test")
    monkeypatch.setattr(luna.subprocess, "Popen", fake_popen_factory(b"{}\n", returncode=3))
    with pytest.raises(luna.LunaProviderError, match="exit code"):
        luna.infer([], {}, tmp_path)


def test_timeout_is_killed_and_rejected(tmp_path, monkeypatch):
    from workbench.provider import ModelServiceError
    monkeypatch.setattr(luna, "_cli_version", lambda: "codex test")
    monkeypatch.setattr(luna.subprocess, "Popen", fake_popen_factory(b"", error=__import__("subprocess").TimeoutExpired(["codex"], 180)))
    with pytest.raises(ModelServiceError, match="timed out"):
        luna.infer([], {}, tmp_path)


def test_strict_transport_schema_preserves_nullability_and_validation_bounds():
    source = {"type": "object", "properties": {
        "optional": {"anyOf": [{"type": "integer", "minimum": 0}, {"type": "null"}], "default": None},
        "nested": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 8}}},
    }}
    strict = luna._strict_schema(source)
    assert strict["required"] == ["optional", "nested"]
    assert strict["additionalProperties"] is False
    assert "default" not in strict["properties"]["optional"]
    assert strict["properties"]["optional"]["anyOf"] == source["properties"]["optional"]["anyOf"]
    assert strict["properties"]["nested"]["required"] == ["text"]
    assert source["properties"]["optional"]["default"] is None


def test_reference_annotations_are_transport_only_and_constraints_never_dropped():
    source={'$ref':'#/$defs/Finding','description':'field guidance','title':'Title'}
    assert luna._strict_schema(source)=={'$ref':'#/$defs/Finding'}
    assert source['description']=='field guidance'
    with pytest.raises(luna.LunaProviderError,match='refusing to weaken'):
        luna._strict_schema({'$ref':'#/$defs/Finding','maxLength':20})


def test_tagged_union_transport_preserves_exclusive_modes_and_source_constraints():
    from copy import deepcopy
    from workbench.models import WorkingReview
    source=WorkingReview.model_json_schema();before=deepcopy(source)
    strict=luna._strict_schema(source)
    original=source['properties']['source_selections']['items']
    item=strict['properties']['source_selections']['items']
    assert item['anyOf']==original['oneOf']
    assert 'oneOf' not in item and 'discriminator' not in item
    for name in ('SourceExcerptChoice','SourceMetadataChoice'):
        body=strict['$defs'][name]
        assert body['properties']['mode']['const']==source['$defs'][name]['properties']['mode']['const']
        assert body['additionalProperties'] is False
        assert 'mode' in body['required']
    excerpt=strict['$defs']['SourceExcerptChoice']['properties']
    assert excerpt['passages']==luna._strict_schema(source['$defs']['SourceExcerptChoice']['properties']['passages'])
    assert source==before


@pytest.mark.parametrize('defect',['overlap','optional_tag','unresolved','untagged','cycle'])
def test_nonexclusive_or_unprovable_union_fails_closed(defect):
    def branch(mode):
        return {'type':'object','properties':{'mode':{'type':'string','const':mode}},'required':['mode']}
    source={'$defs':{'A':branch('a'),'B':branch('b')},'type':'object',
        'properties':{'choice':{'discriminator':{'propertyName':'mode'},
            'oneOf':[{'$ref':'#/$defs/A'},{'$ref':'#/$defs/B'}]}}}
    if defect=='overlap':source['$defs']['B']['properties']['mode']['const']='a'
    if defect=='optional_tag':source['$defs']['B']['required']=[]
    if defect=='unresolved':source['properties']['choice']['oneOf'][1]['$ref']='#/$defs/Missing'
    if defect=='untagged':source['properties']['choice'].pop('discriminator')
    if defect=='cycle':source['$defs']['B']={'$ref':'#/$defs/B'}
    with pytest.raises(luna.LunaProviderError,match='refusing to weaken'):
        luna._strict_schema(source)
