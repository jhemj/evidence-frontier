#!/usr/bin/env python3
"""Replay claim review on a read-only SQLite backup, never on the source case.

No worker, evidence executable, or discovered endpoint is contacted. Live mode
requires an explicit trusted private Ollama destination and pinned model digest.
The report is a component replay, not a fresh E2E or a case-level verdict.
"""
import argparse
import hashlib
import ipaddress
import json
import os
import sqlite3
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.check_dossier_input import backup_readonly
from workbench.claim_review import build_pack,input_hash,validate_scope
from workbench.controller import Controller
from workbench.investigator import consult
from workbench.review_context import model_view_size
from workbench.review_stream import resolved
from workbench.store import Store


def observation_digest(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    try:
        h=hashlib.sha256()
        for row in db.execute("SELECT body FROM records WHERE kind='observation' ORDER BY id"):
            h.update(row[0].encode());h.update(b'\n')
        return h.hexdigest()
    finally:db.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--claim',action='append',required=True)
    parser.add_argument('--live',action='store_true')
    parser.add_argument('--model-url')
    parser.add_argument('--model-digest')
    args=parser.parse_args()
    if args.out.exists():raise SystemExit('Preserve previous replay: select a new output directory.')
    if args.live:
        if os.environ.get('MODEL_RELAY_URL'):raise SystemExit('External relay is forbidden for this replay.')
        url=urlparse(args.model_url or '')
        try:private=ipaddress.ip_address(url.hostname).is_private
        except ValueError:private=False
        if not private or url.scheme not in ('http','https') or url.username or url.password or not args.model_digest:
            raise SystemExit('Live mode requires explicit private Ollama URL and model digest.')
    before=observation_digest(args.source)
    args.out.mkdir(parents=True)
    preserved=args.out/'source-paused.sqlite3';backup_readonly(args.source,preserved)
    snapshot_sha=hashlib.sha256(preserved.read_bytes()).hexdigest()
    replay=args.out/'replay.sqlite3';backup_readonly(preserved,replay)
    store=Store(replay);controller=Controller(store,args.out)
    config=store.list('config')[-1]['provider']
    if args.live:
        if config.get('protocol')!='ollama' or config.get('base_url')!=args.model_url:
            raise SystemExit('Source configuration does not match the explicitly approved private destination.')
        import httpx
        response=httpx.get(args.model_url.rstrip('/')+'/api/tags',timeout=20);response.raise_for_status()
        match=next((m for m in response.json()['models'] if m['name']==config['model']),None)
        if not match or match['digest']!=args.model_digest:raise SystemExit('Model digest mismatch')
    results=[]
    for claim_id in args.claim:
        item=store.get(claim_id,'claim')
        jobs=[j for j in store.list('investigation_job',item['case_id']) if j.get('task_id')==item.get('task_id')]
        pack=build_pack(controller,item,jobs,maximum=33952)
        shown={o['id'] for o in resolved(pack)['observations']}
        old=[r for r in store.list('receipt',item['case_id'])
             if r.get('claim_id')==claim_id and r.get('receipt_type')=='automatic_falsifier']
        previous=old[-1] if old else {}
        previous_ids={s['id'] for s in previous.get('selection_audit',{}).get('selected',[])}
        row={'claim_id':claim_id,'input_sha256':input_hash(pack),'model_view_characters':model_view_size(pack),
             'required_ids':pack['review_scope']['required_observation_ids'],'shown_ids':sorted(shown),
             'missing_required':sorted(set(pack['review_scope']['required_observation_ids'])-shown),
             'previous_receipt_id':previous.get('id'),
             'previous_missing_primary':sorted(set(item['observation_ids'])-previous_ids),
             'input_pack':pack}
        (args.out/(claim_id+'-input.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in row.items() if k!='input_pack'},ensure_ascii=False),flush=True)
        if args.live:
            question=('실제로 제시된 원문과 실행 검사만 바탕으로 다음 주장의 대안 설명·상충 근거·미완료 검사를 검토하세요. '
                '같은 출처 재읽기는 독립 검증이 아니며 기록된 명령과 실행 성공은 다릅니다: '+item['text']+
                '\ncontradicting_observation_ids에는 허용 ID만 정확히 복사하세요: '+', '.join(sorted(shown)))
            output,receipt=consult(config,question,pack,'falsifier')
            if set(output['contradicting_observation_ids'])-shown:raise ValueError('Unknown model citation')
            validate_scope(controller,item,pack)
            row['output']=output;row['receipt']=receipt
            (args.out/(claim_id+'-result.json')).write_text(json.dumps(row,ensure_ascii=False,indent=2))
            print(json.dumps({'claim_id':claim_id,'output':output,'usage':receipt.get('usage')},ensure_ascii=False),flush=True)
        results.append({k:v for k,v in row.items() if k not in ('input_pack','receipt')})
    after=observation_digest(args.source)
    report={'scope':'claim-input replay; no workers or fresh E2E; no source-case writes',
            'live':args.live,'model_digest':args.model_digest,'source_snapshot_sha256':snapshot_sha,
            'source_observation_digest_before':before,'source_observation_digest_after':after,
            'source_observations_unchanged':before==after,'claims':results}
    (args.out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if before!=after:raise SystemExit('Source observations changed during replay')
    print(json.dumps({k:v for k,v in report.items() if k!='claims'},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
