"""Host-only assisted E2E driver (test use; never imported by workbench).

It preserves the application's normal scheduler and provider validation. Only
explicitly selected roles are diverted to the test Luna adapter. All roles can
be selected for harness E2E testing; this is not an Ollama model-quality test.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from scripts import luna_test_provider as _luna_adapter
except ModuleNotFoundError:  # direct ``python scripts/run_assisted_e2e.py`` invocation
    import luna_test_provider as _luna_adapter

infer = _luna_adapter.infer
MODEL_ROLES = ('analyst', 'investigator', 'judgment', 'synthesis', 'falsifier')


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dotenv_worker_token(path: Path) -> str | None:
    if not path.is_file():
        return None
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("WORKER_TOKEN="):
            value = line.partition("=")[2].strip()
            return value.strip('"\'') or None
    return None


def configure_environment(args: argparse.Namespace) -> dict[str, str]:
    """Set only the host process environment needed by the normal app."""
    values = {
        "DATA_ROOT": str(Path(args.data_root).resolve()),
        "ANALYSIS_ROOT": str(Path(args.analysis_root).resolve()),
        "WORKER_URL": args.worker_url.rstrip("/"),
        "FRONTIER_WORKER_SOURCE_SHA256": args.worker_source,
        "FRONTIER_WORKER_IMAGE": args.worker_image,
        "FRONTIER_MODEL_DIGEST": args.model_digest,
        "MODEL_UPSTREAM_URL": args.model_url.rstrip("/"),
    }
    for key, value in values.items():
        os.environ[key] = value
    os.environ.pop("MODEL_RELAY_URL", None)
    token = os.environ.get("WORKER_TOKEN") or _dotenv_worker_token(Path(".env"))
    if token:
        os.environ["WORKER_TOKEN"] = token
    return values


def _receipt_dir(root: Path) -> Path:
    path = root / "luna-transport-receipts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _split_transport_budget(receipt: dict[str, Any], luna_receipt: dict[str, Any]) -> dict[str, Any]:
    """Keep the production budget distinct from test-transport measurements.

    ``Provider.generate`` builds ``prompt_budget`` for the configured Ollama
    provider.  The assisted path intercepts the request before Ollama sees it,
    so Luna's reported token counts cannot be measured into that budget (and
    are not Qwen/Ollama tokenizer counts).  Keep both records explicit and do
    not infer retained-input or headroom properties for the Luna transport.
    """
    requested_ollama = receipt.get("prompt_budget") if isinstance(receipt, dict) else None
    if isinstance(requested_ollama, dict):
        # ``Provider.generate`` saw Luna's intercepted response, so any
        # measured fields in its copied budget are transport measurements from
        # the wrong provider.  Preserve the requested envelope and heuristic
        # estimates, but make production execution and measured counters
        # explicitly unavailable.
        requested_ollama = dict(requested_ollama)
        for key in ("actual_input_tokens", "exact_input_tokens", "actual_output_tokens", "actual_headroom"):
            requested_ollama[key] = None
        requested_ollama["production_transport_executed"] = False
    usage = luna_receipt.get("usage", {}) if isinstance(luna_receipt, dict) else {}
    if not isinstance(usage, dict):
        usage = {}
    return {
        "requested_ollama": requested_ollama,
        "actual_luna_transport": {
            "transport": "codex-luna-test-only",
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "usage": dict(usage),
            "token_count_basis": "Luna-reported; not an Ollama/Qwen tokenizer count",
            "server_retained_full_input_verified": False,
        },
    }


def install_assisted_provider(analysis_root: Path, driver_path: Path | None = None, *, luna_roles=('judgment',), transport_root: Path | None = None) -> dict[str, Any]:
    """Patch Provider.generate in this test process and return driver identity."""
    from workbench.models import Analysis, InvestigationPlan, Falsification, review_output_schema
    from workbench.provider import Provider

    original = Provider.generate
    original_provider_response = Provider.response
    source = driver_path or Path(__file__).resolve()
    analysis_root = Path(analysis_root).resolve()
    analysis_root.mkdir(parents=True, exist_ok=True)
    transport_root = Path(transport_root or analysis_root).resolve()
    transport_root.mkdir(parents=True, exist_ok=True)
    allowed_roles = frozenset(luna_roles)
    if not allowed_roles or not allowed_roles.issubset(MODEL_ROLES):
        raise ValueError('luna_roles must contain only supported model roles')
    driver_sha256 = _sha(source)
    adapter_path = Path(_luna_adapter.__file__).resolve()
    adapter_sha256 = _sha(adapter_path)
    release = f"TEST_ONLY-assisted-{driver_sha256}-{adapter_sha256}-{'-'.join(sorted(allowed_roles))}"
    os.environ["FRONTIER_RELEASE"] = release
    lock = threading.Lock()
    receipts = _receipt_dir(transport_root)
    # The test transport is the only endpoint explicitly allowed to run two
    # independent requests.  This is configuration by transport identity, not
    # a production/model-name special case; local Ollama keeps its default of 1.
    luna_identity = hashlib.sha256(('codex-luna-test-only:'+_luna_adapter.MODEL).encode()).hexdigest()
    from workbench.model_concurrency import configure_endpoint
    # Repeated installs are idempotent; a conflicting prior limit fails closed.
    configure_endpoint(luna_identity, limit=2)

    manifest_path = transport_root / "assisted-e2e-manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("assisted E2E manifest is corrupt; refusing restart") from exc
        if (manifest.get("driver_sha256") != driver_sha256 or manifest.get("adapter_sha256") != adapter_sha256
                or set(manifest.get('luna_roles', [])) != set(allowed_roles)
                or manifest.get("test_only") is not True or not isinstance(manifest.get("transports"), list)):
            raise ValueError("assisted E2E manifest pin mismatch; refusing restart")
    else:
        manifest = {"test_only": True, "driver": source.name, "driver_sha256": driver_sha256,
                    "adapter": adapter_path.name, "adapter_sha256": adapter_sha256, "release": release,
                    "luna_roles": sorted(allowed_roles), "transport_root": str(transport_root), "transports": []}
        manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    def record_transport(kind, path, payload, response=None):
        row = {"id": "TRANSPORT-" + uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
               "test_only": True, "transport": kind, "path": path, "driver_sha256": driver_sha256,
               "request_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()}
        if response is not None:
            row["response_sha256"] = hashlib.sha256(json.dumps(response, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        with lock:
            (receipts / f"{row['id']}.json").write_text(json.dumps(row, sort_keys=True), encoding="utf-8")
            manifest["transports"].append(row)
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    def audit_luna_failure(captured, error, role):
        entered='payload' in captured
        metadata=dict(getattr(error,'metadata',{}))
        actual=captured.get('luna_receipt') or {}
        # Entering the adapter or spawning a CLI is not delivery to the model.
        attempted=actual.get('request_attempted',metadata.get('request_attempted',None if entered else False))
        delivery=actual.get('delivery_state',metadata.get('delivery_state','unknown' if entered else 'not_sent'))
        row = {"id": "LUNA-ERROR-" + uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(),
               "test_only": True, "transport": "codex-luna" if entered else "request-preparation", "role": role, "driver_sha256": driver_sha256,
               "adapter_sha256": adapter_sha256, "model": _luna_adapter.MODEL, "reasoning_effort": "low",
               "error_type": "pydantic_gate" if getattr(error, "category", "") == "output_schema" else "transport",
               "error": str(error)[:1000],
               "adapter_entered":entered,"request_attempted":attempted,"delivery_state":delivery,
               "phase":metadata.get('phase','output_validation' if actual else 'unknown' if entered else 'preparation'),
               "failure_category":getattr(error,'category','unclassified'),"failure_metadata":metadata,
               "transport_identity":luna_identity,
               "request_sha256": hashlib.sha256(json.dumps(captured['payload'], sort_keys=True, ensure_ascii=False).encode()).hexdigest() if entered else None}
        if captured.get("luna_receipt"):
            row["usage"] = captured["luna_receipt"].get("usage", {})
        with lock:
            (receipts / f"{row['id']}.json").write_text(json.dumps(row, sort_keys=True), encoding="utf-8")
            manifest["transports"].append(row)
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    def recorded_response(self, path, payload):
        response = original_provider_response(self, path, payload)
        record_transport("ollama", path, payload, response)
        return response

    Provider.response = recorded_response

    def assisted(self, question, pack, role="analyst"):
        if role not in allowed_roles:
            return original(self, question, pack, role=role)
        captured: dict[str, Any] = {}
        original_response = self.response
        original_identity = self.verify_model_identity
        original_transport_identity = self.transport_identity

        def verify_identity(model):
            # Actual Luna identity is pinned by this immutable test adapter and
            # CLI invocation, not by an unrelated Ollama availability probe.
            if self.config['protocol']!='ollama' or self.config['think'] not in ('off','auto'):
                raise ValueError('Assisted transport requires Ollama-compatible prompt settings with think off/auto')
            return {'transport':'codex-luna-test-only','model':_luna_adapter.MODEL,
                    'driver_sha256':driver_sha256,'adapter_sha256':adapter_sha256,
                    'requested_ollama_identity_checked':False}

        def response(path, payload):
            if path != "/api/chat":
                raise ValueError('Unsupported assisted transport operation: '+path)
            captured["payload"] = payload
            schema = review_output_schema(role,pack) if role in ('judgment','synthesis') else {'falsifier':Falsification,'investigator':InvestigationPlan,'analyst':Analysis}[role]
            output_schema = payload.get('format') if isinstance(payload.get('format'),dict) else schema.model_json_schema()
            content, luna_receipt = infer(payload["messages"], output_schema, transport_root)
            captured["luna_receipt"] = luna_receipt
            self._request_attempted=luna_receipt.get('request_attempted')
            return {"done_reason": "stop", "message": {"content": content}, "eval_count": luna_receipt.get("usage", {}).get("output_tokens", 0)}

        self.response = response
        self.verify_model_identity = verify_identity
        self.transport_identity = lambda: luna_identity
        try:
            output, receipt = original(self, question, pack, role=role)
        except Exception as error:
            audit_luna_failure(captured, error, role)
            raise
        finally:
            self.response = original_response
            self.verify_model_identity = original_identity
            self.transport_identity = original_transport_identity
        transport = {
            "id": "LUNA-" + uuid.uuid4().hex,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "test_only": True,
            "transport": "codex-luna",
            "role": role,
            "driver_sha256": driver_sha256,
            "adapter_sha256": adapter_sha256,
            "request_sha256": hashlib.sha256(json.dumps(captured.get("payload", {}), sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "model": _luna_adapter.MODEL,
        }
        for key in ("cli_version", "input_sha256", "output_sha256", "wall_time_seconds", "usage",
                    "phase", "process_started", "request_attempted", "delivery_state"):
            if key in captured.get("luna_receipt", {}):
                transport[key] = captured["luna_receipt"][key]
        # Keep the receipt transport-only: no prompt, response, or private reasoning.
        with lock:
            (receipts / f"{transport['id']}.json").write_text(json.dumps(transport, sort_keys=True), encoding="utf-8")
            manifest["transports"].append(transport)
            manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
        luna_receipt = captured.get("luna_receipt", {})
        receipt = {**receipt, "model": _luna_adapter.MODEL, "role": role, "test_only": True,
                   "reasoning_effort": "low",
                   "usage": luna_receipt.get("usage", {}),
                   "prompt_budget": _split_transport_budget(receipt, luna_receipt),
                   "generation_settings": {"transport": "codex-luna-test-only", "requested_ollama": receipt.get("generation_settings")}}
        return output, receipt

    Provider.generate = assisted
    return {"driver_sha256": driver_sha256, "manifest": manifest_path}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the host E2E app with explicitly selected test-only Luna roles")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--analysis-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--worker-url", default="http://127.0.0.1:8776")
    parser.add_argument("--worker-source", required=True)
    parser.add_argument("--worker-image", required=True)
    parser.add_argument("--model-digest", required=True)
    parser.add_argument("--model-url", required=True)
    parser.add_argument("--luna-roles", nargs="+", choices=MODEL_ROLES, default=["judgment"])
    parser.add_argument("--ollama-only", action="store_true",
                        help="Use the normal configured private Ollama provider for every role; do not install or call Luna.")
    parser.add_argument("--transport-root", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.data_root = args.data_root.resolve()
    args.analysis_root = args.analysis_root.resolve()
    args.data_root.mkdir(parents=True, exist_ok=True)
    args.analysis_root.mkdir(parents=True, exist_ok=True)
    configure_environment(args)
    roles = tuple(args.luna_roles)
    if not args.ollama_only:
        install_assisted_provider(args.analysis_root, luna_roles=roles, transport_root=args.transport_root)
    else:
        os.environ['FRONTIER_RELEASE']='TEST_ONLY-ollama-'+_sha(Path(__file__).resolve())
    import uvicorn
    from workbench.api import create_app

    app = create_app(args.data_root, os.getenv("EVIDENCE_ROOT"), start_worker=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
