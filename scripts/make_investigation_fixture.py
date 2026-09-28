"""Build independent known-ground-truth Linux E01 in a disposable directory.

Run inside the worker image. No fixture script or binary is ever executed.
"""
import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp:
    root=Path(tmp)/'root';root.mkdir()
    files={
        'etc/passwd':'root:x:0:0:root:/root:/bin/bash\nanalyst:x:1000:1000:Analyst:/home/analyst:/bin/bash\n',
        'etc/hostname':'fixture-host\n','etc/os-release':'NAME=Fixture Linux\n',
        'etc/cron.d/agent':'* * * * * root /opt/agent.sh\n',
        'etc/cron.d/dormant':'* * * * * root /opt/never-ran.sh\n',
        'opt/agent.sh':'#!/bin/sh\n# Inert forensic test content. Never executed.\necho fixture\n',
        'opt/never-ran.sh':'#!/bin/sh\necho dormant-fixture\n',
        'tmp/rootkit_scan.sh':'#!/bin/sh\n# Detection strings only: xmrig stratum+tcp rootkit\necho scanner-test\n',
        'tmp/rootkit_report.txt':'[PASS] inspection found no matching process\n',
        'var/log/secure':'2026-09-01T01:00:00+09:00 fixture sshd[501]: Accepted password for analyst from 192.0.2.10 port 4567 ssh2\n2026-09-01T01:00:01+09:00 fixture sshd[501]: session opened for user analyst\n',
        'var/log/cron':'2026-09-01T01:02:00+09:00 fixture CROND[710]: (root) CMD (/opt/agent.sh)\n',
        'var/log/bash_his.log':'2026-09-01T01:02:01+09:00 CMD: fixture root 01:02 711 /opt C= 10 ./agent.sh\n2026-09-01T01:03:00+09:00 CMD: fixture root 01:03 712 /opt C= 11 scp archive.tar 192.0.2.20:/drop/\n',
        'var/log/transfer.log':'2026-09-01T01:03:01+09:00 scp: Connection refused; transferred=0 bytes\n',
        'var/log/audit/audit.log':'type=SYSCALL msg=audit(1788192121.000:77): arch=c000003e syscall=59 success=yes exit=0 pid=711 auid=1000 ses=4 exe="/bin/sh"\ntype=EXECVE msg=audit(1788192121.000:77): argc=2 a0="/bin/sh" a1="/opt/agent.sh"\ntype=CWD msg=audit(1788192121.000:77): cwd="/opt"\n',
    }
    for name,content in files.items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content)
        if path.suffix=='.sh':path.chmod(0o755)
    zone=root/'usr/share/zoneinfo/Asia';zone.mkdir(parents=True)
    (zone/'Seoul').write_bytes(Path('/usr/share/zoneinfo/Asia/Seoul').read_bytes())
    (root/'etc/localtime').symlink_to('../usr/share/zoneinfo/Asia/Seoul')
    partition=Path(tmp)/'partition.raw'
    with partition.open('wb') as f:f.truncate(32*1024*1024)
    subprocess.run(['mkfs.ext4','-q','-F','-d',str(root),str(partition)],check=True)
    disk=Path(tmp)/'fixture.raw';mbr=bytearray(1024*1024)
    mbr[446:462]=struct.pack('<B3sB3sII',0,b'\0\0\0',0x83,b'\0\0\0',2048,partition.stat().st_size//512)
    mbr[510:512]=b'\x55\xaa'
    with disk.open('wb') as f:f.write(mbr);f.write(partition.read_bytes())
    subprocess.run(['ewfacquire','-u','-q','-c','fast','-t',str(out/'investigation-fixture'),str(disk)],check=True,capture_output=True)
    truth={'source_sha256':{name:hashlib.sha256(content.encode()).hexdigest() for name,content in files.items()},
           'invariants':['authentication accepted record','cron invocation for agent only','dormant config is not execution',
                         'audit event 77 has 3 records','scanner strings do not prove infection','scp attempt does not prove transfer success']}
    (out/'ground-truth.json').write_text(json.dumps(truth,indent=2))
print(json.dumps({'fixture':'investigation-fixture.E01','status':'created'}))
