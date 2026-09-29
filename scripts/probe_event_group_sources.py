"""Read-only replay of retained event groups; no model, worker or image access."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.check_dossier_input import backup_readonly
from workbench.event_groups import grouping_key, initialize, merge
from workbench.file_hashes import sha256_file


def replay(database,analysis_root,case_id,observation_ids,out):
    database=Path(database).resolve();analysis_root=Path(analysis_root).resolve();out=Path(out).resolve()
    out.mkdir(parents=True,exist_ok=False)
    snapshot=out/'source-paused.sqlite3'
    backup_readonly(database,snapshot)
    db=sqlite3.connect(snapshot.as_uri()+'?mode=ro',uri=True)
    case=json.loads(db.execute('SELECT body FROM records WHERE id=? AND kind=?',(case_id,'case')).fetchone()[0])
    if case['status']!='paused':raise ValueError('Require a paused source case')
    selected={};ledgers=set()
    for oid in observation_ids:
        row=db.execute('SELECT body FROM records WHERE id=? AND kind=? AND case_id=?',(oid,'observation',case_id)).fetchone()
        if not row:raise ValueError('Missing scoped observation')
        o=json.loads(row[0]);selected[grouping_key(o)]=o
        ledger=(analysis_root/o['fields']['artifact_path']).parent.parent/'events.ndjson'
        ledger.resolve().relative_to(analysis_root);ledgers.add(ledger)
    summaries={};excerpts={};record_count=0
    for ledger in sorted(ledgers):
        with ledger.open(encoding='utf-8') as stream:
            for line in stream:
                event=json.loads(line);key=grouping_key(event)
                if key not in selected:continue
                record_count+=1;f=event['fields']
                excerpts[(f['artifact_path'],f.get('byte_offset'),f.get('byte_length'),event.get('timestamp'))]=f.get('excerpt')
                if key not in summaries:summaries[key]=deepcopy(event);initialize(summaries[key])
                else:merge(summaries[key],event)
    checks=[];hashes={}
    for key,original in selected.items():
        if key not in summaries:raise ValueError('Source event group absent from ledger')
        fields=summaries[key]['fields'];anchors=[]
        for name in ('first_observed_source','last_observed_source','last_record_source'):
            anchor=fields.get(name)
            if not anchor:continue
            file=(analysis_root/anchor['artifact_path']).resolve();file.relative_to(analysis_root)
            digest=hashes.setdefault(str(file),sha256_file(file))
            with file.open('rb') as raw:
                raw.seek(anchor['byte_offset']);data=raw.read(anchor['byte_length'])
            expected=excerpts[(anchor['artifact_path'],anchor['byte_offset'],anchor['byte_length'],anchor['timestamp'])]
            valid=digest==anchor['source_sha256'] and (expected is None or expected in data.decode('utf-8',errors='replace'))
            anchors.append({'name':name,'valid':valid,'anchor':anchor})
        old=original['fields'];legacy=None
        if old.get('last_byte_offset') is not None:
            file=(analysis_root/old['artifact_path']).resolve();file.relative_to(analysis_root)
            with file.open('rb') as raw:raw.seek(old['last_byte_offset']);legacy=raw.readline(4096).decode('utf-8',errors='replace')
        checks.append({'observation_id':original['id'],'occurrences_before':old.get('occurrences'),
            'occurrences_after':fields['occurrences'],'first_before':old.get('first_observed'),
            'first_after':fields['first_observed'],'last_before':old.get('last_observed'),
            'last_after':fields['last_observed'],'legacy_last_locator_line':legacy,
            'flat_last_offset_removed':'last_byte_offset' not in fields,'anchors':anchors})
    result={'case_id':case_id,'source_snapshot_sha256':sha256_file(snapshot),
        'source_snapshot_status':case['status'],'source_ledger_sha256':{str(p):sha256_file(p) for p in ledgers},
        'records_replayed':record_count,'worker_calls':0,'model_calls':0,'source_writes':0,
        'checks':checks,'passed':bool(checks) and all(x['anchors'] and all(a['valid'] for a in x['anchors'])
            and x['occurrences_before']==x['occurrences_after'] and x['first_before']==x['first_after']
            and x['last_before']==x['last_after'] and x['flat_last_offset_removed'] for x in checks)}
    (out/'event-group-replay.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({k:v for k,v in result.items() if k not in ('checks','source_ledger_sha256')},ensure_ascii=False))
    if not result['passed']:raise ValueError('Replay source verification failed')


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--analysis-root',required=True,type=Path)
    parser.add_argument('--case-id',required=True)
    parser.add_argument('--observation-id',required=True,action='append')
    parser.add_argument('--out',required=True,type=Path)
    args=parser.parse_args()
    replay(args.database,args.analysis_root,args.case_id,args.observation_id,args.out)
