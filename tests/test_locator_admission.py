from copy import deepcopy

import pytest

from workbench.locator_admission import trusted_source_locators
from workbench.question_engine import reserve
from workbench.store import Store
from workbench.test_admission import assess


def request(**changes):
    return {'tool': 'read_file', 'path': '/synthetic/target',
            'partition_offset': 0, 'inode': 42, 'byte_offset': 0,
            'byte_length': 8192, **changes}


def locator(**changes):
    return {'observation_id': 'o', 'receipt_id': 'r', 'evidence_id': 'e',
            'source_run': 'RUN-source', 'path': '/synthetic/target',
            'partition_offset': 0, 'inode': 43, **changes}


@pytest.mark.parametrize('tool', ['read_file', 'static_file', 'archive_list'])
def test_positive_exact_target_inode_conflict_blocks_without_rewriting(tool):
    call = request(tool=tool)
    original = deepcopy(call)
    out = assess(call, source_locators=[locator()])
    assert not out['eligible'] and out['reason'] == 'target_locator_conflict'
    assert out['source_object_binding']['target']['inode'] == 42
    assert out['source_object_binding']['source_refs'][0]['inode'] == 43
    assert call == original


def test_exact_verified_target_binding_is_allowed_even_if_other_path_has_same_inode():
    out = assess(request(), source_locators=[locator(inode=42),
        locator(observation_id='other', path='/synthetic/hard-link', inode=42)])
    assert out['eligible'] and out['source_object_binding']['status'] == 'verified'


def test_referenced_target_borrowing_source_inode_is_advisory_not_identity_or_absence():
    # The extracted source and its referenced object are separate roles. This
    # does not exclude a hard link or claim the referenced object is absent.
    out = assess(request(), source_locators=[locator(path='/synthetic/source', inode=42)])
    binding = out['source_object_binding']
    assert out['eligible'] and out['reason'] is None and binding['status'] == 'unverified'
    assert binding['notes'][0]['code'] == 'borrowed_source_locator_unverified'
    assert 'hard link' in binding['notes'][0]['limitation']
    assert binding['source_refs'][0]['path'] == '/synthetic/source'


@pytest.mark.parametrize('changes', [
    {'partition_offset': 512}, {'partition_offset': None}, {'inode': None},
    {'path': '/synthetic/new-object'}, {'path': '/synthetic/target/child'},
])
def test_unknown_new_partition_path_or_selector_is_not_rejected(changes):
    assert assess(request(**changes), source_locators=[locator()])['eligible']


def test_ambiguous_existing_bindings_cannot_select_one_inode_to_block():
    out = assess(request(), source_locators=[locator(inode=43), locator(inode=44)])
    assert out['eligible'] and out['source_object_binding']['status'] == 'ambiguous'


def test_locator_check_does_not_restrict_search_or_correlate():
    for tool in ('search', 'correlate', 'read_source'):
        assert assess(request(tool=tool), source_locators=[locator()])['eligible']


def setup(tmp_path, *, tool='read_file', result_status='partial', **changes):
    store = Store(tmp_path / 'case.db')
    cid = store.add('case', 'c', target_os='linux')['id']
    receipt = store.add('receipt', cid, evidence_id='e', receipt_type='investigation_tool',
                        result={'tool': tool, 'status': result_status})
    fields = {'path': '/synthetic/target', 'partition_offset': 0, 'inode': 43,
              'source_sha256': 'a' * 64, 'artifact_path': 'RUN-source/objects/synthetic.bin',
              'target_path': '/synthetic/other', 'referenced_paths': [{'absolute': '/synthetic/other'}]}
    fields.update(changes.pop('fields', {}))
    row = store.add('observation', cid, evidence_id=changes.pop('evidence_id', 'e'),
                    receipt_id=receipt['id'], type='linux_tool_result', fields=fields, **changes)
    return store, cid, row


def test_native_provenance_projection_uses_extraction_source_not_references(tmp_path):
    store, cid, row = setup(tmp_path)
    rows = trusted_source_locators(store, cid, 'e', 'RUN-source')
    assert len(rows) == 1 and rows[0]['observation_id'] == row['id']
    assert rows[0]['path'] == '/synthetic/target' and rows[0]['inode'] == 43


@pytest.mark.parametrize('changes', [
    {'tool': 'local-ai-dossiers-v2'}, {'result_status': 'failed'}, {'evidence_id': 'other'},
    {'fields': {'artifact_path': 'RUN-old/objects/synthetic.bin'}},
    {'fields': {'artifact_path': 'RUN-source/../objects/synthetic.bin'}},
    {'fields': {'source_sha256': None}}, {'fields': {'partition_offset': None}},
    {'fields': {'inode': None}}, {'fields': {'path': None}},
])
def test_untrusted_legacy_foreign_or_unbound_provenance_does_not_block(tmp_path, changes):
    store, cid, _ = setup(tmp_path, **changes)
    assert trusted_source_locators(store, cid, 'e', 'RUN-source') == []


def test_native_source_projection_never_crosses_evidence_or_source_run(tmp_path):
    store, cid, _ = setup(tmp_path)
    assert trusted_source_locators(store, cid, 'other', 'RUN-source') == []
    assert trusted_source_locators(store, cid, 'e', 'RUN-other') == []


def test_reserve_persists_positive_conflict_before_physical_job_admission(tmp_path):
    store, cid, _ = setup(tmp_path)
    task = {'id': 'task', 'retry_generation': 0}
    evidence = {'id': 'e', 'signature': 'immutable'}
    question = {'id': 'question', 'question_key': 'q', 'version': 1}
    intent = reserve(store, cid, task, question, request(), evidence, 'RUN-source')
    assert intent['status'] == 'blocked' and intent['blocked_reason'] == 'target_locator_conflict'
    assert intent['admission']['source_object_binding']['status'] == 'contradicted'
    assert not store.list('investigation_job', cid)


def test_reserve_keeps_borrowed_selector_and_new_scope_as_eligible(tmp_path):
    store, cid, _ = setup(tmp_path, fields={'path': '/synthetic/source', 'inode': 42})
    call = request()
    intent = reserve(store, cid, {'id': 'task', 'retry_generation': 0},
                     {'id': 'question', 'question_key': 'q', 'version': 1},
                     call, {'id': 'e', 'signature': 'immutable'}, 'RUN-source')
    assert intent['admission']['eligible']
    assert intent['scope']['request']['inode'] == 42
    assert intent['admission']['source_object_binding']['notes'][0]['code'] == 'borrowed_source_locator_unverified'


def test_binding_references_are_bounded_with_honest_omission_count():
    rows = [locator(observation_id=f'o{i}') for i in range(20)]
    out = assess(request(), source_locators=rows)['source_object_binding']
    assert out['status'] == 'contradicted'
    assert len(out['source_refs']) == 8 and out['source_refs_total'] == 20
    assert out['source_refs_omitted'] == 12
