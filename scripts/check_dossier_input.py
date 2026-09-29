#!/usr/bin/env python3
"""Read-only dossier-input budget diagnostic.

The source SQLite file is opened read-only and copied with sqlite backup before
Frontier Store/Controller code is initialized. No model or worker request is
allowed; the consult/worker boundaries are replaced by fail-fast probes.
"""
import argparse
import copy
import importlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def backup_readonly(source, destination):
    src = sqlite3.connect(Path(source).resolve().as_uri() + '?mode=ro', uri=True)
    try:
        dst = sqlite3.connect(destination)
        try:
            src.backup(dst)
            dst.commit()
        finally:
            dst.close()
    finally:
        src.close()


def serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def locators(pack):
    identity=('source_sha256','partition_offset','volume_id','snapshot_id','os_instance',
              'target_os','time_basis','timestamp','byte_offset','line','inode','path')
    result=[]
    for o in pack.get('observations', []):
        fields=o.get('fields',{})
        result.append((o.get('id'),o.get('source_location'),
                       tuple((key, fields.get(key, o.get(key))) for key in identity)))
    return result


def field_membership(pack):
    return [(o.get('id'), tuple(sorted(o.get('fields', {}).keys())))
            for o in pack.get('observations', [])]


def metadata_snapshot(pack, context_module):
    expanded=copy.deepcopy(pack)
    expand=getattr(context_module,'expand_metadata',None)
    if expand:expand(expanded)
    protected=('context_limit','time_semantics','time_basis','time_record','time_kind','time_type',
               'source_sha256','source_complete','source_location','source_offset','byte_offset',
               'image_file_byte_offset','byte_length','source_range_start','line','inode',
               'partition_offset','os_instance','volume_id','snapshot_id','locator_basis','path')
    return [(o.get('id'),{key:o.get(key) for key in protected},
             {key:o.get('fields',{}).get(key) for key in protected})
            for o in expanded.get('observations', [])]


def top_sizes(pack):
    return {key: len(serialized(value)) for key, value in pack.items()}


def observation_metrics(pack):
    rows=[]
    field_totals={}
    for observation in pack.get('observations', []):
        fields=observation.get('fields', {})
        sizes={key: len(serialized(value)) for key, value in fields.items()}
        for key,size in sizes.items():field_totals[key]=field_totals.get(key,0)+size
        rows.append({'id':observation.get('id'), 'characters':len(serialized(observation)),
                     'field_characters':sorted(sizes.items(),key=lambda pair:pair[1],reverse=True)[:5]})
    return {'count':len(rows), 'total_characters':sum(row['characters'] for row in rows),
            'max_observation_characters':max((row['characters'] for row in rows),default=0),
            'top_field_totals':sorted(field_totals.items(),key=lambda pair:pair[1],reverse=True)[:12],
            'largest_observations':sorted(rows,key=lambda row:row['characters'],reverse=True)[:5]}


def load_frozen_fit(runtime_root, temp_root):
    """Load the frozen runtime review_context under an isolated package name."""
    alias='frozen_runtime_'+uuid.uuid4().hex
    package = Path(temp_root) / alias
    shutil.copytree(Path(runtime_root) / 'workbench', package / 'workbench')
    (package / '__init__.py').write_text('', encoding='utf-8')
    spec_name = alias+'.workbench.review_context'
    sys.path.insert(0, str(package.parent))
    try:
        return importlib.import_module(spec_name)
    finally:
        sys.path.pop(0)


def main(database, case_id, batch_id, out, runtime_root=None, rebuild_child_from_parent=False):
    from workbench.controller import Controller
    from workbench.store import Store
    import workbench.dossiers as dossiers
    import workbench.discovery as discovery
    import workbench.runtime_contract as runtime_contract
    import workbench.review_context as workspace_context

    out_path = Path(out).resolve()
    if out_path.exists():
        raise ValueError('--out은 존재하지 않는 신규 폴더여야 합니다.')
    out_path.mkdir(parents=True)
    runtime_root = runtime_root or str(Path(__file__).resolve().parents[1])

    with tempfile.TemporaryDirectory(prefix='dossier-input-probe-') as td:
        local_db = Path(td) / 'case.sqlite3'
        backup_readonly(database, local_db)
        store = Store(local_db)
        controller = Controller(store, Path(td))
        batch = store.get(batch_id, 'dossier_batch')
        if batch.get('case_id') != case_id:
            raise ValueError('batch와 case-id가 일치하지 않습니다.')
        task = store.get(batch['task_id'], 'task')
        evidence = store.get(batch['evidence_id'], 'evidence')
        if task.get('evidence_id') != evidence['id']:
            raise ValueError('batch의 task/evidence 범위가 일치하지 않습니다.')

        rebuild = None
        if rebuild_child_from_parent:
            parent_id = batch.get('parent_batch_id')
            if not parent_id:
                raise ValueError('--rebuild-child-from-parent requires a split child batch')
            parent = store.get(parent_id, 'dossier_batch')
            if parent.get('case_id') != case_id:
                raise ValueError('child와 parent의 case-id가 일치하지 않습니다.')
            if len(batch.get('dossier_ids', [])) != 1:
                raise ValueError('split child는 정확히 하나의 dossier를 가져야 합니다.')
            # Apply the same inheritance helper used by production splitting,
            # but only to the temporary backup. This avoids inventing a result
            # while reproducing the exact child input after a code change.
            inherited = dossiers.split_context(store, parent, batch['dossier_ids'][0])
            store.update(batch_id, **inherited)
            batch = store.get(batch_id, 'dossier_batch')
            rebuild = {'parent_batch_id': parent_id, 'dossier_id': batch['dossier_ids'][0],
                       'inherited_job_ids': list(batch.get('job_ids', [])),
                       'inherited_deferred_checks': len(batch.get('deferred_checks', []))}

        # Make only the requested batch eligible in the copy. The original
        # status, attempts and queue remain untouched in the source DB.
        for other in store.list('dossier_batch', case_id):
            if other['id'] != batch_id and other.get('status') not in ('done', 'failed', 'split'):
                store.update(other['id'], status='done')
        store.update(batch_id, status='pending', attempts=0)
        # This probe examines the already assembled dossier input. If the
        # frozen batch was waiting on a worker receipt, mark only the copy's
        # listed jobs ingested so finish() reaches the fit boundary; no worker
        # result is fabricated or sent to an external service.
        job_status_before={job_id:store.get(job_id).get('status') for job_id in batch.get('job_ids', [])}
        for job_id in batch.get('job_ids', []):
            job=store.get(job_id)
            if job.get('status')!='ingested':
                store.update(job_id, status='ingested', result_status=job.get('result_status','partial'))
        copied_ingested_job_ids=[job_id for job_id,status in job_status_before.items()
                                if status!='ingested' and store.get(job_id).get('status')=='ingested']

        captured = {}
        original_fit = workspace_context.fit

        def capture_fit(pack, maximum=36000):
            captured['before'] = copy.deepcopy(pack)
            captured['before_characters'] = len(serialized(pack))
            captured['before_top_sizes'] = top_sizes(pack)
            captured['before_observations'] = observation_metrics(pack)
            captured['before_locators'] = locators(pack)
            captured['before_fields'] = field_membership(pack)
            captured['before_metadata'] = metadata_snapshot(pack, workspace_context)
            captured['before_allowlist'] = list(pack.get('allowed_observation_ids', []))
            try:
                result = original_fit(pack, maximum)
            except Exception as exc:
                captured['workspace_error'] = {'type': type(exc).__name__, 'message': str(exc)}
                raise
            finally:
                captured['after'] = copy.deepcopy(pack)
                captured['after_characters'] = len(serialized(pack))
                captured['after_top_sizes'] = top_sizes(pack)
                captured['after_observations'] = observation_metrics(pack)
                captured['after_locators'] = locators(pack)
                captured['after_fields'] = field_membership(pack)
                captured['after_metadata'] = metadata_snapshot(pack, workspace_context)
                captured['after_allowlist'] = list(pack.get('allowed_observation_ids', []))
            return result

        workspace_context.fit = capture_fit
        old_guard = runtime_contract.guard
        runtime_contract.guard = lambda *args, **kwargs: None
        old_consult = dossiers.consult
        old_worker = dossiers.worker_request
        old_seed = dossiers.seed
        old_review_exhausted = dossiers.review_exhausted
        old_admit = discovery.admit
        dossiers.consult = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('model call forbidden'))
        dossiers.worker_request = lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('worker call forbidden'))
        dossiers.seed = lambda *args, **kwargs: None
        dossiers.review_exhausted = lambda *args, **kwargs: False
        discovery.admit = lambda *args, **kwargs: False
        try:
            # finish() itself builds the production pack and stops at fit() when
            # the budget is rejected; no consult/worker boundary can pass.
            try:
                dossiers.finish(controller, case_id, evidence, task)
            except AssertionError as exc:
                captured['boundary_error'] = str(exc)
        finally:
            workspace_context.fit = original_fit
            runtime_contract.guard = old_guard
            dossiers.consult = old_consult
            dossiers.worker_request = old_worker
            dossiers.seed = old_seed
            dossiers.review_exhausted = old_review_exhausted
            discovery.admit = old_admit

        baseline = load_frozen_fit(runtime_root, td)
        baseline_pack = copy.deepcopy(captured.get('before', {}))
        baseline_result = {'before_characters': len(serialized(baseline_pack)),
                           'before_top_sizes': top_sizes(baseline_pack),
                           'before_observations': observation_metrics(baseline_pack),
                           'before_fields': field_membership(baseline_pack),
                           'before_metadata': metadata_snapshot(baseline_pack, baseline),
                           'error': None}
        try:
            baseline.fit(baseline_pack)
        except Exception as exc:
            baseline_result['error'] = {'type': type(exc).__name__, 'message': str(exc)}
        baseline_result.update(after_characters=len(serialized(baseline_pack)),
                               after_top_sizes=top_sizes(baseline_pack),
                               after_observations=observation_metrics(baseline_pack),
                               after_fields=field_membership(baseline_pack),
                               after_metadata=metadata_snapshot(baseline_pack, baseline),
                               ids_preserved=[o.get('id') for o in baseline_pack.get('observations', [])] ==
                               [o.get('id') for o in captured.get('before', {}).get('observations', [])],
                               locators_preserved=locators(baseline_pack) == captured.get('before_locators', []),
                               fields_membership_preserved=baseline_result['before_fields'] == field_membership(baseline_pack),
                               shared_metadata_preserved=baseline_result['before_metadata'] == metadata_snapshot(baseline_pack, baseline),
                               allowlist_preserved=list(baseline_pack.get('allowed_observation_ids', [])) ==
                               captured.get('before_allowlist', []))
        result = {
            'database': str(Path(database).resolve()), 'case_id': case_id, 'batch_id': batch_id,
            'model_boundary_reached': bool(captured.get('boundary_error')),
            'external_calls': 0,
            'boundary_error': captured.get('boundary_error'),
            'assembler': 'current_workspace', 'workspace_fit': 'current_workspace',
            'workspace': {k: captured.get(k) for k in ('before_characters','after_characters','before_top_sizes','after_top_sizes','workspace_error')},
            'workspace_observations': {'before': captured.get('before_observations'), 'after': captured.get('after_observations')},
            'workspace_ids_preserved': captured.get('before_locators') == captured.get('after_locators'),
            'workspace_fields_membership_preserved': captured.get('before_fields') == captured.get('after_fields'),
            'workspace_shared_metadata_preserved': captured.get('before_metadata') == captured.get('after_metadata'),
            'workspace_allowlist_preserved': captured.get('before_allowlist') == captured.get('after_allowlist'),
            'workspace_check_contracts': [
                {'id': x.get('id'), 'observation_ids': len(x.get('observation_ids', [])),
                 'omitted_observations': x.get('omitted_observations', 0),
                 'contracts': len(x.get('contracts', []))} for x in captured.get('before', {}).get('executed_checks', [])
            ],
            'copy_ingested_job_ids': copied_ingested_job_ids,
            'rebuild_child_from_parent': rebuild,
            'baseline_frozen_runtime': {'assembler': 'current_workspace', 'fit': 'frozen_runtime', **baseline_result},
        }
        (out_path / 'diagnostic.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'out': str(out_path), 'model_boundary_reached': result['model_boundary_reached'],
                      'workspace_error': result['workspace'].get('workspace_error'),
                      'baseline_error': result['baseline_frozen_runtime'].get('error')}, ensure_ascii=False))
    # Reaching the fail-fast model boundary is an expected successful probe;
    # only a captured budget failure is a non-zero diagnostic result.
    return 1 if result['workspace'].get('workspace_error') else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', required=True)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--batch-id', required=True)
    parser.add_argument('--out', required=True)
    parser.add_argument('--runtime-root')
    parser.add_argument('--rebuild-child-from-parent', action='store_true')
    args = parser.parse_args()
    raise SystemExit(main(args.database, args.case_id, args.batch_id, args.out, args.runtime_root,
                          args.rebuild_child_from_parent))
