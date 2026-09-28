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
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.check_dossier_input import backup_readonly  # noqa: E402


class BoundaryStop(RuntimeError):
    pass


def digest_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


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


def run(database: Path, case_id: str, batch_id: str, out: Path, max_calls: int, live: bool) -> dict:
    if max_calls <= 0:
        raise ValueError('--max-calls must be greater than zero')
    if out.exists():
        raise ValueError('--out must name a new directory')
    out.mkdir(parents=True)
    source_hash = digest_file(database)
    with tempfile.TemporaryDirectory(prefix='review-stream-probe-') as td:
        local_db = Path(td) / 'case.sqlite3'
        backup_readonly(database, local_db)
        from workbench.controller import Controller
        from workbench.store import Store
        import workbench.dossiers as dossiers
        import workbench.discovery as discovery
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
        for other in store.list('dossier_batch', case_id):
            if other['id'] != batch_id and other.get('status') not in ('done', 'failed', 'split'):
                store.update(other['id'], status='done')
        store.update(batch_id, status='pending', attempts=0, active_attempt_id=None,
                     validation_feedback=None, review_stream_id=None)

        old_guard = runtime_contract.guard
        old_admit = discovery.admit
        old_worker = dossiers.worker_request
        old_seed = dossiers.seed
        old_consult = dossiers.consult
        runtime_contract.guard = lambda *args, **kwargs: None  # TEST_ONLY copied DB
        discovery.admit = lambda *args, **kwargs: False
        dossiers.seed = lambda *args, **kwargs: None
        dossiers.worker_request = lambda *args, **kwargs: (_ for _ in ()).throw(
            BoundaryStop('worker boundary disabled for review-stream probe'))
        if not live:
            dossiers.consult = lambda *args, **kwargs: (_ for _ in ()).throw(
                BoundaryStop('model boundary disabled; use --live explicitly'))
        else:
            from scripts.run_assisted_e2e import install_assisted_provider
            install_assisted_provider(out, luna_roles=('judgment',), transport_root=out / 'transport')

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
                if current.get('status') in ('done', 'input_projection_blocked', 'model_failed'):
                    break
        finally:
            runtime_contract.guard = old_guard; discovery.admit = old_admit
            dossiers.worker_request = old_worker; dossiers.seed = old_seed; dossiers.consult = old_consult

        receipts = [r for r in store.list('receipt', case_id)
                    if r.get('batch_id') == batch_id and r.get('id') not in baseline_receipt_ids]
        diagnostics = [r for r in store.list('review_diagnostic', case_id)
                       if r.get('batch_id') == batch_id and r.get('id') not in baseline_diagnostic_ids]
        inputs = [r for r in store.list('review_input', case_id)
                  if r.get('batch_id') == batch_id and r.get('id') not in baseline_input_ids]
        for record in inputs:
            record['input_characters']=len(json.dumps(record.get('pack',{}),ensure_ascii=False,separators=(',',':')))
        result = {
            'mode': 'live' if live else 'boundary_only', 'test_only_guard_bypass': True,
            'external_worker_calls': worker_attempts, 'model_calls': consult_attempts,
            'new_input_records': [{'id':r.get('id'),'phase':r.get('review_stream',{}).get('phase'),
                                   'input_characters':r.get('input_characters')} for r in inputs],
            'database': str(database.resolve()), 'source_sha256_before': source_hash,
            'source_sha256_after': digest_file(database), 'source_unchanged': source_hash == digest_file(database),
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
        }
        if original_output is not None:
            (out/'original-batch-output.json').write_text(
                json.dumps(original_output, ensure_ascii=False, indent=2), encoding='utf-8')
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
    args = parser.parse_args()
    result = run(Path(args.database), args.case_id, args.batch_id, Path(args.out), args.max_calls, args.live)
    print(json.dumps({'out': str(Path(args.out).resolve()), 'mode': result['mode'],
                      'batch_status': result['batch_status'], 'counts': result['counts']}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
