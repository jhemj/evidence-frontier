#!/usr/bin/env python3
"""Probe one investigator recovery plan without dispatching proposed tools.

The source SQLite database is opened read-only and copied with sqlite's backup
API before Store/Controller initialization. The output directory must be new.
"""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def copy_database(source, destination):
    source_uri = Path(source).resolve().as_uri() + "?mode=ro"
    src = sqlite3.connect(source_uri, uri=True)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def original_binding(source, case_id):
    uri = Path(source).resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True)
    try:
        row = db.execute("select body from records where kind='case' and id=?", (case_id,)).fetchone()
        if not row:
            raise ValueError('case-id를 찾을 수 없습니다.')
        return json.loads(row[0]).get('runtime_binding')
    finally:
        db.close()


def main(database, case_id, out):
    from workbench.controller import Controller
    from workbench.investigation_graph import Investigation
    from workbench.models import ProviderConfig
    from workbench.provider import Provider
    from workbench.store import Store, now
    from workbench.investigator import consult

    out_path = Path(out).resolve()
    if out_path.exists():
        raise ValueError('--out은 존재하지 않는 신규 폴더여야 합니다.')
    out_path.mkdir(parents=True)
    original = original_binding(database, case_id)
    if not original or original.get('model_digest') in (None, '', 'unverified'):
        raise ValueError('원본 runtime binding에 검증 가능한 model_digest가 없습니다.')

    with tempfile.TemporaryDirectory(prefix='investigator-probe-') as td:
        copy_path = Path(td) / 'case.sqlite3'
        copy_database(database, copy_path)
        store = Store(copy_path)
        controller = Controller(store, Path(td))
        case = store.get(case_id, 'case')
        target_os=case.get('target_os','linux')
        if target_os not in ('linux','windows'):
            raise ValueError('지원하지 않는 조사 대상 OS입니다.')
        task = next(t for t in reversed(store.list('task', case_id))
                    if t.get('action') == f'{target_os}_investigate' and t.get('evidence_id'))
        evidence = store.get(task['evidence_id'], 'evidence')
        if not evidence.get('connected', True):
            raise ValueError('investigate task의 evidence가 연결되어 있지 않습니다.')
        run = next(r for r in reversed(store.list('investigation_run', case_id)) if r.get('task_id') == task['id'])
        config = store.list('config')[-1]['provider']
        if ProviderConfig.model_validate(original.get('provider', {})).model_dump() != ProviderConfig.model_validate(config).model_dump():
            raise ValueError('원본 runtime binding의 provider 설정과 현재 provider 설정이 다릅니다.')

        # Recovery-only settings are intentionally isolated to this copy.
        for plan in store.list('investigation_plan', case_id):
            if plan.get('task_id') == task['id'] and not plan.get('assessed'):
                store.update(plan['id'], assessed=True)
        store.update(run['id'], model_calls=0, max_model_calls=12,
                     plan_domain_batch_size=1, consecutive_plan_failures=1)
        run = store.get(run['id'])
        inv = Investigation(controller, case_id, evidence, task)
        captured = {}

        def proposal_model(question, pack, _run, _purpose):
            captured['question'] = question
            captured['pack'] = pack
            old_digest = os.environ.get('FRONTIER_MODEL_DIGEST')
            os.environ['FRONTIER_MODEL_DIGEST'] = original['model_digest']
            try:
                output, receipt = consult(config, question, pack, role='investigator',
                                         provider_factory=Provider)
            except Exception as exc:
                metadata = getattr(exc, 'metadata', {})
                captured['error'] = {
                    'type': type(exc).__name__, 'category': getattr(exc, 'category', 'error'),
                    'message': str(exc), 'metadata': metadata,
                    'raw_output': getattr(exc, 'raw_output', None),
                }
                return None
            finally:
                if old_digest is None:
                    os.environ.pop('FRONTIER_MODEL_DIGEST', None)
                else:
                    os.environ['FRONTIER_MODEL_DIGEST'] = old_digest
            captured['output'] = output
            captured['receipt'] = receipt
            return output

        inv.model = proposal_model
        try:
            result = inv.plan({'run_id': run['id']})
        except Exception as exc:
            captured['plan_error'] = {'type': type(exc).__name__, 'message': str(exc)}
            result = None

        manifest = {
            'created_at':now(), 'probe_source_sha256':controller.runtime_code,
            'scope':'Proposal-only recovery probe on a temporary database copy; not an E2E or completed source assessment.',
            'database': str(Path(database).resolve()), 'case_id': case_id,
            'task_id': task['id'], 'run_id': run['id'],
            'original_runtime_binding': original,
            'provider': config,
            'copy_only_overrides': {'model_calls': 0, 'max_model_calls': 12,
                                    'plan_domain_batch_size': 1,
                                    'consecutive_plan_failures': 1,
                                    'existing_unassessed_plans_marked_assessed': True},
            'dispatch_performed': False, 'plan_result': result,
        }
        (out_path / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        payload = {'question': captured.get('question'), 'evidence_pack': captured.get('pack'),
                   'output': captured.get('output'), 'receipt': captured.get('receipt'),
                   'error': captured.get('error'), 'plan_error': captured.get('plan_error')}
        (out_path / 'proposal.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    print(json.dumps({'out': str(out_path), 'dispatch_performed': False,
                      'error': {k:v for k,v in (captured.get('error') or {}).items() if k in ('type','category','message','metadata')},
                      'plan_error': captured.get('plan_error'),
                      'output_present': 'output' in captured}, ensure_ascii=False))
    return 1 if captured.get('error') or captured.get('plan_error') else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args()
    raise SystemExit(main(args.database, args.case_id, args.out))
