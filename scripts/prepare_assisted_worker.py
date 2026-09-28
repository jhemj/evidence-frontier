"""Deploy an isolated read-only E2E worker and import an actual prior EWF proof.

No scan or model result is imported. The new case must hash every segment again
and execute every normal phase. This script never changes the baseline ledger.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def import_verified_proof(baseline, destination, evidence_path):
    from workbench.integrity_cache import save_proof
    baseline = Path(baseline)
    db = sqlite3.connect(f"file:{baseline / 'jobs/jobs.sqlite3'}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT * FROM jobs WHERE status='succeeded'").fetchall()
    finally:
        db.close()
    for row in rows:
        request = json.loads(row['request'])
        if request.get('action') != 'integrity' or request.get('path') != evidence_path:
            continue
        envelope_path = baseline / 'jobs' / (row['id'] + '.json')
        raw = envelope_path.read_bytes()
        envelope = json.loads(raw)
        result = envelope['result']
        expected = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if envelope['result_sha256'] != expected:
            raise ValueError('Baseline result digest mismatch')
        if envelope['request_sha256'] != hashlib.sha256(row['request'].encode()).hexdigest():
            raise ValueError('Baseline request digest mismatch')
        if (result.get('status') != 'covered' or not result.get('complete')
                or result.get('truncated') or result.get('error')
                or result.get('tool') != 'libewf ewfverify'
                or 'SUCCESS' not in result.get('verification', '')):
            raise ValueError('Baseline is not a completed successful EWF verification')
        proof = save_proof(Path(destination) / 'integrity-proofs', result['manifest'], result['version'],
            verification_stdout=result['verification'], recorded_at=row['updated_at'])
        provenance = {'test_only_import': True, 'prior_job_id': row['id'],
            'prior_envelope_sha256': hashlib.sha256(raw).hexdigest(),
            'prior_runtime_code': envelope['runtime_code'], 'prior_completed_at': row['updated_at'],
            'proof_sha256': proof['proof_sha256'],
            'scope': 'Previous logical verification, not a new independent check; fresh physical hashes are mandatory.'}
        (Path(destination) / 'proof-import.json').write_text(json.dumps(provenance, indent=2))
        return provenance
    raise ValueError('No successful baseline integrity job yet; verification is not bypassed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--analysis-root', type=Path, required=True)
    parser.add_argument('--evidence-root', type=Path, required=True)
    parser.add_argument('--baseline-analysis', type=Path)
    parser.add_argument('--image-path', required=True)
    parser.add_argument('--container')
    parser.add_argument('--image')
    parser.add_argument('--network', help='Explicit pre-created internal-only worker network')
    parser.add_argument('--port', type=int, default=8776)
    parser.add_argument('--import-only', action='store_true')
    args = parser.parse_args()
    args.analysis_root.mkdir(parents=True, exist_ok=True)
    if args.baseline_analysis:
        print(json.dumps(import_verified_proof(args.baseline_analysis, args.analysis_root, args.image_path)))
    if args.import_only:
        return
    if not all((args.container,args.image,args.network)):
        parser.error('--container, --image and --network are required when starting a worker')
    token = os.getenv('WORKER_TOKEN')
    if not token:
        for line in (ROOT / '.env').read_text().splitlines():
            if line.startswith('WORKER_TOKEN='):
                token = line.split('=', 1)[1].strip().strip('"').strip("'")
    if not token:
        raise ValueError('Worker authentication token is required')
    docker = ['docker', '--context', 'desktop-linux']
    image_id = subprocess.check_output(docker + ['image', 'inspect', args.image, '--format', '{{.Id}}'], text=True).strip()
    # Worker has no internet route. A separate fixed ingress is needed on
    # Docker Desktop to publish a host port from an internal-only network.
    network = args.network
    network_info=json.loads(subprocess.check_output(docker+['network','inspect',network],text=True))
    if not network_info or network_info[0].get('Internal') is not True:
        raise ValueError('Worker network must be internal-only')
    subprocess.run(docker + ['run', '-d', '--name', args.container, '--init',
        '--network', network, '-p', f'127.0.0.1:{args.port}:8766',
        '--read-only', '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
        '--user', f'{os.getuid()}:{os.getgid()}', '--memory', '4g', '--cpus', '2',
        '--tmpfs', '/tmp:size=256M,mode=1777', '-e', 'WORKER_TOKEN',
        '-e', 'EVIDENCE_ROOT=/evidence', '-e', 'ANALYSIS_ROOT=/analysis',
        '--mount', f'type=bind,source={args.evidence_root.resolve(strict=True)},target=/evidence,readonly',
        '--mount', f'type=bind,source={args.analysis_root.resolve()},target=/analysis', image_id],
        check=True, env={**os.environ, 'WORKER_TOKEN': token})
    code = subprocess.check_output(docker + ['exec', args.container, 'python', '-c',
        'from workbench.runtime_contract import code_identity; print(code_identity())'], text=True).strip()
    manifest = {'image': image_id, 'source_sha256': code, 'container': args.container,
        'worker_url': f'http://127.0.0.1:{args.port}', 'evidence_read_only': True,
        'evidence_root': str(args.evidence_root.resolve()), 'network': network}
    (args.analysis_root / 'deployment.json').write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest))


if __name__ == '__main__':
    main()
