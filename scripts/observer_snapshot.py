"""Capture a transactionally consistent, minimal replay; never open writable Store."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.observer_view import (digest, project, stamp, validate,
    purpose_observation_ids, purpose_job_current, purpose_source_current)
from workbench.observer_activity import input_context, review_recovery_manifest

KINDS = ('case', 'epoch', 'task', 'evidence', 'claim', 'dossier', 'case_synthesis', 'case_question', 'business_question', 'hypothesis_proposal', 'decision_revision',
         'test_intent', 'test_result_use', 'investigation_job', 'hypothesis',
         'model_reservation', 'review_input', 'synthesis_input', 'falsifier_input', 'request_lifecycle', 'explanation_relation',
         'receipt', 'report', 'report_finalization', 'jev_annotation')
# Large prompts/results are neither needed nor sent to the display.
REMOVED = ('pack', 'raw_output', 'rejected_output', 'result', 'output', 'model_reference_output',
           'reference_projection', 'text_projection', 'table_projection', 'selection_audit',
           'all_observation_ids', 'related_observation_ids', 'revision_history', 'assessment_history')
REF_FIELDS = {'observation_ids', 'counterevidence_ids', 'contradicting_observation_ids',
              'supporting_evidence_ids', 'refuting_evidence_ids', 'original_observation_ids',
              'required_observation_ids', 'baseline_observation_ids', 'trigger_observation_ids', 'triggering_evidence_ids'}
PURPOSE_SOURCE_MAX_BYTES = 256 * 1024


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


def purpose_sources(connection, rows, case_id, originals):
    """Capture bounded display-only sources in the caller's read transaction.

    Metadata is checked before fetching source bodies. IDs already captured by
    structured REF_FIELDS are reused, not read/appended again. Missing/foreign
    names remain unresolved in the projection; no address/path is ever opened.
    """
    tasks = {r['id']: r for r in rows if r['kind'] == 'task' and not r.get('superseded')}
    active = {r['id'] for r in rows if r['kind'] == 'evidence' and r.get('connected', True)}
    needed = {}
    for job in rows:
        if job['kind'] != 'investigation_job' or not purpose_job_current(
                job, case_id=case_id, tasks=tasks, active_evidence=active):
            continue
        identities, _ = purpose_observation_ids((job.get('request') or {}).get('reason'))
        needed.setdefault(job['evidence_id'], set()).update(i for i in identities if i not in originals)
    for evidence_id, identities in needed.items():
        identities = sorted(identities)
        for start in range(0, len(identities), 400):
            part = identities[start:start + 400]
            if not part:
                continue
            # Only minimal scope metadata is returned for potential matches.
            metadata = connection.execute(
                f"""SELECT id, json_extract(body,'$.id'), json_extract(body,'$.kind'),
                    json_extract(body,'$.case_id'), json_extract(body,'$.evidence_id'),
                    json_extract(body,'$.task_id'), json_extract(body,'$.generation'),
                    json_extract(body,'$.superseded'), length(CAST(body AS BLOB)) FROM records
                    WHERE case_id=? AND kind='observation'
                    AND json_extract(body,'$.case_id')=?
                    AND json_extract(body,'$.evidence_id')=?
                    AND id IN ({','.join('?' for _ in part)})""",
                (case_id, case_id, evidence_id, *part))
            permitted = []
            for ident, body_id, kind, source_case, evidence, task, generation, superseded, byte_count in metadata:
                scope = {'id': body_id, 'kind': kind, 'case_id': source_case,
                    'evidence_id': evidence, 'task_id': task, 'superseded': superseded}
                if generation is not None:
                    scope['generation'] = generation
                if byte_count <= PURPOSE_SOURCE_MAX_BYTES and ident == body_id and purpose_source_current(scope, case_id=case_id,
                        evidence_id=evidence_id, tasks=tasks, active_evidence=active):
                    permitted.append(ident)
            if not permitted:
                continue
            for body, in connection.execute(
                    f"SELECT body FROM records WHERE case_id=? AND kind='observation' AND id IN ({','.join('?' for _ in permitted)})",
                    (case_id, *permitted)):
                source = json.loads(body)
                if not purpose_source_current(source, case_id=case_id, evidence_id=evidence_id,
                        tasks=tasks, active_evidence=active):
                    continue
                originals[source['id']] = dict(source)
                source['_source_version'] = digest(source)
                source['_purpose_reference_only'] = True
                excerpt = source.get('fields', {}).get('excerpt')
                if isinstance(excerpt, str):
                    source['_excerpt_characters'] = len(excerpt)
                    source['_excerpt_partial'] = len(excerpt) > 12000
                rows.append(source)


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
        inputs = {r['id']: r for r in rows if r['kind'] in ('review_input', 'synthesis_input', 'falsifier_input')}
        # Retain only the digest of the canonical input. Projection removes the
        # prompt, so its display hash cannot verify an execution callback's ref.
        for identity, body in connection.execute(
                "SELECT id,body FROM records WHERE case_id=? AND kind IN ('review_input','synthesis_input','falsifier_input')", (case_id,)):
            original = json.loads(body)
            inputs[identity]['_source_version'] = digest(original)
            recovery = review_recovery_manifest(original, digest)
            if recovery:
                inputs[identity]['_review_recovery'] = recovery
        # Frozen producers retained explicit correction feedback rather than
        # request lifecycle events. Export just diagnostic identity/bindings
        # and per-target accepted receipt IDs, never rejected/model content.
        diagnostics = {r.get('diagnostic_id') for r in rows if r['kind'] == 'receipt'}
        diagnostics.update((r.get('_review_recovery') or {}).get('diagnostic_id') for r in inputs.values())
        diagnostic_fields = ('id', 'kind', 'case_id', 'task_id', 'batch_id', 'generation',
            'round', 'attempt', 'contract_version', 'prompt_version', 'input_record_id', 'input_sha256', 'failure_category')
        projection = ','.join("'" + key + "',json_extract(body,'$." + key + "')" for key in diagnostic_fields)
        names = sorted(i for i in diagnostics if isinstance(i, str) and i)
        for start in range(0, len(names), 400):
            part = names[start:start + 400]
            rows.extend(json.loads(b) for b, in connection.execute(
                f"SELECT json_object({projection}) FROM records WHERE case_id=? AND kind='review_diagnostic' AND id IN ({','.join('?' for _ in part)})",
                (case_id, *part)))
        dossiers = {r['id']: r for r in rows if r['kind'] == 'dossier'}
        for identity, accepted in connection.execute("""
            SELECT r.id, json_group_array(json_extract(h.value,'$.receipt_id'))
            FROM records r, json_each(r.body,'$.assessment_history') h
            WHERE r.case_id=? AND r.kind='dossier'
              AND json_extract(h.value,'$.finding.dossier_id')=r.id
              AND json_type(h.value,'$.receipt_id')='text' GROUP BY r.id
            """, (case_id,)):
            dossiers[identity]['_assessment_receipts'] = list(dict.fromkeys(json.loads(accepted)))
        # Extract only bounded source labels from the saved request in this same
        # transaction. Do not export whole prompts, rejected output or command bodies.
        for identity, required, observations, mode in connection.execute("""
            SELECT id, json_extract(body,'$.pack.required_dossiers'),
              (SELECT json_group_array(json_object('id',json_extract(value,'$.id'),
                'fields',json_object(
                  'path',substr(json_extract(value,'$.fields.path'),1,221),
                  'command',substr(json_extract(value,'$.fields.command'),1,221),
                  'excerpt',substr(json_extract(value,'$.fields.excerpt'),1,221),
                  '_display_partial',length(json_extract(value,'$.fields.excerpt'))>221)))
                FROM json_each(body,'$.pack.observations')),
              json_extract(body,'$.pack.review_mode')
            FROM records WHERE case_id=? AND kind IN ('review_input','synthesis_input')
            """, (case_id,)):
            inputs[identity]['_activity_context'] = input_context({
                'required_dossiers': json.loads(required or '[]'),
                'observations': json.loads(observations or '[]'), 'review_mode': mode})
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
        purpose_sources(connection, rows, case_id, originals)
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
                    'observation_scope': 'Explicit cited/test/purpose navigation references only; not all indexed observations'}
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
