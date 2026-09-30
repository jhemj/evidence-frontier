"""Positive source-object bindings for controller-owned tool admission.

A referenced path is not the object from which a record was extracted. This
module never opens evidence or resolves paths: it reuses native receipt-backed
source locators in the current immutable run. Missing bindings remain unknown.
In particular, different paths can be hard links to the same inode.
"""
import re

VERSION = 'source-object-binding-1'
_OBJECT_TOOLS = {'read_file', 'static_file', 'archive_list'}
_NATIVE_TOOLS = {
    'linux-investigation-1', 'deterministic-linux-correlation-1',
    'read_source', 'read_file', 'static_file', 'archive_list',
    'search',
}
_GOOD_RESULTS = {'partial', 'covered', 'covered_zero'}


def trusted_source_locators(store, cid, evidence_id, source_run):
    """Project narrow source identities; never infer target identity from prose.

    Receipts and retained artifact provenance must both bind the same evidence
    and run. Legacy/inventory-only rows without this binding are not a reason
    to reject a new object. Hashes bind the retained source, not full-file
    content equivalence or independent evidence.
    """
    if not isinstance(source_run, str) or not source_run:
        return []
    receipts = {r['id']: r for r in store.list('receipt', cid)
                if r.get('case_id') == cid and r.get('evidence_id') == evidence_id}
    output = []
    for row in store.list('observation', cid):
        if row.get('case_id') != cid or row.get('evidence_id') != evidence_id:
            continue
        receipt = receipts.get(row.get('receipt_id'))
        result = (receipt or {}).get('result', {})
        if (result.get('tool') not in _NATIVE_TOOLS
                or result.get('status') not in _GOOD_RESULTS):
            continue
        fields = row.get('fields', {})
        artifact = fields.get('artifact_path')
        digest = fields.get('source_sha256')
        path = fields.get('path')
        partition, inode = fields.get('partition_offset'), fields.get('inode')
        if (not isinstance(artifact, str) or not artifact.startswith(source_run + '/')
                or any(part in ('', '.', '..') for part in artifact.split('/'))
                or not isinstance(digest, str) or not re.fullmatch(r'[a-fA-F0-9]{64}', digest)
                or not isinstance(path, str) or not path.startswith('/') or '\x00' in path
                or '..' in path.split('/')
                or type(partition) is not int or partition < 0
                or type(inode) is not int or inode < 0):
            continue
        # Neither target_path, referenced_paths, linked_records nor model
        # interpretations are candidates for the extraction-source identity.
        output.append({'observation_id': row['id'], 'receipt_id': receipt['id'],
                       'evidence_id': evidence_id, 'source_run': source_run,
                       'path': path, 'partition_offset': partition, 'inode': inode,
                       'source_sha256': digest, 'artifact_path': artifact})
    return output


def check(call, locators=()):
    """Block positive contradictions, not absence or unverified target scope."""
    target = {key: call.get(key) for key in ('path', 'partition_offset', 'inode')}
    result = {'version': VERSION, 'target': target, 'status': 'not_applicable',
              'reason': None, 'source_refs': [], 'source_refs_total': 0,
              'source_refs_omitted': 0, 'notes': []}
    if call.get('tool') not in _OBJECT_TOOLS:
        return result
    partition, inode = target['partition_offset'], target['inode']
    if type(partition) is not int or type(inode) is not int:
        result['status'] = 'unverified'
        return result
    scoped = [row for row in locators if row.get('partition_offset') == partition]
    exact = [row for row in scoped if row.get('path') == target['path']]
    known_inodes = {row['inode'] for row in exact}

    def set_refs(rows):
        result['source_refs_total'] = len(rows)
        result['source_refs_omitted'] = max(0, len(rows) - 8)
        result['source_refs'] = [{'observation_id': row['observation_id'],
                 'receipt_id': row['receipt_id'], 'evidence_id': row['evidence_id'],
                 'source_run': row['source_run'], 'path': row['path'],
                 'partition_offset': row['partition_offset'], 'inode': row['inode']}
                for row in rows[:8]]

    if len(known_inodes) == 1:
        set_refs(exact)
        if inode not in known_inodes:
            result.update(status='contradicted', reason='target_locator_conflict')
        else:
            result['status'] = 'verified'
        return result
    if len(known_inodes) > 1:
        # An internally ambiguous source binding cannot establish which inode
        # is right. Do not use it to reject a caller's object selector.
        result['status'] = 'ambiguous'
        set_refs(exact)
        result['notes'] = [{'code': 'target_locator_ambiguous',
                            'limitation': 'Current source bindings disagree; target identity remains unresolved.'}]
        return result
    borrowed = [row for row in scoped if row.get('inode') == inode
                and row.get('path') != target['path']]
    result['status'] = 'unverified'
    if borrowed:
        set_refs(borrowed)
        result['notes'] = [{'code': 'borrowed_source_locator_unverified',
                            'limitation': 'This inode is verified for another source path, not this target. '
                                          'A reference is not a target binding; a hard link or new object is not excluded.'}]
    return result
