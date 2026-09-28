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
MODEL = "gpt-5.6-luna"
MAX_IO_BYTES = 8 * 1024 * 1024
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "apps", "plugins", "browser_use",
    "browser_use_external", "computer_use", "in_app_browser", "multi_agent",
    "image_generation", "view_image", "hooks", "skill_search",
    "workspace_dependencies",
)


class LunaProviderError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_schema(value: Any) -> Any:
    """Require explicit defaults on the test transport; Pydantic still validates."""
    if isinstance(value, list):
        return [_strict_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    result = {key: _strict_schema(item) for key, item in value.items() if key != "default"}
    if result.get("type") == "object":
        result["additionalProperties"] = False
        result["required"] = list(result.get("properties", {}))
    return result


def _cli_version() -> str:
    try:
        completed = subprocess.run([CLI, "-V"], capture_output=True, text=True, timeout=10, check=False)
        if completed.returncode or not completed.stdout.strip() or len(completed.stdout.encode()) > 4096:
            raise LunaProviderError("unable to determine Codex CLI version")
        return completed.stdout.strip()[:256]
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LunaProviderError("unable to determine Codex CLI version") from exc


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


def infer(messages: Sequence[Mapping[str, Any]], schema: Mapping[str, Any], artifact_root: Path | str) -> tuple[str, dict[str, Any]]:
    """Run one isolated Luna request and return ``(final_text, receipt)``."""
    try:
        request = json.dumps({"messages": messages}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        schema_bytes = json.dumps(_strict_schema(schema), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    except (TypeError, ValueError) as exc:
        raise LunaProviderError("messages/schema must be JSON serializable") from exc
    if len(request) > MAX_IO_BYTES or len(schema_bytes) > MAX_IO_BYTES:
        raise LunaProviderError("request or schema exceeds bounded input")
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
                    process.communicate(input=request, timeout=180)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.communicate()
                    raise LunaProviderError("Luna CLI timed out") from exc
            except OSError as exc:
                raise LunaProviderError("unable to start Luna CLI") from exc
            spool.seek(0)
            raw = spool.read(MAX_IO_BYTES + 1)
    if len(raw) > MAX_IO_BYTES:
        raise LunaProviderError("Luna output exceeds bounded limit")
    if process.returncode:
        error_message = ""
        for line in raw.splitlines():
            try:
                event = json.loads(line)
                if event.get("type") in ("error", "turn.failed"):
                    detail = event.get("error", event)
                    if isinstance(detail, dict):
                        error_message = str(detail.get("message", ""))[:1500]
            except (ValueError, AttributeError):
                pass
        raise LunaProviderError(f"Luna CLI failed with exit code {process.returncode}: {error_message}")
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
        "output_sha256": _sha(output),
        "wall_time_seconds": round(time.monotonic() - started, 3),
        "usage": next((_usage(event) for event in reversed(events) if _usage(event)), {}),
    }
    return content, receipt


__all__ = ["LunaProviderError", "infer"]
