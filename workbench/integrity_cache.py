"""Fail-closed cache for successful EWF verification proofs.

The cache is deliberately independent of the evidence tree.  A caller must
first hash every physical segment and pass that freshly computed manifest
here; this module never treats file metadata as an identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "ewf-verification-proof-1"
DEFAULT_VERIFIER_CONTRACT = "ewfverify-sha256-v1"
_MAX_JSON_BYTES = 2 * 1024 * 1024
_MAX_STDOUT_BYTES = 1024 * 1024
_HEX64 = set("0123456789abcdef")


def _text(value: Any, name: str, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError(f"invalid {name}")
    return value


def canonical_manifest(manifest: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Validate and canonicalize a freshly computed physical-segment manifest."""
    if not isinstance(manifest, (list, tuple)) or not manifest or len(manifest) > 10000:
        raise ValueError("invalid segment manifest")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for entry in manifest:
        if not isinstance(entry, Mapping):
            raise ValueError("invalid segment entry")
        name = _text(entry.get("name"), "segment name", 255)
        if name in {".", ".."} or "/" in name or "\\" in name or "\x00" in name:
            raise ValueError("unsafe segment name")
        if name in names:
            raise ValueError("duplicate segment name")
        digest = entry.get("sha256")
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in _HEX64 for c in digest):
            raise ValueError("invalid segment sha256")
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError("invalid segment size")
        names.add(name)
        result.append({"name": name, "sha256": digest, "size": size})
    return sorted(result, key=lambda item: item["name"])


def proof_cache_key(
    manifest: Sequence[Mapping[str, Any]],
    tool_version: str,
    verifier_contract: str = DEFAULT_VERIFIER_CONTRACT,
) -> str:
    payload = {
        "manifest": canonical_manifest(manifest),
        "tool_version": _text(tool_version, "tool version"),
        "verifier_contract": _text(verifier_contract, "verifier contract"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def cache_path(
    analysis_root: Path | str,
    manifest: Sequence[Mapping[str, Any]],
    tool_version: str,
    verifier_contract: str = DEFAULT_VERIFIER_CONTRACT,
) -> Path:
    return _root(analysis_root) / f"ewf-proof-{proof_cache_key(manifest, tool_version, verifier_contract)}.json"


def _root(value: Path | str) -> Path:
    root = Path(value)
    # Check existing components too: an otherwise ordinary-looking child can
    # redirect the cache outside the configured analysis root.
    absolute = root.absolute()
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if current.exists() and current.is_symlink():
            raise ValueError("analysis root path must not contain symlinks")
    root.mkdir(parents=True, exist_ok=True)
    if not root.is_dir() or root.is_symlink():
        raise ValueError("analysis root is not a directory")
    return root


def _proof_body(manifest: list[dict[str, Any]], tool_version: str, contract: str, stdout: str, recorded_at: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "cache_key": proof_cache_key(manifest, tool_version, contract),
        "manifest": manifest,
        "tool_version": tool_version,
        "verifier_contract": contract,
        "verification": {"status": "succeeded", "stdout": stdout, "recorded_at": recorded_at},
    }


def _digest(body: Mapping[str, Any]) -> str:
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def save_proof(
    analysis_root: Path | str,
    manifest: Sequence[Mapping[str, Any]],
    tool_version: str,
    verifier_contract: str = DEFAULT_VERIFIER_CONTRACT,
    verification_stdout: str = "",
    *,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Atomically publish a successful proof, returning the stored record."""
    canonical = canonical_manifest(manifest)
    tool = _text(tool_version, "tool version")
    contract = _text(verifier_contract, "verifier contract")
    if not isinstance(verification_stdout, str) or len(verification_stdout.encode()) > _MAX_STDOUT_BYTES:
        raise ValueError("verification stdout is too large")
    when = recorded_at or datetime.now(timezone.utc).isoformat()
    _text(when, "recorded time", 128)
    body = _proof_body(canonical, tool, contract, verification_stdout, when)
    record = {**body, "proof_sha256": _digest(body)}
    encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    root = _root(analysis_root)
    destination = root / f"ewf-proof-{record['cache_key']}.json"
    fd, temporary = tempfile.mkstemp(prefix=".ewf-proof-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
        return record
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def load_proof(
    analysis_root: Path | str,
    manifest: Sequence[Mapping[str, Any]],
    tool_version: str,
    verifier_contract: str = DEFAULT_VERIFIER_CONTRACT,
) -> dict[str, Any] | None:
    """Return a validated prior proof, or ``None`` for every cache failure."""
    try:
        canonical = canonical_manifest(manifest)
        tool = _text(tool_version, "tool version")
        contract = _text(verifier_contract, "verifier contract")
        path = cache_path(analysis_root, canonical, tool, contract)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > _MAX_JSON_BYTES:
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(record, dict):
            return None
        if len(json.dumps(record, ensure_ascii=False).encode()) > _MAX_JSON_BYTES:
            return None
        expected = {"schema_version", "cache_key", "manifest", "tool_version", "verifier_contract", "verification", "proof_sha256"}
        if set(record) != expected:
            return None
        body = {key: record[key] for key in expected if key != "proof_sha256"}
        if record["schema_version"] != SCHEMA_VERSION or record["cache_key"] != proof_cache_key(canonical, tool, contract):
            return None
        if record["manifest"] != canonical or record["tool_version"] != tool or record["verifier_contract"] != contract:
            return None
        verification = record["verification"]
        if not isinstance(verification, dict) or set(verification) != {"status", "stdout", "recorded_at"}:
            return None
        if verification["status"] != "succeeded" or not isinstance(verification["stdout"], str) or len(verification["stdout"].encode()) > _MAX_STDOUT_BYTES:
            return None
        _text(verification["recorded_at"], "recorded time", 128)
        if record["proof_sha256"] != _digest(body):
            return None
        return {**record, "reuse": {"reused": True, "label": "previous validation; not a new independent check"}}
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        return None


__all__ = ["DEFAULT_VERIFIER_CONTRACT", "SCHEMA_VERSION", "canonical_manifest", "proof_cache_key", "cache_path", "save_proof", "load_proof"]
