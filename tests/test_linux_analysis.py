import hashlib
import json
import struct
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from workbench.linux_analysis import text_events, utmp_events, normalize_command, stamp
from workbench.investigation import evidence_pack
from workbench.controller import Controller
from workbench.store import Store

KST = timezone(timedelta(hours=9))
MTIME = datetime(2026, 9, 4, tzinfo=KST).timestamp()


def test_authentication_and_command_keep_independent_success_and_locators():
    data = b'Aug 31 11:17:07 host sshd[55]: Accepted password for analyst from 192.0.2.10 port 4000 ssh2\n'
    event = list(text_events('/var/log/secure', data, MTIME, KST))[0]
    assert event['type'] == 'linux_authentication'
    assert event['fields']['stage'] == '인증 성공'
    assert event['timestamp'] == '2026-08-31T11:17:07+09:00'
    assert '추정' in event['fields']['time_basis']
    assert event['fields']['byte_length'] == len(data)
    line = b'Aug 31 11:17:11 host CMD: host root 11:17 991 /opt/app C=  94  ./bin/service --status\n'
    event = list(text_events('/var/log/bash_his.log', line, MTIME, KST))[0]
    assert event['fields']['pid'] == '991'
    assert event['fields']['cwd'] == '/opt/app'
    assert event['fields']['referenced_paths'][0]['absolute'] == '/opt/app/bin/service'
    assert event['fields']['stage'] == '명령 기록'


def test_cron_invocation_does_not_mean_program_success():
    line = b'Sep  1 01:00:00 host CROND[321]: (root) CMD (/opt/check.sh >/dev/null 2>&1)\n'
    event = list(text_events('/var/log/cron', line, MTIME, KST))[0]
    assert event['type'] == 'linux_cron_call'
    assert '별도 확인' in event['fields']['interpretation_limit']
    assert normalize_command('./agent', None)[0]['absolute'] is None


def test_scanner_keywords_are_not_infection_observations():
    source = b'#!/bin/sh\n# scan xmrig stratum+tcp\necho "ESTABLISHED xmrig"\n'
    assert not list(text_events('/tmp/rootkit_scan.sh', source, MTIME, KST))
    report = '종합판정 : [정상] 특이사항 없음\n'.encode()
    event = list(text_events('/tmp/ldpreload_report.txt', report, MTIME, KST))[0]
    assert event['fields']['inspection_context']
    assert event['type'] == 'linux_inspection_result'
    assert '이전 점검' in event['fields']['interpretation_limit']


def test_year_rollover_and_missing_timezone_are_explicit():
    mtime = datetime(2026, 1, 2, tzinfo=KST).timestamp()
    ts, basis = stamp('Dec 31 23:59:59 host event', mtime, KST, '/var/log/secure-20260102')
    assert ts.startswith('2025-12-31') and '추정' in basis
    assert stamp('Sep 1 01:02:03 host', MTIME, None, '/var/log/secure')[0] is None
    assert stamp('type=EXECVE msg=audit(1788182227.001:50)', MTIME, None, '/audit')[1] == 'audit Unix epoch'


def test_utmp_binary_record_and_bad_layout():
    record = bytearray(384)
    struct.pack_into('<h', record, 0, 7); struct.pack_into('<i', record, 4, 123)
    record[44:48] = b'user'; record[76:85] = b'192.0.2.1'
    struct.pack_into('<i', record, 340, 1788182227)
    event = list(utmp_events('/var/log/wtmp', bytes(record)))[0]
    assert event['fields']['pid'] == 123 and event['fields']['user'] == 'user'
    assert event['fields']['byte_offset'] == 0
    with pytest.raises(ValueError): list(utmp_events('/var/log/wtmp', b'bad'))


def test_existing_disk_case_gets_content_jobs_without_rehash(tmp_path, monkeypatch):
    ev = tmp_path / 'ev'; ev.mkdir(); (ev / 'test.E01').write_bytes(b'fixture')
    c = Controller(Store(tmp_path / 'case.db'), ev)
    case = c.create('Test', '', 'standard'); e = c.register(case['id'], 'test.E01')
    c.start(case['id']); c.pause(case['id']); c.start(case['id'])
    tasks = c.store.list('task', case['id'])
    assert len([t for t in tasks if t['action'] == 'integrity']) == 1
    assert len([t for t in tasks if t['action'] == 'linux_scan']) == 1
    assert len(c.store.list('hypothesis', case['id'])) == 10
    assert all(t['attempts'] == 0 for t in tasks)


def test_evidence_pack_avoids_metadata_starvation_and_detached_evidence(tmp_path):
    c = Controller(Store(tmp_path / 'case.db'), tmp_path)
    case = c.create('Test', '', 'standard')
    e = c.store.add('evidence', case['id'], path='x', connected=True)
    for i in range(100): c.store.add('observation', case['id'], evidence_id=e['id'], type='filesystem_time', timestamp=None, source_location='x', fields={'path': '/boot/' + str(i)})
    desired = c.store.add('observation', case['id'], evidence_id=e['id'], type='linux_authentication', timestamp=None, source_location='secure:L1', fields={'user': 'test'})
    pack = evidence_pack(c, case['id'])
    assert desired['id'] in {o['id'] for o in pack['observations']}
    c.store.update(e['id'], connected=False)
    assert not evidence_pack(c, case['id'])['observations']
