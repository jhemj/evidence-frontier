"""Create and examine a synthetic MBR image entirely within disposable /tmp storage."""
import json
import struct
import subprocess
import tempfile
from pathlib import Path
from workbench.worker import execute

with tempfile.TemporaryDirectory() as temp:
    path=Path(temp)/'synthetic.raw'
    raw=bytearray(4*1024*1024)
    raw[446:462]=struct.pack('<B3sB3sII',0,b'\0\0\0',0x83,b'\0\0\0',2048,4096)
    raw[510:512]=b'\x55\xaa'
    path.write_bytes(raw)
    subprocess.run(['ewfacquire','-u','-q','-c','fast','-t',str(Path(temp)/'synthetic'),str(path)],check=True,capture_output=True,timeout=60)
    results={name:execute(temp,name,'synthetic.E01') for name in ('integrity','inventory','crosscheck')}
    for name,result in results.items():assert result['status']=='covered',(name,result)
    partitions=results['inventory']['observations'][0]['fields']['partitions']
    assert partitions[0]['offset']==1048576 and partitions[0]['size']==2097152
    print(json.dumps({'synthetic_e01':'passed','ewfverify':'passed','tsk_partition_inventory':'passed','dissect_partition_crosscheck':'passed'}))
