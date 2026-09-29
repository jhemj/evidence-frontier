#!/usr/bin/env python3
"""Boundary-safe review-stream replay on a temporary SQLite backup.

Default mode constructs and advances the normal dossier pipeline only until the
model boundary, with worker dispatch and model transport fail-closed. ``--live``
is an explicit opt-in for the caller; this script never enables it implicitly.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_dossier_input import backup_readonly  # noqa: E402


class BoundaryStop(RuntimeError):
    pass


CHECKPOINT_MANIFEST_SUFFIX = '.manifest.json'


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def checkpoint_manifest_path(checkpoint: Path) -> Path:
    """Return the sidecar path that authenticates a replay checkpoint."""
    return checkpoint.with_name(checkpoint.name + CHECKPOINT_MANIFEST_SUFFIX)


def _validate_checkpoint(checkpoint: Path, source: Path, source_hash: str,
                         case_id: str, batch_id: str) -> dict:
    checkpoint = Path(checkpoint)
    source = Path(source)
    if checkpoint.resolve() == source.resolve():
        raise ValueError('--resume-checkpoint must not be the original database')
    if checkpoint.is_symlink() or not checkpoint.is_file():
        raise ValueError('--resume-checkpoint must name a regular SQLite checkpoint')
    manifest_path = checkpoint_manifest_path(checkpoint)
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ValueError('checkpoint manifest is missing')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('checkpoint manifest is corrupt') from exc
    if (manifest.get('format') != 'review-stream-checkpoint-v1'
            or manifest.get('source_sha256') != source_hash
            or manifest.get('case_id') != case_id
            or manifest.get('batch_id') != batch_id):
        raise ValueError('checkpoint manifest does not match source/case/batch')
    expected_checkpoint_hash = manifest.get('checkpoint_sha256')
    if not isinstance(expected_checkpoint_hash, str) or digest_file(checkpoint) != expected_checkpoint_hash:
        raise ValueError('checkpoint hash does not match its manifest')
    return manifest


def _write_checkpoint(local_db: Path, checkpoint: Path, source: Path, source_hash: str,
                      case_id: str, batch_id: str, batch_status: str | None) -> Path:
    checkpoint = Path(checkpoint)
    if checkpoint.resolve() == Path(source).resolve():
        raise ValueError('--checkpoint-out must not overwrite the original database')
    if checkpoint.exists() or checkpoint.is_symlink():
        raise ValueError('--checkpoint-out must name a new file')
    manifest_path = checkpoint_manifest_path(checkpoint)
    if manifest_path.exists() or manifest_path.is_symlink():
        raise ValueError('checkpoint manifest path already exists')
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    backup_readonly(local_db, checkpoint)
    manifest = {
        'format': 'review-stream-checkpoint-v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source_database': str(Path(source).resolve()),
        'source_sha256': source_hash,
        'case_id': case_id,
        'batch_id': batch_id,
        'batch_status': batch_status,
        'checkpoint_sha256': digest_file(checkpoint),
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding='utf-8')
    return manifest_path


def logical_observation_digest(store, cid):
    rows=sorted(store.list('observation', cid), key=lambda row: row.get('id',''))
    return hashlib.sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def counts(store, cid, batch_id, baseline_input_ids):
    stream = store.get(batch_id).get('review_stream_id')
    pages = [p for p in store.list('review_page', cid) if p.get('stream_id') == stream] if stream else []
    canonical = store.get(stream).get('canonical', {}) if stream else {}
    inputs = [r for r in store.list('review_input', cid)
              if r.get('batch_id') == batch_id and r.get('id') not in baseline_input_ids]
    synthesis = [r for r in inputs if r.get('review_stream', {}).get('phase') == 'synthesis']
    return {
        'canonical_observations': len(canonical.get('observations', [])),
        'canonical_allowed_ids': len(canonical.get('allowed_observation_ids', [])),
        'pages_total': len(pages),
        'pages_reviewed': sum(p.get('status') == 'reviewed' for p in pages),
        'page_memberships': [{'index': p.get('index'), 'status': p.get('status'),
                              'included_ids': len(p.get('pack', {}).get('observations', []))}
                             for p in pages],
        'review_inputs': len(inputs),
        'synthesis_inputs': len(synthesis),
        'synthesis_reference_counts': [len(r.get('pack', {}).get('allowed_observation_ids', [])) for r in synthesis],
    }


def run(database: Path, case_id: str, batch_id: str, out: Path, max_calls: int, live: bool,
        checkpoint_out: Path | None = None, resume_checkpoint: Path | None = None,
        model_transport: str = 'ollama', retry_model_failure: bool = False) -> dict:
    if max_calls <= 0:
        raise ValueError('--max-calls must be greater than zero')
    if retry_model_failure and resume_checkpoint is None:
        raise ValueError('--retry-model-failure requires --resume-checkpoint')
    if model_transport not in ('ollama','luna'):
        raise ValueError('model_transport must be ollama or explicitly authorized luna')
    database = Path(database)
    if out.exists():
        raise ValueError('--out must name a new directory')
    source_hash = digest_file(database)
    if checkpoint_out is not None and resume_checkpoint is not None:
        if Path(checkpoint_out).resolve() == Path(resume_checkpoint).resolve():
            raise ValueError('--checkpoint-out and --resume-checkpoint must be different files')
    checkpoint_manifest = None
    if resume_checkpoint is not None:
        checkpoint_manifest = _validate_checkpoint(Path(resume_checkpoint), database, source_hash, case_id, batch_id)
    out.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='review-stream-probe-') as td:
        local_db = Path(td) / 'case.sqlite3'
        # Always work on a fresh temporary copy. A resume checkpoint is itself
        # copied read-only; it is never opened by Store or mutated in place.
        backup_readonly(Path(resume_checkpoint) if resume_checkpoint is not None else database, local_db)
        from workbench.controller import Controller
        from workbench.store import Store
        import workbench.dossiers as dossiers
        import workbench.discovery as discovery
        import workbench.case_synthesis as case_synthesis
        import workbench.runtime_contract as runtime_contract
        from workbench.dossiers import finish

        store = Store(local_db); controller = Controller(store, Path(td))
        case = store.get(case_id, 'case'); batch = store.get(batch_id, 'dossier_batch')
        if batch.get('case_id') != case_id: raise ValueError('batch/case mismatch')
        task = store.get(batch['task_id'], 'task'); evidence = store.get(batch['evidence_id'], 'evidence')
        if task.get('evidence_id') != evidence['id']: raise ValueError('task/evidence mismatch')
        original_output = deepcopy(batch.get('output')) if batch.get('output') is not None else None
        baseline_input_ids={r.get('id') for r in store.list('review_input', case_id)
                            if r.get('batch_id') == batch_id}
        baseline_receipt_ids={r.get('id') for r in store.list('receipt', case_id)
                              if r.get('batch_id') == batch_id}
        baseline_diagnostic_ids={r.get('id') for r in store.list('review_diagnostic', case_id)
                                 if r.get('batch_id') == batch_id}
        original_observation_digest=logical_observation_digest(store, case_id)
        if resume_checkpoint is not None:
            if batch.get('status') != 'input_projection_blocked':
                rejected=[r for r in store.list('review_diagnostic',case_id) if r.get('batch_id')==batch_id]
                failure = rejected[-1] if rejected else {}
                category = failure.get('failure_category')
                if batch.get('status')!='failed' or not failure:
                    raise ValueError('--resume-checkpoint batch must be input_projection_blocked or a diagnosed model failure')
                if category == 'input_context_pressure':
                    from workbench.review_diagnostics import smaller_input_budget
                    stream_id=batch.get('review_stream_id')
                    if stream_id:store.update(stream_id,status='superseded',reason='measured_model_context_pressure')
                    store.update(batch_id,review_input_maximum=smaller_input_budget(batch.get('review_input_maximum',36000),
                        failure.get('model_metadata',{})),review_stream_id=None,
                        input_budget_diagnostic_id=failure['id'])
                elif retry_model_failure and category in ('output_schema','output_budget','output_contract','unsupported_inference'):
                    # Explicit test-only replay after a code/contract correction.
                    # Never adopt the rejected output or erase prior diagnostics.
                    store.add('replay_retry', case_id, batch_id=batch_id,
                        diagnostic_id=failure['id'], failure_category=category,
                        source_checkpoint_sha256=checkpoint_manifest['checkpoint_sha256'],
                        source_sha256=runtime_contract.code_identity(), test_only=True)
                else:
                    raise ValueError('--resume-checkpoint model failure requires explicit --retry-model-failure for a supported output category')
            # Preserve the durable stream/pages and every neighbouring batch.
            # Only the blocked target becomes eligible for normal prepare().
            store.update(batch_id, status='pending', attempts=0, active_attempt_id=None)
        else:
            for other in store.list('dossier_batch', case_id):
                if other['id'] != batch_id and other.get('status') not in ('done', 'failed', 'split'):
                    store.update(other['id'], status='done')
            store.update(batch_id, status='pending', attempts=0, active_attempt_id=None,
                         validation_feedback=None, review_stream_id=None)

        # Validate the local destination before installing any process-global
        # replay boundary. A rejected configuration must leave callers intact.
        if live and model_transport == 'ollama':
            config=store.list('config')[-1]['provider']
            from workbench.provider import validate_url
            import os
            if os.environ.get('MODEL_RELAY_URL'):
                raise ValueError('Ollama-only replay refuses an inherited model relay; use the configured private endpoint directly')
            if config.get('protocol') != 'ollama':
                raise ValueError('Ollama-only replay requires a configured Ollama provider')
            validate_url(config['base_url'],config.get('trusted_lan',False))
        if live and model_transport == 'luna':
            from scripts.run_assisted_e2e import install_assisted_provider
            install_assisted_provider(out, luna_roles=('judgment',), transport_root=out / 'transport')

        old_guard = runtime_contract.guard
        old_admit = discovery.admit
        old_worker = dossiers.worker_request
        old_seed = dossiers.seed
        old_consult = dossiers.consult
        old_synthesis = case_synthesis.tick
        case_synthesis.tick = lambda *args, **kwargs: (_ for _ in ()).throw(
            BoundaryStop('case synthesis outside the selected dossier replay scope'))
        runtime_contract.guard = lambda *args, **kwargs: None  # TEST_ONLY copied DB
        discovery.admit = lambda *args, **kwargs: False
        dossiers.seed = lambda *args, **kwargs: None
        dossiers.worker_request = lambda *args, **kwargs: (_ for _ in ()).throw(
            BoundaryStop('worker boundary disabled for review-stream probe'))
        events=[]; consult_attempts=0; worker_attempts=0
        input_dir=out/'inputs'; receipt_dir=out/'receipts'; diagnostic_dir=out/'diagnostics'
        input_dir.mkdir(); receipt_dir.mkdir(); diagnostic_dir.mkdir()
        def boundary_consult(*args, **kwargs):
            nonlocal consult_attempts
            consult_attempts += 1
            raise BoundaryStop('model boundary disabled; use --live explicitly')
        def live_consult(*args, **kwargs):
            nonlocal consult_attempts
            consult_attempts += 1
            return old_consult(*args, **kwargs)
        def blocked_worker(*args, **kwargs):
            nonlocal worker_attempts
            worker_attempts += 1
            raise BoundaryStop('worker boundary disabled for review-stream probe')
        dossiers.worker_request = blocked_worker
        try:
            dossiers.consult = live_consult if live else boundary_consult
            for attempt in range(max_calls):
                try:
                    finish(controller, case_id, evidence, task)
                    status='returned'
                except BoundaryStop as exc:
                    status='boundary_stop'; events.append({'attempt': attempt + 1, 'status': status, 'error': str(exc)})
                    if not live: break
                current = store.get(batch_id)
                events.append({'attempt': attempt + 1, 'status': status,
                               'batch_status': current.get('status')}) if status == 'returned' else None
                new_inputs=[r for r in store.list('review_input', case_id)
                            if r.get('batch_id') == batch_id and r.get('id') not in baseline_input_ids]
                for record in new_inputs:
                    path=input_dir/(record['id']+'.json')
                    if not path.exists():path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
                new_receipts=[r for r in store.list('receipt', case_id)
                              if r.get('batch_id') == batch_id and r.get('id') not in baseline_receipt_ids]
                for record in new_receipts:
                    (receipt_dir/(record['id']+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
                new_diagnostics=[r for r in store.list('review_diagnostic', case_id)
                                 if r.get('batch_id') == batch_id and r.get('id') not in baseline_diagnostic_ids]
                for record in new_diagnostics:
                    (diagnostic_dir/(record['id']+'.json')).write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
                if any(r.get('receipt_type') == 'dossier_model' for r in new_receipts):break
                if current.get('status') in ('done', 'input_projection_blocked', 'model_failed', 'failed'):
                    break
        finally:
            runtime_contract.guard = old_guard; discovery.admit = old_admit
            dossiers.worker_request = old_worker; dossiers.seed = old_seed; dossiers.consult = old_consult
            case_synthesis.tick = old_synthesis

        receipts = [r for r in store.list('receipt', case_id)
                    if r.get('batch_id') == batch_id and r.get('id') not in baseline_receipt_ids]
        diagnostics = [r for r in store.list('review_diagnostic', case_id)
                       if r.get('batch_id') == batch_id and r.get('id') not in baseline_diagnostic_ids]
        inputs = [r for r in store.list('review_input', case_id)
                  if r.get('batch_id') == batch_id and r.get('id') not in baseline_input_ids]
        for record in inputs:
            record['input_characters']=len(json.dumps(record.get('pack',{}),ensure_ascii=False,separators=(',',':')))
        source_hash_after = digest_file(database)
        result = {
            'mode': 'live' if live else 'boundary_only', 'test_only_guard_bypass': True,
            'model_transport': model_transport if live else 'disabled',
            'external_worker_calls': worker_attempts, 'model_calls': consult_attempts,
            'new_input_records': [{'id':r.get('id'),'phase':r.get('review_stream',{}).get('phase'),
                                   'input_characters':r.get('input_characters')} for r in inputs],
            'database': str(database.resolve()), 'source_sha256_before': source_hash,
            'source_sha256_after': source_hash_after, 'source_unchanged': source_hash == source_hash_after,
            'logical_observation_digest_before': original_observation_digest,
            'logical_observation_digest_after': logical_observation_digest(store, case_id),
            'original_batch_output_saved': original_output is not None,
            'batch_id': batch_id, 'batch_status': store.get(batch_id).get('status'),
            'events': events, 'counts': counts(store, case_id, batch_id, baseline_input_ids),
            'receipts': [{'id': r.get('id'), 'type': r.get('receipt_type'), 'error': r.get('error'),
                          'input_record_id': r.get('input_record_id')} for r in receipts
                         if r.get('batch_id') == batch_id],
            'diagnostics': [{'id': d.get('id'), 'category': d.get('failure_category'),
                             'error': d.get('error')} for d in diagnostics if d.get('batch_id') == batch_id],
            'copy_mutations_only': True,
            'explicit_model_failure_retry': retry_model_failure,
            'resumed_from_checkpoint': str(Path(resume_checkpoint).resolve()) if resume_checkpoint is not None else None,
            'checkpoint_manifest': str(checkpoint_manifest_path(Path(resume_checkpoint)).resolve()) if resume_checkpoint is not None else None,
        }
        if original_output is not None:
            (out/'original-batch-output.json').write_text(
                json.dumps(original_output, ensure_ascii=False, indent=2), encoding='utf-8')
        (out / 'review-stream-probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        if checkpoint_out is not None:
            _write_checkpoint(local_db, Path(checkpoint_out), database, source_hash, case_id, batch_id,
                              store.get(batch_id).get('status'))
            result['checkpoint'] = str(Path(checkpoint_out).resolve())
            result['checkpoint_manifest'] = str(checkpoint_manifest_path(Path(checkpoint_out)).resolve())
            (out / 'review-stream-probe.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--max-calls', type=int, default=8)
    parser.add_argument('--live', action='store_true')
    parser.add_argument('--model-transport', choices=('ollama','luna'), default='ollama',
                        help='Live defaults to the configured private/loopback Ollama. Luna requires explicit destination-specific evidence transfer authorization.')
    parser.add_argument('--checkpoint-out', default=None,
                        help='save the mutated temporary SQLite copy and a matching manifest')
    parser.add_argument('--resume-checkpoint', default=None,
                        help='resume from a prior checkpoint copy; the original DB is never opened for writes')
    parser.add_argument('--retry-model-failure', action='store_true',
                        help='explicit test-only retry of a preserved output schema/budget/contract failure after a correction')
    args = parser.parse_args()
    result = run(Path(args.database), args.case_id, args.batch_id, Path(args.out), args.max_calls, args.live,
                 checkpoint_out=Path(args.checkpoint_out) if args.checkpoint_out else None,
                 resume_checkpoint=Path(args.resume_checkpoint) if args.resume_checkpoint else None,
                 model_transport=args.model_transport, retry_model_failure=args.retry_model_failure)
    print(json.dumps({'out': str(Path(args.out).resolve()), 'mode': result['mode'],
                      'batch_status': result['batch_status'], 'counts': result['counts']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
