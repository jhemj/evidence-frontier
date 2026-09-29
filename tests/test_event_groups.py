from copy import deepcopy

from workbench.event_groups import initialize, merge, source_anchor


def event(*, timestamp, artifact, sha, offset, source_range_start,
          complete=False, locator_basis='original file bytes',
          path='/var/log/cron', inode=77, nested=None):
    fields = {
        'path': path,
        'inode': inode,
        'partition_offset': 4096,
        'artifact_path': artifact,
        'source_sha256': sha,
        'source_complete': complete,
        'byte_offset': offset,
        'byte_length': 42,
        'source_range_start': source_range_start,
        'locator_basis': locator_basis,
        'line': offset // 10,
        'time_basis': 'explicit source timestamp',
        'time_record': {
            'raw': timestamp,
            'timezone_basis': 'source timezone',
            'nested': {'precision': 'nanoseconds'},
        },
        'source_fields': {
            'retained': {'artifact_path': artifact, 'byte_offset': offset},
        },
    }
    if nested is not None:
        fields['raw_payload'] = nested
    return {
        'id': f'event-{artifact}',
        'type': 'linux_cron_call',
        'timestamp': timestamp,
        'source_location': f'partition:4096:{path}:inode:{inode}:offset:{offset}',
        'fields': fields,
    }


def test_different_retained_chunks_keep_exact_endpoint_artifacts_hashes_and_offsets():
    representative = event(
        timestamp='2026-01-01T04:03:00.000000001+09:00',
        artifact='RUN/objects/first.gz', sha='hash-first', offset=100,
        source_range_start=0, complete=False,
        locator_basis='gzip decompressed bytes')
    later_chunk = event(
        timestamp='2026-01-01T04:04:00.000000002+09:00',
        artifact='RUN/objects/second.gz', sha='hash-second', offset=200,
        source_range_start=8192, complete=True,
        locator_basis='original file bytes')

    initialize(representative)
    merge(representative, later_chunk)
    fields = representative['fields']

    assert fields['first_observed_source']['artifact_path'] == 'RUN/objects/first.gz'
    assert fields['first_observed_source']['source_sha256'] == 'hash-first'
    assert fields['first_observed_source']['byte_offset'] == 100
    assert fields['first_observed_source']['source_range_start'] == 0
    assert fields['first_observed_source']['locator_basis'] == 'gzip decompressed bytes'
    assert fields['last_observed_source']['artifact_path'] == 'RUN/objects/second.gz'
    assert fields['last_observed_source']['source_sha256'] == 'hash-second'
    assert fields['last_observed_source']['byte_offset'] == 200
    assert fields['last_observed_source']['source_range_start'] == 8192
    assert fields['last_observed_source']['locator_basis'] == 'original file bytes'


def test_out_of_order_events_have_chronological_extrema_and_separate_scan_last():
    representative = event(
        timestamp='2026-01-01T10:00:00Z', artifact='rep', sha='rep-hash',
        offset=10, source_range_start=0)
    scan_late = event(
        timestamp='2026-01-01T12:00:00Z', artifact='late', sha='late-hash',
        offset=20, source_range_start=100)
    scan_early = event(
        timestamp='2026-01-01T09:00:00Z', artifact='early', sha='early-hash',
        offset=30, source_range_start=200)

    initialize(representative)
    merge(representative, scan_late)
    merge(representative, scan_early)
    fields = representative['fields']

    assert fields['first_observed'] == '2026-01-01T09:00:00Z'
    assert fields['first_observed_source']['artifact_path'] == 'early'
    assert fields['last_observed'] == '2026-01-01T12:00:00Z'
    assert fields['last_observed_source']['artifact_path'] == 'late'
    assert fields['last_record_source']['artifact_path'] == 'early'
    assert fields['last_record_source']['byte_offset'] == 30


def test_mixed_utc_offsets_and_nanoseconds_are_compared_exactly():
    representative = event(
        timestamp='2026-01-01T00:00:00.000000002+09:00', artifact='rep',
        sha='rep-hash', offset=1, source_range_start=0)
    earlier = event(
        timestamp='2025-12-31T14:59:59.999999999Z', artifact='early',
        sha='early-hash', offset=2, source_range_start=1)
    later = event(
        timestamp='2026-01-01T05:00:00.000000003+05:00', artifact='late',
        sha='late-hash', offset=3, source_range_start=2)

    initialize(representative)
    merge(representative, earlier)
    merge(representative, later)
    fields = representative['fields']

    assert fields['first_observed'] == earlier['timestamp']
    assert fields['first_observed_source']['artifact_path'] == 'early'
    assert fields['last_observed'] == later['timestamp']
    assert fields['last_observed_source']['artifact_path'] == 'late'


def test_missing_timestamps_do_not_create_false_extrema():
    representative = event(
        timestamp=None, artifact='rep', sha='rep-hash', offset=1,
        source_range_start=0)
    dated = event(
        timestamp='2026-01-01T00:00:00Z', artifact='dated', sha='dated-hash',
        offset=2, source_range_start=1)
    undated = event(
        timestamp=None, artifact='undated', sha='undated-hash', offset=3,
        source_range_start=2)

    initialize(representative)
    merge(representative, dated)
    merge(representative, undated)
    fields = representative['fields']

    assert fields['first_observed'] == dated['timestamp']
    assert fields['last_observed'] == dated['timestamp']
    assert fields['first_observed_source']['artifact_path'] == 'dated'
    assert fields['last_observed_source']['artifact_path'] == 'dated'
    assert fields['last_record_source']['artifact_path'] == 'undated'
    assert fields['last_record_source']['timestamp'] is None


def test_raw_fields_and_incoming_event_are_not_mutated_and_nested_anchor_is_lossless():
    raw_payload = {'nested': {'values': ['α', 'line\n2'], 'missing': None}}
    representative = event(
        timestamp='2026-01-01T00:00:00Z', artifact='rep', sha='rep-hash',
        offset=1, source_range_start=0, nested=raw_payload)
    incoming = event(
        timestamp='2026-01-01T00:01:00Z', artifact='incoming', sha='in-hash',
        offset=2, source_range_start=1, nested={'incoming': {'x': 1}})
    representative_fields_before = deepcopy(representative['fields'])
    incoming_before = deepcopy(incoming)

    initialize(representative)
    merge(representative, incoming)

    for key, value in representative_fields_before.items():
        assert representative['fields'][key] == value
    assert incoming == incoming_before
    assert representative['fields']['raw_payload'] == raw_payload
    assert representative['fields']['first_observed_source']['time_record'] == representative_fields_before['time_record']
    assert representative['fields']['source_fields'] == representative_fields_before['source_fields']

    # Anchors are deep copies: changing the incoming nested source metadata
    # after merge cannot rewrite the retained endpoint.
    incoming['fields']['time_record']['nested']['precision'] = 'changed'
    incoming['fields']['source_fields']['retained']['byte_offset'] = 999
    assert representative['fields']['last_observed_source']['time_record']['nested']['precision'] == 'nanoseconds'
    assert representative['fields']['source_fields'] == representative_fields_before['source_fields']


def test_source_anchor_preserves_completeness_gzip_locator_and_nested_time_record():
    observed = event(
        timestamp='2026-01-01T00:00:00Z', artifact='chunk.gz', sha='gzip-hash',
        offset=4096, source_range_start=4096, complete=False,
        locator_basis='gzip decompressed bytes')

    anchor = source_anchor(observed)

    assert anchor['artifact_path'] == 'chunk.gz'
    assert anchor['source_sha256'] == 'gzip-hash'
    assert anchor['source_complete'] is False
    assert anchor['source_range_start'] == 4096
    assert anchor['byte_offset'] == 4096
    assert anchor['locator_basis'] == 'gzip decompressed bytes'
    assert anchor['time_record'] == observed['fields']['time_record']
    assert anchor['time_record'] is not observed['fields']['time_record']


def test_context_projection_keeps_full_endpoint_locators():
    from workbench.review_context import source_fields
    representative=event(timestamp='2026-01-01T00:00:00Z',artifact='RUN/objects/'+('a'*64)+'.bin',
        sha='a'*64,offset=10,source_range_start=0)
    later=event(timestamp='2026-01-01T00:01:00Z',artifact='RUN/objects/'+('b'*64)+'.bin',
        sha='b'*64,offset=20,source_range_start=1024)
    initialize(representative);merge(representative,later)
    projected=source_fields(representative['fields'],length=8,items=1)
    for key in ('first_observed_source','last_observed_source','last_record_source','occurrences_scope'):
        assert projected[key]==representative['fields'][key]


def test_stream_parser_endpoint_resolves_its_own_exact_bytes(tmp_path):
    from workbench.hunt_stream import Hunter
    from workbench.event_groups import grouping_key
    from datetime import datetime,timezone
    from zoneinfo import ZoneInfo
    from pathlib import Path
    run=tmp_path/'RUN-test';run.mkdir();(run/'objects').mkdir()
    chunks=[b'Jun 01 00:00:00 host CROND[1]: (root) CMD (/opt/example/task)\n',
            b'# context\nJun 02 00:00:00 host CROND[2]: (root) CMD (/opt/example/task)\n']
    item={'path':'/var/log/cron','inode':123,'partition_offset':0,'size':sum(map(len,chunks)),
          'mtime':datetime(2026,6,3,tzinfo=timezone.utc).timestamp()}
    groups={}
    def emit(observed,source):
        observed['fields'].update(artifact_path=f"{run.name}/{source['relative_path']}",
            source_sha256=source['sha256'],source_complete=source['complete'],
            partition_offset=source['partition_offset'],inode=source['inode'])
        key=grouping_key(observed)
        if key in groups:merge(groups[key],observed)
        else:initialize(observed);groups[key]=observed
    hunter=Hunter.__new__(Hunter);hunter.run=run;hunter.sources=[];hunter.emit=emit;hunter.timezone=ZoneInfo('UTC')
    hunter.parse(item,chunks[0],0,1)
    hunter.parse(item,chunks[1],len(chunks[0]),2)
    assert len(groups)==1
    fields=next(iter(groups.values()))['fields']
    assert fields['occurrences']==2 and 'last_byte_offset' not in fields
    end=fields['last_observed_source']
    assert end['artifact_path']!=fields['artifact_path']
    assert end['source_range_start']==len(chunks[0])
    assert end['byte_offset']==len(b'# context\n')
    raw=(tmp_path/Path(end['artifact_path'])).read_bytes()
    assert raw[end['byte_offset']:end['byte_offset']+end['byte_length']]==chunks[1].split(b'\n',1)[1]
