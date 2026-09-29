"""Read-only audit of every selected quote against its canonical retained field.

This checks presentation binding, not semantic sufficiency or acquired-file
coverage. No Store, model, worker or network is instantiated.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.review_stream import resolved, span_rows


def audit(database,stream_id):
    with sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True) as db:
        stream=json.loads(db.execute('select body from records where id=? and kind=?',
                                    (stream_id,'review_stream')).fetchone()[0])
        pages=[json.loads(raw) for raw, in db.execute('select body from records where kind=?',('review_page',))]
    canonical={o['id']:o for o in resolved(stream['canonical'])['observations']}
    checked=[];errors=[];metadata_checked=[]
    for page in pages:
        if page.get('stream_id')!=stream_id or not page.get('selected_pack'):continue
        for row in resolved(page['selected_pack'])['observations']:
            if row.get('projection_omissions',{}).get('replacement')=='model_selected_metadata_only':
                expected={k:v for k,v in canonical[row['id']]['fields'].items() if k not in ('excerpt','excerpt_spans')}
                valid=row['fields']==expected and not row.get('source_span') and not row.get('source_spans')
                metadata_checked.append({'page_id':page['id'],'observation_id':row['id'],'valid':valid})
                if not valid:errors.append(row['id'])
            for sid,source in span_rows(row):
                span=source['source_span']
                raw=canonical[source['id']]['fields']['excerpt'].encode('utf-8')
                quote=source['fields']['excerpt'].encode('utf-8')
                valid=(span.get('coordinate_basis')=='canonical_field_utf8'
                    and span['full_sha256']==hashlib.sha256(raw).hexdigest()
                    and span['sha256']==hashlib.sha256(quote).hexdigest()
                    and raw[span['byte_start']:span['byte_end']]==quote)
                checked.append({'page_id':page['id'],'span_id':sid,'valid':valid})
                if not valid:errors.append(sid)
    return {'database':str(Path(database).resolve()),'stream_id':stream_id,
            'spans_checked':len(checked),'checks':checked,'metadata_selections_checked':len(metadata_checked),
            'metadata_checks':metadata_checked,'errors':errors,
            'passed':not errors,'model_calls':0,'worker_calls':0,'source_writes':0,
            'scope':'Exact retained-field quote binding only; not semantic relevance, source independence or full acquired-byte verification.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--stream-id',required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    if args.out.exists():raise ValueError('Audit output must be new')
    result=audit(args.database,args.stream_id)
    args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k!='checks'}))
    raise SystemExit(0 if result['passed'] else 1)
