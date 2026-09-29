"""Read-only, case-scoped investigation audit. No model, worker or network calls.

Counts include failed/repair attempts and physical versus logical work. No raw
paths, queries, excerpts or credentials are emitted. Semantic precision and
critical recall require a separately curated analyst oracle; unknown is null.
"""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))


def audit(rows,oracle=None):
    from workbench.retrieval import fingerprint_scope
    from workbench.semantic_contract import digest
    kinds=defaultdict(list)
    for row in rows:kinds[row['kind']].append(row)
    tasks={r['id']:r for r in kinds['task'] if not r.get('superseded')}
    def current(r):
        t=tasks.get(r.get('task_id'))
        return bool(t and r.get('generation',0)==t.get('retry_generation',0))
    jobs=[j for j in kinds['investigation_job'] if current(j)]
    labels={}
    def label(category,value):
        if value is None:return None
        key=(category,str(value))
        if key not in labels:labels[key]=f'{category}-{1+sum(k[0]==category for k in labels):03d}'
        return labels[key]
    groups=defaultdict(list);traces=[]
    for j in jobs:
        scope=fingerprint_scope(j['request']);result=j.get('result_scope',{})
        identity=[j.get('evidence_id'),j.get('task_id'),j.get('generation',0),j.get('source_run'),scope]
        groups[digest(identity)].append(j)
        if j['request'].get('tool')!='read_file' or result.get('status') not in ('failed','error'):continue
        request={k:(label(k,v) if k in ('path','inode') else v) for k,v in scope.items()}
        traces.append({'job':label('job',j['id']),'source_run':label('run',j.get('source_run')),
            'effective_request':request,'ignored_limit':j['request'].get('limit'),
            'ignored_source_offset':j['request'].get('source_offset'),
            'status':result.get('status'),'complete':result.get('complete'),
            'failure_code':(result.get('failure') or {}).get('code','legacy_untyped'),
            'observation_count':len(j.get('observation_ids',[])),
            'logical_contracts':len(j.get('contracts',[])),
            'elapsed_seconds':result.get('elapsed_seconds'),
            'absence_can_refute_history':False})
    receipts=kinds['receipt'];inputs=kinds['review_input']+kinds['synthesis_input']+kinds['falsifier_input']
    attempts=[r for r in receipts if r.get('model_request_attempted') is True or r.get('request_attempted') is True]
    usage=Counter()
    # Account provider costs once per transport identity (parent summaries may
    # repeat their original metadata). No missing usage is fabricated as zero.
    seen=set();metered=0
    for r in receipts:
        metadata=r.get('model_metadata') or r
        transport=metadata.get('luna_test') or metadata.get('transport') or {}
        # Failure receipts may name a transport without a nested request record.
        if not isinstance(transport,dict):transport={}
        identity=transport.get('request_id') or metadata.get('request_id') or r['id']
        u=metadata.get('usage')
        if not isinstance(u,dict) or identity in seen:continue
        seen.add(identity);metered+=1
        usage.update({k:v for k,v in u.items() if isinstance(v,(int,float)) and not isinstance(v,bool)})
    findings=[r.get('finding',{}) for r in kinds['dossier'] if current(r) and r.get('status')=='reviewed']
    cited={i for f in findings for i in f.get('observation_ids',[])}
    semantics={'claim_precision':None,'critical_recall':None,
        'scope':'Analyst-curated recoverable critical items and correctness labels required. Counts do not measure semantic understanding.'}
    if oracle:
        judgments=[x for x in oracle.get('accepted_claim_annotations',[]) if isinstance(x.get('correct'),bool)]
        if judgments:semantics['claim_precision']=sum(x['correct'] for x in judgments)/len(judgments)
        recoverable=[x for x in oracle.get('critical_items',[]) if x.get('recoverable') is True]
        if recoverable:
            semantics['critical_recall']=sum(bool(set(x['observation_ids'])&cited) for x in recoverable)/len(recoverable)
        semantics.update(correctness_annotation_count=len(judgments),recoverable_critical_count=len(recoverable),
            critical_recall_scope='Citation coverage of annotated critical items, not successful interpretation.')
    questions=[q for q in kinds['case_question'] if current(q)]
    intents=[i for i in kinds['test_intent'] if current(i.get('scope',{}))]
    return {'schema_version':'question-audit-1','scope':'Current case/generation; original database read-only.',
        'counts':{k:len(v) for k,v in kinds.items() if k in ('observation','dossier','receipt','review_input','synthesis_input')},
        'question_states':dict(Counter(q.get('status','unknown') for q in questions)),
        'test_states':dict(Counter(i.get('status','unknown') for i in intents)),
        'test_assessments':dict(Counter(i.get('assessment_status','unassessed') for i in intents)),
        'physical_jobs':len(jobs),'logical_result_uses':len(kinds['test_result_use']),
        'same_effective_request_extra_executions':sum(len(g)-1 for g in groups.values()),
        'same_effective_request_limit':'Includes legitimate transient retries. New ranges/objects stay distinct; not automatically waste.',
        'typed_transient_retry_jobs':sum(':transient-retry:' in j.get('fingerprint','') for j in jobs),
        'input_reservations_including_repairs':len(inputs),
        'model_receipts_explicitly_attempted':len(attempts),
        'rejected_or_error_receipts':dict(Counter(r.get('receipt_type') for r in receipts if 'error' in str(r.get('receipt_type','')))),
        'metered_receipts':metered,'provider_usage':dict(usage) if metered else None,
        'provider_usage_limit':'DB receipt accounting; authoritative unique transport receipts must be reconciled separately.',
        'failed_read_traces':traces,'analyst_oracle_metrics':semantics}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',type=Path,required=True);p.add_argument('--case-id',required=True)
    p.add_argument('--oracle',type=Path);p.add_argument('--out',type=Path)
    p.add_argument('--transport-root',type=Path,help='Optional authoritative test transport receipts, including rejected responses')
    a=p.parse_args()
    with sqlite3.connect(a.database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        db.execute('PRAGMA query_only=ON')
        rows=[json.loads(r[0]) for r in db.execute('SELECT body FROM records WHERE case_id=? ORDER BY created_at,id',(a.case_id,))]
    if not rows:raise SystemExit('Case not found; no cross-case fallback')
    result=audit(rows,json.loads(a.oracle.read_text()) if a.oracle else None)
    if a.transport_root:
        receipts=[json.loads(p.read_text()) for p in sorted((a.transport_root/'luna-transport-receipts').glob('*.json'))]
        unique={r['id']:r for r in receipts};usage=Counter()
        for r in unique.values():usage.update({k:v for k,v in (r.get('usage') or {}).items() if isinstance(v,(int,float))})
        result['test_transport']={'unique_receipts':len(unique),
            'models':dict(Counter(r.get('model') for r in unique.values())),
            'roles':dict(Counter(r.get('role') for r in unique.values())),
            'provider_usage':dict(usage),'wall_seconds_sum':sum(r.get('wall_time_seconds',0) for r in unique.values()),
            'scope':'Includes rejected outputs and repair requests. Summed request time is not case elapsed time or semantic quality.'}
    output=json.dumps(result,ensure_ascii=False,indent=2)
    if a.out:
        a.out.parent.mkdir(parents=True,exist_ok=True)
        with a.out.open('x') as f:f.write(output+'\n')
        print(a.out.resolve())
    else:print(output)


if __name__=='__main__':main()
