"""Bind a case to loaded code and configured model without retaining credentials."""
import hashlib
import json
import os
from pathlib import Path


def code_identity():
    root=Path(__file__).resolve().parents[1];digest=hashlib.sha256()
    for folder in ('workbench','dist','templates','profiles','recipes'):
        for path in sorted((root/folder).rglob('*')):
            if path.is_file() and '__pycache__' not in path.parts:
                digest.update(path.relative_to(root).as_posix().encode());digest.update(path.read_bytes())
    for name in ('requirements.lock','requirements-worker.lock'):
        path=root/name
        if path.exists():digest.update(path.read_bytes())
    return digest.hexdigest()


def binding(code, provider):
    safe={k:provider.get(k) for k in ('model','falsifier_model','base_url','protocol','trusted_lan','temperature','num_ctx') if k in provider}
    value={'version':'runtime-contract-1','source_sha256':code,'provider':safe,
        'release':os.getenv('FRONTIER_RELEASE','unrecorded'),
        'model_digest':os.getenv('FRONTIER_MODEL_DIGEST','unverified'),
        'worker_image':os.getenv('FRONTIER_WORKER_IMAGE','unverified'),
        'investigation_model_calls':os.getenv('INVESTIGATION_MODEL_CALLS','12')}
    value['fingerprint']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
    return value
