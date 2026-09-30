"""Capture a transactionally consistent, minimal replay; never open writable Store."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.observer_view import digest, project, stamp, validate

KINDS = ('case', 'epoch', 'task', 'evidence', 'claim', 'dossier', 'case_synthesis', 'case_question', 'business_question', 'hypothesis_proposal', 'decision_revision',
         'test_intent', 'test_result_use', 'investigation_job', 'hypothesis',
         'model_reservation', 'review_input', 'synthesis_input', 'receipt', 'report', 'report_finalization')
# Large prompts/results are neither needed nor sent to the display.
REMOVED = ('pack', 'raw_output', 'rejected_output', 'result', 'output', 'model_reference_output',
           'reference_projection', 'text_projection', 'table_projection', 'selection_audit',
           'all_observation_ids', 'related_observation_ids', 'revision_history')
REF_FIELDS = {'observation_ids', 'counterevidence_ids', 'contradicting_observation_ids',
              'supporting_evidence_ids', 'refuting_evidence_ids', 'original_observation_ids',
              'required_observation_ids', 'baseline_observation_ids', 'trigger_observation_ids', 'triggering_evidence_ids'}


def scenario_source_current(connection,case_id,hypothesis,originals):
    """Verify the existing engine digest, including related uncited sources.

    Stream exact canonical rows from the SAME read transaction. A bounded scan
    that cannot verify the dependency returns unknown, never current. No new
    ranking, model judgment, source payload, or writable cache is produced.
    """
    revision=hypothesis.get('scenario_source_revision')
    refs=sorted(set(hypothesis.get('supporting_evidence_ids',[])+hypothesis.get('refuting_evidence_ids',[])))
    if not revision or not refs:return None
    if any(i not in originals for i in refs):return False
    from workbench.case_memory import _object
    origins={_object(originals[i]) for i in refs}-{None}
    clauses=['id IN ('+','.join('?' for _ in refs)+')'];args=list(refs)
    paths=('evidence_id','fields.partition_offset','fields.os_instance',
           'fields.volume_id','fields.snapshot_id','fields.path')
    for origin in sorted(origins,key=repr):
        clauses.append('('+ ' AND '.join("json_extract(body,'$."+p+"') IS ?" for p in paths)+')')
        args.extend(origin)
    cursor=connection.execute("SELECT id,body FROM records WHERE case_id=? AND kind='observation' AND ("+
        ' OR '.join(clauses)+') ORDER BY id LIMIT 5001',(case_id,*args))
    hasher=hashlib.sha256();hasher.update(b'[');total=0;seen=set()
    for index,(identity,body) in enumerate(cursor):
        raw=json.dumps([identity,json.loads(body)],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        total+=len(raw)
        if index>=5000 or total>8_000_000:return None
        if index:hasher.update(b',')
        hasher.update(raw);seen.add(identity)
    hasher.update(b']')
    return set(refs)<=seen and hasher.hexdigest()==revision


def references(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in REF_FIELDS and isinstance(child, list):
                yield from (x for x in child if isinstance(x, str))
            elif isinstance(child, (dict, list)):
                yield from references(child)
    elif isinstance(value, list):
        for child in value:
            yield from references(child)


def capture(database, case_id, run_id, sequence=1, *, data_mode='replay'):
    database = Path(database).resolve(strict=True)
    connection = sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.execute('BEGIN')
        captured = stamp()
        counts = dict(connection.execute('SELECT kind,count(*) FROM records WHERE case_id=? GROUP BY kind', (case_id,)))
        tables = {x[0] for x in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rev = connection.execute('SELECT revision FROM report_revisions WHERE case_id=?', (case_id,)).fetchone() if 'report_revisions' in tables else None
        remove = ','.join("'$." + key + "'" for key in REMOVED)
        rows = [json.loads(b) for b, in connection.execute(
            f'SELECT json_remove(body,{remove}) FROM records WHERE case_id=? AND kind IN ({",".join("?" for _ in KINDS)}) ORDER BY created_at,id',
            (case_id, *KINDS))]
        # Unreviewed dossier inventory is not a displayed claim. Do not ship
        # thousands of unrelated source objects just because they are queued.
        reference_rows = [r for r in rows if r['kind'] != 'dossier' or r.get('finding')]
        refs = sorted(set(references(reference_rows)))
        originals={}
        for start in range(0, len(refs), 400):
            part = refs[start:start+400]
            for body, in connection.execute(
                    f'SELECT body FROM records WHERE case_id=? AND kind=? AND id IN ({",".join("?" for _ in part)})',
                    (case_id, 'observation', *part)):
                row = json.loads(body)
                originals[row['id']]=dict(row)
                row['_source_version'] = digest(row)
                excerpt = row.get('fields', {}).get('excerpt')
                if isinstance(excerpt, str):
                    row['_excerpt_characters'] = len(excerpt)
                    row['_excerpt_partial'] = len(excerpt) > 12000
                    # Binding uses the retained field; clipping is display-only
                    # and happens AFTER validation in observer_view.project.
                rows.append(row)
        for h in rows:
            if h['kind']=='case_synthesis':
                h['_incident_source_current']=scenario_source_current(connection,case_id,{
                    'scenario_source_revision':h.get('source_revision'),
                    'supporting_evidence_ids':h.get('source_ids') or [],
                    'refuting_evidence_ids':[]},originals)
            if h['kind']=='hypothesis' and h.get('hypothesis_kind')=='dynamic':
                h['_scenario_source_current']=scenario_source_current(connection,case_id,h,originals)
                # Only lightweight revision descriptors enter the common view.
                # Public explanation text is read on explicit history navigation.
                from workbench.observer_history import explanation_manifest
                history=connection.execute("SELECT body FROM records WHERE id=? AND case_id=? AND kind='hypothesis'",
                    (h['id'],case_id)).fetchone()
                h['_explanation_history']=explanation_manifest(json.loads(history[0]))
        last = connection.execute('SELECT id,created_at FROM records WHERE case_id=? ORDER BY rowid DESC LIMIT 1', (case_id,)).fetchone()
        position = {'last_insert': list(last) if last else None, 'record_counts': counts,
                    'projected_records_sha256': digest(rows), 'transaction': 'SQLite read transaction / query_only',
                    'observation_scope': 'Explicit cited/test references only; not all indexed observations'}
        return validate(project(rows, case_id=case_id, run_id=run_id, data_mode=data_mode,
                                captured_at=captured, ledger_position=position,
                                sequence=sequence, source_revision=rev[0] if rev else None))
    finally:
        connection.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--case-id', required=True)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--sequence', type=int, default=1)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    view = capture(args.database, args.case_id, args.run_id, args.sequence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Captures are immutable. A new capture requires a new filename.
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump(view, stream, ensure_ascii=False, separators=(',', ':'))
    print(json.dumps({'file': str(args.output), 'snapshot': view['envelope'],
                      'objects': len(view['objects'])}, ensure_ascii=False))
