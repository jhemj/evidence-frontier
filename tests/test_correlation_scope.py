import json
from pathlib import Path
from types import SimpleNamespace

from workbench.linux_correlate import correlate as linux_correlate
from workbench.windows_tools import correlate as windows_correlate


def req(**kw):
    defaults = dict(path='', partition_offset=None, inode=None, query='', account='',
                    time_from='', time_to='', cursor='', source_offset=None,
                    byte_offset=0, byte_length=8192, limit=None)
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def linux_run(tmp_path, events, sources):
    run = tmp_path / ('RUN-' + 'a' * 32)
    run.mkdir()
    (run / 'manifest.json').write_text(json.dumps({'sources': sources}), encoding='utf-8')
    (run / 'events.ndjson').write_text('\n'.join(json.dumps(e) for e in events), encoding='utf-8')
    return run


def test_linux_source_path_and_inode_scope_are_identity_bound(tmp_path):
    event = {'type': 'linux_persistence', 'source_location': 'cfg', 'timestamp': None,
             'fields': {'path': '/etc/arbitrary.conf', 'partition_offset': 1,
                        'inode': 10, 'referenced_paths': [{'absolute': '/opt/tool'}]}}
    sources = [{'path': '/opt/tool', 'partition_offset': 1, 'inode': 20, 'sha256': 'x'}]
    run = linux_run(tmp_path, [event], sources)
    assert len(linux_correlate(run, req(path='/etc/arbitrary.conf')).get('observations')) == 1
    assert len(linux_correlate(run, req(inode=10)).get('observations')) == 1
    assert len(linux_correlate(run, req(inode=20)).get('observations')) == 1
    assert not linux_correlate(run, req(inode=30)).get('observations')
    assert not linux_correlate(run, req(inode=20, partition_offset=2)).get('observations')
    assert not linux_correlate(run, req(path='/etc/arbitrary.conf',inode=20))['observations']
    assert not linux_correlate(run, req(path='/opt/tool',inode=10))['observations']
    assert len(linux_correlate(run, req(path='/',inode=20))['observations'])==1
    assert linux_correlate(run, req(source_offset=0))['status'] == 'unsupported'
    assert linux_correlate(run, req(byte_offset=100))['status']=='unsupported'


def test_linux_scope_before_hard_cap_and_limit(tmp_path):
    events = []
    for i in range(1001):
        events.append({'type': 'linux_persistence', 'source_location': str(i), 'timestamp': None,
                       'fields': {'path': '/etc/c', 'partition_offset': 1,
                                  'referenced_paths': [{'absolute': f'/wanted/{i}'}]}})
    sources = [{'path': f'/wanted/{i}', 'partition_offset': 1, 'inode': i, 'sha256': 'x'} for i in range(1001)]
    run=linux_run(tmp_path,events,sources)
    result = linux_correlate(run, req(path='/wanted/1000'))
    assert result['relations_found'] == 1 and len(result['observations']) == 1
    result=linux_correlate(run,req(path='/wanted',limit=2))
    assert len(result['observations'])==2 and result['omitted_records']==999 and result['truncated']


def test_windows_collision_keeps_full_context_and_os_scopes_tasks(tmp_path):
    run = tmp_path / 'run'; run.mkdir()
    rows = [
        {'type': 'windows_file', 'fields': {'path': r'C:\A\tool.exe', 'os_instance': 'one'}, 'source_location': 'a'},
        {'type': 'windows_file', 'fields': {'path': r'C:\B\tool.exe', 'os_instance': 'one'}, 'source_location': 'b'},
        {'type': 'windows_file', 'fields': {'path': r'C:\B\tool.exe', 'os_instance': 'two'}, 'source_location': 'c'},
    ]
    for osid, ver in [('one', '1'), ('two', '2')]:
        rows.append({'type': 'windows_task', 'fields': {'path': r'C:\Tasks\x', 'os_instance': osid,
                      'task_identity': 'same', 'version_sha256': ver, 'task_uri': r'\Task\X'},
                     'source_location': osid})
    (run / 'events.ndjson').write_text('\n'.join(json.dumps(r) for r in rows), encoding='utf-8')
    out = windows_correlate(run, Path('image'), {}, req(path=r'C:\A'))
    collisions = [r for r in out['observations'] if r['type'] == 'windows_counterevidence']
    assert collisions and set(collisions[0]['fields']['distinct_full_paths']) == {r'c:\a\tool.exe', r'c:\b\tool.exe'}
    assert collisions[0]['fields']['scope_matched_paths'] == [r'c:\a\tool.exe']
    all_rows=windows_correlate(run,Path('image'),{},req())['observations']
    tasks=[r['fields'] for r in all_rows if r['type']=='windows_correlation']
    assert {r['os_instance']:set(r['versions']) for r in tasks}=={'one':{'1'},'two':{'2'}}
    assert windows_correlate(run,Path('image'),{},req(partition_offset=0))['status']=='unsupported'
    assert windows_correlate(run,Path('image'),{},req(source_offset=0))['status']=='unsupported'
    limited=windows_correlate(run,Path('image'),{},req(limit=1))
    assert len(limited['observations'])==1 and limited['omitted_records']==2
