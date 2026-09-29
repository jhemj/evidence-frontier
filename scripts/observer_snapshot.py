"""Capture a transactionally consistent, minimal replay; never open writable Store."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.observer_view import digest, project, stamp, validate

KINDS = ('case', 'task', 'evidence', 'claim', 'dossier', 'case_question', 'decision_revision',
         'test_intent', 'test_result_use', 'investigation_job', 'hypothesis',
         'model_reservation', 'review_input', 'synthesis_input', 'receipt', 'report')
# Large prompts/results are neither needed nor sent to the display.
REMOVED = ('pack', 'raw_output', 'rejected_output', 'result', 'output', 'model_reference_output',
           'reference_projection', 'text_projection', 'table_projection', 'selection_audit',
           'all_observation_ids', 'related_observation_ids', 'revision_history')
REF_FIELDS = {'observation_ids', 'counterevidence_ids', 'contradicting_observation_ids',
              'supporting_evidence_ids', 'refuting_evidence_ids', 'original_observation_ids',
              'required_observation_ids', 'baseline_observation_ids'}


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
        for start in range(0, len(refs), 400):
            part = refs[start:start+400]
            for body, in connection.execute(
                    f'SELECT body FROM records WHERE case_id=? AND kind=? AND id IN ({",".join("?" for _ in part)})',
                    (case_id, 'observation', *part)):
                row = json.loads(body)
                row['_source_version'] = digest(row)
                excerpt = row.get('fields', {}).get('excerpt')
                if isinstance(excerpt, str):
                    row['_excerpt_characters'] = len(excerpt)
                    row['_excerpt_partial'] = len(excerpt) > 12000
                    row['fields']['excerpt'] = excerpt[:12000]
                rows.append(row)
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
