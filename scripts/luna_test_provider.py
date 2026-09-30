"""Test-only adapter for bounded Luna assistance.

This module is intentionally outside ``workbench`` and is not a production
provider.  It invokes the local Codex CLI with every model-controlled direct
tool disabled and returns only the final agent message plus a small receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

CLI = "/usr/lib/chatgpt/resources/codex"
MODEL = "gpt-6-luna"
MAX_IO_BYTES = 8 * 1024 * 1024
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "apps", "plugins", "browser_use",
    "browser_use_external", "computer_use", "in_app_browser", "multi_agent",
    "image_generation", "view_image", "hooks", "skill_search",
    "workspace_dependencies",
)


class LunaProviderError(ValueError):
    category = 'model_protocol'

    def __init__(self, message, *, metadata=None):
        super().__init__(message)
        self.metadata = {'phase': 'output_validation', 'delivery_state': 'unknown',
                         'request_attempted': None, **(metadata or {})}


def _service_error(message, operation, request_attempted=False, **classification):
    from workbench.provider import ModelServiceError
    return ModelServiceError(message,transport='codex-luna-test-only',
        operation=operation,request_attempted=request_attempted,**classification)


def _cli_failure(raw, diagnostics, returncode):
    """Classify only explicit transport diagnostics, never agent prose.

    Unknown exits remain protocol failures. In particular, a nonzero exit is
    not enough to claim a service outage or that generation was delivered.
    Persist diagnostic hashes, not stderr (which can contain local secrets).
    """
    messages=[];codes=set();statuses=set()
    for line in raw.splitlines():
        try:
            event=json.loads(line)
        except ValueError:continue
        if not isinstance(event,dict) or event.get('type') not in ('error','turn.failed'):continue
        detail=event.get('error',event)
        if not isinstance(detail,dict):continue
        messages.append(str(detail.get('message',''))[:1500])
        codes.add(str(detail.get('code','')).lower())
        status=detail.get('status_code',detail.get('status'))
        if isinstance(status,int):statuses.add(status)
    diagnostic='\n'.join(messages)+'\n'+diagnostics.decode('utf-8',errors='replace')
    lowered=diagnostic.lower()
    metadata={'process_started':True,'exit_code':returncode,
              'stdout_sha256':_sha(raw),'stderr_sha256':_sha(diagnostics)}
    if 'workspace_discovery_failed' in codes or 'workspace routing discovery failed' in lowered:
        failure=_service_error('Luna workspace routing discovery failed','cli-discovery',False,
            category='model_service_discovery',phase='discovery',delivery_state='not_sent')
    elif statuses & {401,403} or codes & {'unauthorized','authentication_error','invalid_api_key'} or any(
            message in lowered for message in ('not logged in','authentication required','invalid api key')):
        failure=_service_error('Luna authentication requires attention','cli-authentication',True if statuses & {401,403} else None,
            category='model_authentication',phase='authentication',retryable=False,
            delivery_state='response_received' if statuses & {401,403} else 'unknown')
    elif statuses & {400,404,422} or codes & {'model_not_found','unsupported_model','invalid_request_error'}:
        failure=_service_error('Luna request/model configuration rejected','cli-generation',True if statuses else None,
            category='model_request_configuration',phase='generation',retryable=False,
            delivery_state='response_received' if statuses else 'unknown')
    elif 429 in statuses or any(status>=500 for status in statuses) or codes & {'rate_limit_exceeded','server_error','service_unavailable'}:
        failure=_service_error('Luna service temporarily unavailable','cli-generation',True if statuses else None,
            phase='generation',delivery_state='response_received' if statuses else 'unknown')
    else:
        failure=LunaProviderError(f'Luna CLI failed with exit code {returncode}',metadata=metadata)
    failure.metadata.update(metadata)
    return failure


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exclusive_tagged_union(value: Mapping[str, Any], root: Any) -> bool:
    """Prove the branches are disjoint before translating oneOf to anyOf.

    OpenAI's strict subset supports anyOf, not oneOf. A required discriminator
    with disjoint string literals makes these two operators equivalent. Other
    unions must fail closed; silently broadening their meaning is not allowed.
    """
    tag = value.get('discriminator', {}).get('propertyName')
    branches = value.get('oneOf')
    if not isinstance(tag, str) or not isinstance(branches, list) or not branches:
        return False
    seen = set()
    for branch in branches:
        visited = set()
        while isinstance(branch, dict) and '$ref' in branch:
            ref = branch['$ref']
            if not isinstance(ref, str) or not ref.startswith('#/') or ref in visited:
                return False
            visited.add(ref)
            node = root
            try:
                for key in ref[2:].split('/'):
                    node = node[key.replace('~1', '/').replace('~0', '~')]
            except (KeyError, TypeError):
                return False
            branch = node
        if not isinstance(branch, dict) or branch.get('type') != 'object' or tag not in branch.get('required', []):
            return False
        prop = branch.get('properties', {}).get(tag, {})
        choices = [prop['const']] if 'const' in prop else prop.get('enum', [])
        if prop.get('type') != 'string' or not isinstance(choices, list) or not choices or not all(isinstance(x, str) for x in choices):
            return False
        values = set(choices)
        if seen & values:
            return False
        seen.update(values)
    return True


def _strict_schema(value: Any, *, _root: Any = None) -> Any:
    """Require explicit defaults on the test transport; Pydantic still validates."""
    root = value if _root is None else _root
    if isinstance(value, list):
        return [_strict_schema(item, _root=root) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _strict_schema(item, _root=root) for key, item in value.items() if key != "default"}
    if 'oneOf' in result:
        if 'anyOf' in result or not _exclusive_tagged_union(value, root):
            raise LunaProviderError('unsupported non-exclusive oneOf; refusing to weaken validation')
        result['anyOf'] = result.pop('oneOf')
        # Pydantic's discriminator is an annotation; its literal constraints
        # remain in every branch and in the unchanged local validation model.
        result.pop('discriminator', None)
    if '$ref' in result:
        # The strict transport rejects annotation siblings on references.
        # Remove only non-validating annotations, never source/type bounds.
        result.pop('description',None)
        result.pop('title',None)
        if set(result)!={'$ref'}:
            raise LunaProviderError('unsupported constrained schema reference; refusing to weaken validation')
    if result.get("type") == "object":
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def _cli_version() -> str:
    try:
        completed = subprocess.run([CLI, "-V"], capture_output=True, text=True, timeout=10, check=False)
        if completed.returncode or not completed.stdout.strip() or len(completed.stdout.encode()) > 4096:
            raise _service_error('unable to determine Codex CLI version','cli-version',
                category='model_request_configuration',retryable=False)
        return completed.stdout.strip()[:256]
    except OSError as exc:
        raise _service_error('unable to determine Codex CLI version','cli-version',
            category='model_request_configuration',retryable=False) from exc
    except subprocess.TimeoutExpired as exc:
        raise _service_error('unable to determine Codex CLI version','cli-version') from exc


def _contains_tool_event(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if key in {"type", "event_type", "kind"} and isinstance(item, str):
                lowered = item.lower()
                if "tool" in lowered or any(token in lowered for token in ("function_call", "computer_call", "shell_command")):
                    return True
            if _contains_tool_event(item):
                return True
    elif isinstance(value, list):
        return any(_contains_tool_event(item) for item in value)
    return False


def _agent_text(value: Any) -> str | None:
    if isinstance(value, Mapping):
        typ = str(value.get("type", "")).lower()
        if typ in {"agent_message", "assistant_message", "final_message"}:
            for key in ("text", "content", "message"):
                item = value.get(key)
                if isinstance(item, str) and item:
                    return item
                if isinstance(item, list):
                    parts = [x.get("text", "") for x in item if isinstance(x, Mapping) and isinstance(x.get("text"), str)]
                    if "".join(parts):
                        return "".join(parts)
        for item in value.values():
            found = _agent_text(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _agent_text(item)
            if found:
                return found
    return None


def _usage(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        if isinstance(value.get("usage"), Mapping):
            return dict(value["usage"])
        for item in value.values():
            found = _usage(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = _usage(item)
            if found:
                return found
    return {}


def infer(messages: Sequence[Mapping[str, Any]], schema: Mapping[str, Any], artifact_root: Path | str, *, emit=None) -> tuple[str, dict[str, Any]]:
    """Run one isolated Luna request and return ``(final_text, receipt)``."""
    try:
        from workbench.request_compiler import compile_codex_wrapper
        wrapper=compile_codex_wrapper(messages,schema,schema_transform=_strict_schema)
        request=wrapper.request_json.encode()
        schema_bytes=wrapper.schema_json.encode()
    except (TypeError, ValueError) as exc:
        raise _service_error('invalid messages or unsupported transport schema','request-preparation',
            category='model_request_configuration',phase='preparation',retryable=False) from exc
    if len(request) > MAX_IO_BYTES or len(schema_bytes) > MAX_IO_BYTES:
        raise _service_error('request or schema exceeds bounded input','request-preparation',
            category='model_request_configuration',phase='preparation',retryable=False)
    root = Path(artifact_root)
    root.mkdir(parents=True, exist_ok=True)
    absolute = root.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.exists() and current.is_symlink():
            raise LunaProviderError("artifact root path must not contain symlinks")
    if root.is_symlink() or not root.is_dir():
        raise LunaProviderError("artifact root must be a real directory")
    started = time.monotonic()
    cli_version = _cli_version()
    with tempfile.TemporaryDirectory(prefix="luna-test-", dir=root) as work:
        workdir = Path(work)
        schema_file = workdir / "output-schema.json"
        schema_file.write_bytes(schema_bytes)
        args = [CLI, "exec", "--ephemeral", "--ignore-user-config", "--sandbox", "read-only",
                "--skip-git-repo-check", "-C", str(workdir), "-m", MODEL,
                "-c", 'web_search="disabled"', "-c", 'model_reasoning_effort="low"',
                "-c", "skip_host_skill_discovery=true", "--output-schema", str(schema_file), "--json"]
        for feature in DISABLED_FEATURES:
            args.extend(("--disable", feature))
        # CLI diagnostics belong to stderr, not the JSONL event protocol.
        args.append("-")
        with tempfile.TemporaryFile() as spool, tempfile.TemporaryFile() as diagnostics:
            try:
                process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=spool, stderr=diagnostics, shell=False, cwd=workdir)
                try:
                    if emit is not None:
                        # Local adapter entry, not observation of server generation
                        # or proof that its discovery/authentication succeeded.
                        emit('dispatch_attempted',delivery_state='unknown',process_started=True,
                            request_attempted=None,dispatch_boundary='local_cli_adapter')
                        emit('response_waiting',delivery_state='unknown',process_started=True,
                            request_attempted=None,dispatch_boundary='local_cli_adapter')
                    process.communicate(input=request, timeout=180)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.communicate()
                    failure=_service_error('Luna CLI timed out','cli-generation',None,
                        phase='generation',delivery_state='unknown')
                    failure.metadata['process_started']=True
                    raise failure from exc
                except BaseException:
                    # A failed event sink must not leave a local adapter running
                    # without an observable owner or trigger a second request.
                    try:
                        process.kill()
                        process.communicate()
                    except OSError:
                        pass
                    raise
            except OSError as exc:
                raise _service_error('unable to start Luna CLI','cli-start',
                    category='model_request_configuration',retryable=False) from exc
            spool.seek(0)
            raw = spool.read(MAX_IO_BYTES + 1)
            diagnostics.seek(0)
            diagnostic_bytes=diagnostics.read(MAX_IO_BYTES + 1)
    if len(raw) > MAX_IO_BYTES:
        raise LunaProviderError("Luna output exceeds bounded limit")
    if process.returncode:
        raise _cli_failure(raw,diagnostic_bytes,process.returncode)
    events = []
    try:
        for line in raw.splitlines():
            if not line.strip():
                continue
            event = json.loads(line)
            if _contains_tool_event(event):
                raise LunaProviderError("Luna attempted a tool call")
            events.append(event)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LunaProviderError("malformed Luna JSONL output") from exc
    content = next((text for event in reversed(events) if (text := _agent_text(event))), None)
    if not content:
        raise LunaProviderError("Luna returned no final agent message")
    output = content.encode()
    receipt = {
        "test_only": True,
        "actual_model": MODEL,
        "cli_version": cli_version,
        "input_sha256": _sha(request),
        **wrapper.identity,
        "output_sha256": _sha(output),
        "wall_time_seconds": round(time.monotonic() - started, 3),
        "phase": "response_received", "process_started": True,
        "request_attempted": True, "delivery_state": "response_received",
        "usage": next((_usage(event) for event in reversed(events) if _usage(event)), {}),
    }
    if emit is not None:
        emit('response_received',delivery_state='response_received',
            response_sha256=receipt['output_sha256'],output_characters=len(content),usage=receipt['usage'],
            request_attempted=True,dispatch_boundary='codex_luna_response')
    return content, receipt


__all__ = ["LunaProviderError", "infer"]
