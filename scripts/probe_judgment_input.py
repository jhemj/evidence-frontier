"""One test-only Luna judgment of a copied real batch; never dispatch tools.

This component probe is not an E2E completion or an accepted case judgment.
Source SQLite stays read-only. Proposed follow-up checks are recorded, not run.
"""
import argparse
import copy
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def main(args):
    from scripts import check_dossier_input as diagnostic
    from scripts.run_assisted_e2e import install_assisted_provider
    from workbench import review_context
    from workbench.investigator import consult
    from workbench.provider import Provider
    from workbench.review_validation import errors,check_errors

    out=args.out.resolve()
    if out.exists():raise ValueError('Use a new output directory')
    out.mkdir(parents=True)
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        rows=[json.loads(row[0]) for row in db.execute('SELECT body FROM records ORDER BY created_at,id')]
    raw={r['id']:r for r in rows if r.get('kind')=='observation' and r.get('case_id')==args.case_id}
    replay_issues=[]
    if getattr(args,'input_record_id',None):
        by_id={r['id']:r for r in rows}
        record=by_id[args.input_record_id]
        if (record.get('kind')!='review_input' or record.get('case_id')!=args.case_id or
                record.get('batch_id')!=args.batch_id):
            raise ValueError('Saved input case/batch binding mismatch')
        pack=copy.deepcopy(record['pack'])
        if getattr(args,'rejected_receipt_id',None):
            receipt=by_id[args.rejected_receipt_id]
            if (receipt.get('input_record_id')!=record['id'] or receipt.get('case_id')!=args.case_id or
                    receipt.get('input_sha256')!=record.get('input_sha256')):
                raise ValueError('Receipt does not belong to the saved input')
            previous=receipt.get('output')
            if previous is None:
                diagnostic_record=by_id.get(receipt.get('diagnostic_id'),{})
                if (diagnostic_record.get('kind')!='review_diagnostic' or
                        diagnostic_record.get('input_record_id')!=record['id'] or
                        diagnostic_record.get('case_id')!=args.case_id or
                        diagnostic_record.get('batch_id')!=args.batch_id or
                        diagnostic_record.get('input_sha256')!=record.get('input_sha256')):
                    raise ValueError('Rejected diagnostic does not belong to the saved input')
                previous=diagnostic_record.get('rejected_output')
            if not isinstance(previous,dict) or 'findings' not in previous:
                raise ValueError('No structured judgment was retained for revalidation')
            previous=copy.deepcopy(previous)
            replay_issues=errors(previous,[d['id'] for d in pack['required_dossiers']],
                pack['allowed_observation_ids'],pack['allowed_observation_ids_by_dossier'],raw,require_literals=True)
            replay_issues+=check_errors(previous,pack.get('executed_checks',[]),pack['allowed_observation_ids_by_dossier'])
            if not replay_issues:raise ValueError('Saved output is not rejected; no repair probe required')
        (out/'saved-input.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    else:
        if not args.runtime_root:raise ValueError('--runtime-root is required to rebuild a batch')
        captured={};original=review_context.fit
        def capture(pack,maximum=36000):
            original(pack,maximum)
            captured['pack']=copy.deepcopy(pack)
        review_context.fit=capture
        try:
            diagnostic.main(args.database,args.case_id,args.batch_id,out/'input',args.runtime_root,
                            rebuild_child_from_parent=True)
        finally:review_context.fit=original
        if 'pack' not in captured:raise ValueError('No bounded model input was produced')
        pack=captured['pack']
    feedback_issues=list(replay_issues)
    if args.feedback:
        previous=json.loads(args.feedback.read_text())
        feedback_issues+=previous['issues']
    if feedback_issues:
        pack['validation_feedback']={'errors':feedback_issues,
            'instruction':'Rejected output is not evidence. Correct schema errors using the published bounds. Reassess from the provided observations. Only allowed_observation_ids may be cited. Never substitute IDs or remove citations merely to pass validation.'}
    review_context.fit(pack)
    (out/'submitted-input.json').write_text(review_context.serialize(pack),encoding='utf-8')
    (out/'replay-issues.json').write_text(json.dumps(replay_issues,ensure_ascii=False,indent=2),encoding='utf-8')
    configs=[r for r in rows if r.get('kind')=='config']
    config=configs[-1]['provider']
    install_assisted_provider(out,luna_roles=('judgment',),transport_root=out/'transport')
    result,receipt=consult(config,
        '각 required_dossiers의 원문과 이전 판단·검사 결과를 대조하세요. 설정·호출·실행·성공을 구별하고 '
        '근거 범위를 넘는 확정을 하지 마세요. 각 단서당 하나의 판단과 실행된 모든 논리 검사 계약의 평가를 반환하세요.',
        pack,role='judgment',provider_factory=Provider)
    ids=pack['allowed_observation_ids'];allowed=pack['allowed_observation_ids_by_dossier']
    issues=errors(result,[d['id'] for d in pack['required_dossiers']],ids,allowed,raw,require_literals=True)
    issues+=check_errors(result,pack.get('executed_checks',[]),allowed)
    payload={'test_only':True,'scope':'single judgment component; no follow-up tools, case writes or E2E completion',
        'batch_id':args.batch_id,'input_characters':len(review_context.serialize(pack)),
        'input_record_id':getattr(args,'input_record_id',None),'replayed_rejection':replay_issues,
        'issues':issues,'output':result,'receipt':receipt,'worker_calls':0,'original_database_modified':False}
    (out/'result.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'out':str(out),'input_characters':payload['input_characters'],
                      'validation_issues':issues,'worker_calls':0},ensure_ascii=False))
    return bool(issues)


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--database',required=True,type=Path)
    p.add_argument('--case-id',required=True)
    p.add_argument('--batch-id',required=True)
    p.add_argument('--runtime-root',type=Path)
    p.add_argument('--input-record-id',help='Replay an exact saved round instead of rebuilding a split child')
    p.add_argument('--rejected-receipt-id',help='Revalidate this input-bound output and provide its errors as feedback')
    p.add_argument('--out',required=True,type=Path)
    p.add_argument('--feedback',type=Path)
    raise SystemExit(main(p.parse_args()))
