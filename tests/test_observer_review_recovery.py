"""Legacy retry projection requires saved diagnostic links and full scope."""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from scripts.observer_snapshot import capture
from workbench.observer_activity import failure_details, review_recovery_manifest
from workbench.observer_view import digest, project, validate


CASE = 'CASE-recovery-fixture'


def input_digest(pack):
    return hashlib.sha256(json.dumps(pack, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def fixture():
    def row(kind, identity, **kw):
        return {'kind': kind, 'id': identity, 'case_id': CASE,
            'created_at': '2026-01-01T00:00:00Z', **kw}

    pack = {'required_dossiers': [{'id': i, 'title': 'Saved '+i,
        'observation_ids': ['O'+i]} for i in ('D1', 'D2', 'D3')],
        'observations': [{'id': 'O'+i, 'fields': {'path': '/fixture/source'}} for i in ('D1', 'D2', 'D3')],
        'review_mode': 'blind_source_review', 'final_pass': False, 'executed_checks': [], 'open_objections': [],
        'question_context': {'questions': [{'id': 'Q1', 'question': 'fixture question',
            'working_state': {'previous_tests': ['historical check']}}]},
        'secret_prompt': 'PRIVATE PROMPT NOT EXPORTED'}
    common = {'task_id': 'T', 'batch_id': 'B', 'generation': 0, 'round': 0,
        'contract_version': 'dossier-citations-7', 'prompt_version': 'forensic-provider-5'}
    first = row('review_input', 'I1', **common, attempt=1, pack=pack,
        input_sha256=input_digest(pack), included_ids=['OD1', 'OD2', 'OD3'],
        dossier_allowed_ids={i: ['O'+i] for i in ('D1', 'D2', 'D3')},
        evidence_presentation={'version': 'fixture-spans', 'spans': [], 'scope': {'fixture': 'exact'}})
    second = deepcopy(first)
    second.update(id='I2', attempt=2, created_at='2026-01-01T00:01:00Z')
    second['pack']['validation_feedback'] = {'diagnostic_id': 'DG', 'instruction': 'Correct invalid result'}
    # Other jobs may alter this inventory; targets/checks/presented scope cannot.
    second['pack']['question_context']['questions'][0]['working_state']['previous_tests'] = ['new unrelated check']
    second['input_sha256'] = input_digest(second['pack'])
    diagnostic = row('review_diagnostic', 'DG', **common, attempt=1,
        input_record_id='I1', input_sha256=first['input_sha256'],
        failure_category='output_contract',
        rejected_output={'private': 'PRIVATE REJECTED OUTPUT NOT EXPORTED'})
    failed = row('receipt', 'ERROR', **common, attempt=1, evidence_id='E',
        input_record_id='I1', input_sha256=first['input_sha256'], diagnostic_id='DG',
        receipt_type='dossier_model_error', error='invalid check target', failure_category='output_contract',
        validation_errors=[{'code': 'unknown_check_dossier', 'id': 'CASE_QUESTION-unknown'}])
    success = row('receipt', 'SUCCESS', **common, attempt=2, evidence_id='E',
        input_record_id='I2', input_sha256=second['input_sha256'], receipt_type='dossier_model',
        created_at='2026-01-01T00:02:00Z')
    dossiers = [row('dossier', i, task_id='T', evidence_id='E', generation=0,
        status='pending', title='Present title '+i, observation_ids=[],
        assessment_history=[{'receipt_id': 'SUCCESS', 'published': False,
            'finding': {'dossier_id': i, 'reason': 'PRIVATE ASSESSMENT NOT EXPORTED'}}]) for i in ('D1', 'D2', 'D3')]
    return [row('case', CASE, status='running'), row('evidence', 'E'),
        row('task', 'T', evidence_id='E', status='running', retry_generation=0),
        *dossiers, first, diagnostic, failed, second, success]


def record(rows, identity):
    return next(r for r in rows if r['id'] == identity)


def projected(rows):
    return validate(project(rows, case_id=CASE, run_id='recovery-fixture', data_mode='example',
        captured_at='2026-01-01T00:03:00Z', ledger_position={}))


def activity(view, identity='I1'):
    return next(a for a in view['activity']['items'] if a['id'] == identity)


def rehash(rows, identity='I2'):
    inp = record(rows, identity)
    inp['input_sha256'] = input_digest(inp['pack'])
    for r in rows:
        if r.get('input_record_id') == identity:
            r['input_sha256'] = inp['input_sha256']


def test_explicit_feedback_same_scoped_success_resolves_only_prior_validation_impact():
    a = activity(projected(fixture()))
    assert a['state'] == 'failed'  # retained historical error is NOT deleted
    assert a['failure_impact'] == 'resolved'
    assert a['resolved_by_input_id'] == 'I2'
    assert a['resolved_by_receipt_id'] == 'SUCCESS'
    assert a['resolved_diagnostic_id'] == 'DG'
    assert a['recovery_scope'] == 'same_scope_validated_assessments'
    assert '최종 판단 완료를 뜻하지' in a['failure']['impact']


def test_failure_template_names_actual_check_target_id_contract_mismatch():
    detail = failure_details({}, {'validation_errors': [{'code': 'unknown_check_dossier'}]})
    assert '다른 종류의 ID' in detail['reason']
    assert detail['phase'] == 'validation'


@pytest.mark.parametrize('key,value', [
    ('case_id', 'FOREIGN'), ('task_id', 'OTHER'), ('evidence_id', 'OTHER'),
    ('generation', 1), ('batch_id', 'OTHER'), ('round', 1), ('attempt', 3),
    ('input_record_id', 'OTHER'), ('input_sha256', '0'*64),
    ('contract_version', 'OTHER'), ('prompt_version', 'OTHER'),
    ('receipt_type', 'dossier_model_partial'), ('receipt_type', 'dossier_page_model'),
    ('error', 'second validation failed'), ('validation_errors', [{'code': 'unknown_check_dossier'}]),
    ('failure_category', 'output_contract')])
def test_wrong_or_partial_success_cannot_clear_failure(key, value):
    rows = fixture()
    record(rows, 'SUCCESS')[key] = value
    if key == 'case_id':
        # project rejects foreign rows before constructing any activities.
        with pytest.raises(ValueError, match='Cross-case'):
            projected(rows)
    else:
        assert activity(projected(rows))['failure_impact'] == 'unresolved'


@pytest.mark.parametrize('key,value', [
    ('task_id', 'OTHER'), ('generation', 1), ('batch_id', 'OTHER'), ('round', 1),
    ('attempt', 3), ('input_record_id', 'OTHER'), ('input_sha256', '0'*64),
    ('contract_version', 'OTHER'), ('prompt_version', 'OTHER'), ('failure_category', 'OTHER')])
def test_mismatched_diagnostic_cannot_bind_a_retry(key, value):
    rows = fixture()
    record(rows, 'DG')[key] = value
    assert activity(projected(rows))['failure_impact'] == 'unresolved'


@pytest.mark.parametrize('change', ['missing_feedback', 'different_diagnostic', 'missing_diagnostic',
    'changed_targets', 'changed_source_ids', 'changed_presentation', 'changed_check_scope',
    'changed_objections', 'changed_question', 'invalid_pack_hash', 'missing_pack',
    'missing_scope', 'page_scope', 'missing_history', 'partial_history',
    'wrong_finding_owner', 'wrong_dossier_evidence', 'wrong_dossier_generation', 'wrong_task_evidence'])
def test_missing_explicit_link_or_complete_same_scope_adoption_is_unresolved(change):
    rows = fixture()
    inp = record(rows, 'I2')
    if change == 'missing_feedback': inp['pack'].pop('validation_feedback')
    elif change == 'different_diagnostic': inp['pack']['validation_feedback']['diagnostic_id'] = 'OTHER'
    elif change == 'missing_diagnostic': rows.remove(record(rows, 'DG'))
    elif change == 'changed_targets': inp['pack']['required_dossiers'].pop()
    elif change == 'changed_source_ids': inp['dossier_allowed_ids']['D1'] = ['OTHER']
    elif change == 'changed_presentation': inp['evidence_presentation']['scope']['fixture'] = 'different span'
    elif change == 'changed_check_scope': inp['pack']['executed_checks'] = [{'id': 'OTHER'}]
    elif change == 'changed_objections': inp['pack']['open_objections'] = [{'id': 'OTHER'}]
    elif change == 'changed_question': inp['pack']['question_context']['questions'][0]['question'] = 'OTHER'
    elif change == 'invalid_pack_hash': inp['pack']['observations'][0]['fields']['path'] = 'OTHER'
    elif change == 'missing_pack': inp.pop('pack')
    elif change == 'missing_scope': inp.pop('evidence_presentation')
    elif change == 'page_scope': inp['review_stream'] = {'phase': 'source_page'}
    elif change in ('missing_history', 'partial_history'): record(rows, 'D3')['assessment_history'] = []
    elif change == 'wrong_finding_owner': record(rows, 'D3')['assessment_history'][0]['finding']['dossier_id'] = 'D1'
    elif change == 'wrong_dossier_evidence': record(rows, 'D3')['evidence_id'] = 'OTHER'
    elif change == 'wrong_dossier_generation': record(rows, 'D3')['generation'] = 1
    elif change == 'wrong_task_evidence': record(rows, 'T')['evidence_id'] = 'OTHER'
    if change not in ('invalid_pack_hash', 'missing_pack'):
        rehash(rows)
    assert activity(projected(rows))['failure_impact'] == 'unresolved'


def test_error_plus_partial_receipt_preserves_whole_input_warning():
    rows = fixture()
    partial = deepcopy(record(rows, 'ERROR'))
    partial.update(id='PARTIAL', receipt_type='dossier_model_partial', error=None,
        accepted_dossier_ids=['D1'], validation_errors=[])
    rows.append(partial)
    record(rows, 'D3')['assessment_history'] = []
    a = activity(projected(rows))
    assert a['state'] == 'failed'
    assert a['failure_impact'] == 'unresolved'


def test_same_title_unrelated_success_without_feedback_is_not_recovery():
    rows = fixture()
    inp = record(rows, 'I2')
    inp['pack'].pop('validation_feedback')
    inp['activity_target'] = record(rows, 'I1').get('activity_target')
    rehash(rows)
    assert activity(projected(rows))['failure_impact'] == 'unresolved'


def test_manifest_is_hash_and_identity_only_and_limits_targets():
    inp = record(fixture(), 'I2')
    manifest = review_recovery_manifest(inp, digest)
    assert set(manifest) == {'version', 'diagnostic_id', 'target_ids', 'scope_digest'}
    assert 'PRIVATE' not in json.dumps(manifest)
    inp['pack']['required_dossiers'] *= 12
    inp['input_sha256'] = input_digest(inp['pack'])
    assert review_recovery_manifest(inp, digest) is None


def test_capture_recovers_same_scope_and_exports_no_pack_rejected_or_history_text(tmp_path):
    database = tmp_path/'recovery.sqlite3'
    with sqlite3.connect(database) as c:
        c.execute('CREATE TABLE records(id TEXT,kind TEXT,case_id TEXT,created_at TEXT,body TEXT)')
        for r in fixture():
            c.execute('INSERT INTO records VALUES(?,?,?,?,?)',
                (r['id'], r['kind'], r['case_id'], r['created_at'], json.dumps(r)))
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    v = capture(database, CASE, 'recovery-fixture')
    assert activity(v)['failure_impact'] == 'resolved'
    assert 'PRIVATE' not in json.dumps(v)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_legacy_missing_recovery_manifest_keeps_warning():
    rows = fixture()
    for ident in ('I1', 'I2'):
        record(rows, ident).pop('dossier_allowed_ids')
    assert activity(projected(rows))['failure_impact'] == 'unresolved'
