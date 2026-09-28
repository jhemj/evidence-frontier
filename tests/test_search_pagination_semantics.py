import json

from workbench.models import InvestigationTool
from workbench.retrieval import search


def make_run(tmp_path, lines):
    run = tmp_path / ('RUN-' + 'a' * 32)
    run.mkdir()
    (run / 'events.ndjson').write_text(''.join(json.dumps(line) + '\n' for line in lines), encoding='utf-8')
    (run / 'filesystem_inventory.ndjson').write_text('', encoding='utf-8')
    manifest = {'sources': [], 'platform': 'linux'}
    return run, manifest


def test_limit_plus_one_reports_unknown_remaining_matches(tmp_path):
    run, manifest = make_run(tmp_path, [{'fields': {'path': 'xx'}, 'source_location': 's'} for _ in range(4)])
    request = InvestigationTool(tool='search', query='xx', limit=2)

    result = search(run, type('Image', (), {'name': 'image.e01'})(), manifest, request)

    assert result['returned'] == 2
    assert result['omitted_matches'] == 1  # one sentinel beyond the page
    assert result['has_more'] is True
    assert result['remaining_matches_unknown'] is True
    assert 'at least' in result['omission_count_basis']


def test_fully_scanned_page_reports_exact_scope(tmp_path):
    run, manifest = make_run(tmp_path, [{'fields': {'path': 'xx'}, 'source_location': 's'}])
    request = InvestigationTool(tool='search', query='xx', limit=2)

    result = search(run, type('Image', (), {'name': 'image.e01'})(), manifest, request)

    assert result['complete'] is True
    assert result['has_more'] is False
    assert result['remaining_matches_unknown'] is False
    assert result['omitted_matches'] == 0
    assert result['omission_count_basis'].startswith('exact')
