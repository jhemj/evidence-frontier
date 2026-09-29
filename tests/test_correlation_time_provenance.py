import json

from workbench.linux_correlate import correlate


def test_linked_records_preserve_time_basis_and_identity_coordinates(tmp_path):
    run = tmp_path / ('RUN-' + 'a' * 32)
    run.mkdir()
    (run / 'manifest.json').write_text(json.dumps({'sources': [
        {'path': '/opt/job', 'partition_offset': 4096, 'inode': 77,
         'sha256': 'target-sha'}]}))
    parent = {'id': 'parent', 'type': 'linux_persistence', 'source_location': 'cfg',
              'timestamp': None, 'fields': {
                  'path': '/etc/cron.d/job', 'partition_offset': 4096,
                  'referenced_paths': [{'absolute': '/opt/job'}]}}
    child = {'id': 'child', 'type': 'linux_cron_call', 'source_location': 'syslog:420',
             'timestamp': '2026-01-02T04:03:04.123456Z', 'fields': {
                 'path': '/var/log/syslog', 'partition_offset': 4096, 'inode': 88,
                 'source_sha256': 'log-sha', 'time_basis': 'year inferred from file mtime; image timezone',
                 'time_record': {'raw': 'Jan  2 04:03:04', 'timezone_basis': 'image timezone'},
                 'source_offset': 420, 'byte_offset': 12, 'byte_length': 121,
                 'line': 7, 'locator_basis': 'retained original file bytes',
                 'referenced_paths': [{'absolute': '/opt/job'}]}}
    (run / 'events.ndjson').write_text('\n'.join(json.dumps(x) for x in (parent, child)))
    result = correlate(run)
    linked = result['observations'][0]['fields']['linked_records'][0]
    assert linked['type'] == 'linux_cron_call'
    assert linked['timestamp'].endswith('123456Z')
    assert linked['time_basis'].startswith('year inferred')
    assert linked['time_record']['raw'] == 'Jan  2 04:03:04'
    assert linked['path'] == '/var/log/syslog' and linked['partition_offset'] == 4096
    assert linked['inode'] == 88 and linked['source_offset'] == 420
    assert linked['byte_offset'] == 12 and linked['byte_length'] == 121
    assert linked['source_sha256'] == 'log-sha'


def test_parent_and_child_temporal_basis_remain_distinct(tmp_path):
    run = tmp_path / ('RUN-' + 'b' * 32); run.mkdir()
    (run / 'manifest.json').write_text(json.dumps({'sources': []}))
    rows = [
        {'type': 'linux_persistence', 'source_location': 'cfg', 'timestamp': None,
         'fields': {'path': '/etc/cron.d/x', 'partition_offset': 1,
                    'time_basis': 'no precise source timestamp',
                    'referenced_paths': [{'absolute': '/missing/x'}]}},
        {'type': 'linux_cron_call', 'source_location': 'log:1',
         'timestamp': '2026-01-01T00:00:00.000000001Z',
         'fields': {'path': '/var/log/syslog', 'partition_offset': 1,
                    'source_sha256': 's', 'time_basis': 'explicit UTC nanosecond',
                    'time_record': {'raw': '2026-01-01T00:00:00.000000001Z'}}},
    ]
    (run / 'events.ndjson').write_text('\n'.join(json.dumps(x) for x in rows))
    result = correlate(run)
    assert result['observations']
    fields = result['observations'][0]['fields']
    assert fields['facts']['target_lookup'] == 'not_in_collected_manifest'
    assert fields['facts']['lookup_scope'] == 'collected_manifest_only'
