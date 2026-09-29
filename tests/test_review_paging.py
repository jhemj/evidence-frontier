import copy

import pytest

from workbench.review_paging import ProjectionTooLarge, build_pages


def make_pack(count=100):
    observations = []
    for i in range(count):
        observations.append({
            'id': f'obs-{i}', 'type': 'linux_event', 'timestamp': f'2026-09-28T00:{i:02d}:00Z',
            'source_location': f'partition:3/inode:{i}',
            'fields': {'path': f'/var/log/event-{i}', 'inode': i,
                       'source_sha256': f'{i:064x}', 'partition_offset': 3,
                       'excerpt': 'x' * 180},
        })
    return {
        'target_os': 'linux', 'observations': observations,
        'required_dossiers': [{'id': 'd-a', 'observation_ids': ['obs-0', 'obs-50', 'obs-99']}],
        'executed_checks': [{'id': 'job-1', 'status': 'succeeded',
            'request': {'tool': 'search', 'query': 'needle'},
            'contracts': [{'dossier_id': 'd-a', 'contract_id': 'c-1'}],
            'observation_ids': ['obs-1', 'obs-50', 'not-in-pack']}],
        'allowed_observation_ids_by_dossier': {'d-a': ['obs-0', 'obs-50', 'not-in-pack']},
        'shared_check_observation_ids': ['obs-1', 'not-in-pack'],
        'literal_fact_candidates': [{'observation_id': 'obs-50', 'value': 'literal'},
                                    {'observation_id': 'not-in-pack', 'value': 'outside'}],
        'previous_assessment': {'findings': [{'observation_ids': ['obs-50']}]},
    }


def identity_rows(pack):
    return [(o['id'], o['source_location'], o['timestamp'], dict(o['fields']))
            for o in pack['observations']]


def test_pages_are_finite_lossless_views_and_do_not_mutate_canonical_input():
    pack = make_pack(100)
    before = copy.deepcopy(pack)
    pages = build_pages(pack, maximum=5000)
    assert len(pages) > 1
    assert pack == before
    assert all(page['page_version'] == 'review-page-v1' for page in pages)
    assert all(len(__import__('json').dumps(page, ensure_ascii=False, separators=(',', ':'))) <= 5000
               for page in pages)
    assert [o['id'] for page in pages for o in page['observations']] == [o['id'] for o in pack['observations']]
    assert identity_rows({'observations': [o for page in pages for o in page['observations']]}) == identity_rows(pack)
    assert all(page['finalization_allowed'] is False for page in pages)


def test_page_references_are_intersections_and_checks_are_not_presented_as_complete():
    pages = build_pages(make_pack(100), maximum=5000)
    for page in pages:
        ids = {o['id'] for o in page['observations']}
        if not page['executed_checks']:
            assert page['omitted_check_count'] >= 1
            continue
        check = page['executed_checks'][0]
        assert set(check['observation_ids']) <= ids
        assert 'not-in-pack' not in check['observation_ids']
        assert check['paginated_scope'] is True
        assert check['assessment_complete'] is False
        assert set(page['allowed_observation_ids_by_dossier']['d-a']) <= ids
        assert 'not-in-pack' not in page['shared_check_observation_ids']
        assert all(c['observation_id'] in ids for c in page['literal_fact_candidates'])
        assert page['canonical_input_sha256']


def test_fit_function_may_not_compact_source_content():
    pack = make_pack(2)

    def fit(page, maximum):
        for row in page['observations']:
            row['fields']['excerpt'] = row['fields']['excerpt'][:8]

    with pytest.raises(ProjectionTooLarge, match='source content'):
        build_pages(pack, maximum=5000, fit_fn=fit)


def test_single_irreducible_observation_is_explicit_failure():
    pack = make_pack(1)
    with pytest.raises(ProjectionTooLarge, match='single observation'):
        build_pages(pack, maximum=200)


def test_fit_function_changing_identity_is_rejected():
    pack = make_pack(2)

    def bad_fit(page, maximum):
        page['observations'][0]['id'] = 'forged'

    with pytest.raises(ProjectionTooLarge, match='source identity'):
        build_pages(pack, maximum=5000, fit_fn=bad_fit)


def test_budget_value_error_is_a_failed_trial_not_a_fatal_error():
    pack = make_pack(10)
    from workbench import review_context
    class BudgetError(ValueError):
        pass
    review_context.InputBudgetExceeded = BudgetError

    def budget_fit(page, maximum):
        if len(page['observations']) > 2:
            raise BudgetError('최종 근거 입력 예산을 초과했습니다')

    pages = build_pages(pack, maximum=5000, fit_fn=budget_fit)
    assert all(len(page['observations']) <= 2 for page in pages)


def test_large_unique_ids_use_manifest_handle_not_repeated_omitted_id_lists():
    pack = make_pack(1000)
    for row in pack['observations']:
        row['id'] = 'observation-' + ('x' * 180) + str(row['id'])
        row['fields']['timestamp_ns'] = 1727481600000000000 + int(row['id'].split('obs-')[-1])
    before = copy.deepcopy(pack)
    pages = build_pages(pack, maximum=12000)
    assert len(pages) > 1
    assert pack == before
    for page in pages:
        assert 'omitted_observation_ids' not in page
        assert page['omitted_observation_manifest']['canonical_input_sha256'] == page['canonical_input_sha256']
        assert len(page['omitted_observation_manifest']['scope']) < 200
        assert all('timestamp_ns' in row['fields'] for row in page['observations'])


def test_ten_thousand_unique_ids_with_structured_rows_use_real_fit():
    from workbench.review_context import fit
    rows = []
    for i in range(10000):
        rows.append({
            'id': f'long-observation-id-{i:05d}-' + ('z' * 48),
            'type': 'linux_event', 'timestamp': f'2026-09-28T00:{i % 60:02d}:00Z',
            'source_location': f'partition:3/inode:{i}',
            'fields': {'path': f'/var/log/syslog-{i % 4}', 'inode': i,
                       'source_sha256': f'{i:064x}', 'partition_offset': 3,
                       'os_instance': 'linux', 'artifact_path': '/evidence/syslog',
                       'timestamp_ns': 1727481600000000000 + i,
                       'excerpt': f'Sep 28 00:{i % 60:02d}:00 host daemon: repeated payload {i % 10}'},
        })
    pack = {'target_os': 'linux', 'observations': rows,
            'required_dossiers': [], 'executed_checks': [],
            'allowed_observation_ids_by_dossier': {}, 'literal_fact_candidates': []}
    # A deliberately generous bound keeps this regression focused on the
    # actual structured-context round trip; page segmentation is covered by
    # the smaller bounded cases above. The IDs remain long and unique.
    pages = build_pages(pack, maximum=8000000, fit_fn=fit)
    assert len(pages) == 1
    assert all(len(__import__('json').dumps(page, ensure_ascii=False, separators=(',', ':'))) <= 8000000
               for page in pages)
    assert sum(len(page['observations']) for page in pages) == 10000
    assert all(page['finalization_allowed'] is False for page in pages)
    assert all(page['canonical_input_sha256'] for page in pages)


def test_all_empty_checks_appear_once_in_first_page():
    pack=make_pack(100)
    pack['executed_checks']=[{'id':f'empty-{i}','status':'covered_zero',
        'request':{'tool':'search','query':f'synthetic-{i}'},'observation_ids':[],
        'contracts':[{'dossier_id':'d-a','contract_id':f'contract-{i}'}]} for i in range(3)]
    pages=build_pages(pack,maximum=5000)
    assert len(pages)>1
    assert [c['id'] for c in pages[0]['executed_checks']]==['empty-0','empty-1','empty-2']
    assert all(not p['executed_checks'] for p in pages[1:])


def test_late_pages_rebuild_literal_hints_from_their_presented_fields():
    from workbench.semantic_contract import source_facts, assertion_errors
    pack=make_pack(100)
    pack['literal_fact_candidates']=source_facts(pack['observations'])
    before=copy.deepcopy(pack)
    pages=build_pages(pack,maximum=5000)
    assert len(pages)>2
    initial_ids={f['observation_id'] for f in pack['literal_fact_candidates']}
    assert not initial_ids.intersection(o['id'] for o in pages[-1]['observations'])
    for page in pages:
        rows={o['id']:o for o in page['observations']}
        facts=page['literal_fact_candidates']
        assert facts==source_facts(page['observations']) and 0<len(facts)<=12
        assert not assertion_errors({'observation_ids':list(rows),'fact_assertions':facts},rows,list(rows))
        # Hints are not additional citation permission for either dossier.
        assert page['allowed_observation_ids_by_dossier'].get('d-a',[])==[
            i for i in pack['allowed_observation_ids_by_dossier']['d-a'] if i in rows]
    assert pack==before
