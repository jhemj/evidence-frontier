import io
import json
import stat
import sys
from types import SimpleNamespace
from workbench.linux_tools import execute_tool
from workbench.models import InvestigationToolRequest


def test_multiroot_read_requires_selection_and_checks_inode(tmp_path,monkeypatch):
    class Volume(io.BytesIO):
        def __init__(self,offset,data,inode):
            super().__init__(b'XFSB'+b'\0'*4092);self.offset=offset;self.data=data;self.inode=inode
    volumes=[Volume(1048576,b'FIRST_ROOT',41),Volume(2097152,b'SECOND_ROOT',42)]
    target=SimpleNamespace(disks=SimpleNamespace(apply=lambda:None),volumes=volumes)
    class FS:
        def __init__(self,volume):self.volume=volume
        def get(self,path):
            v=self.volume
            return SimpleNamespace(open=lambda:io.BytesIO(v.data),lstat=lambda:SimpleNamespace(st_mode=stat.S_IFREG|0o644,
                st_ino=v.inode,st_size=len(v.data),st_uid=0,st_gid=0,st_mtime=0,st_ctime=0,st_atime=0))
    monkeypatch.setitem(sys.modules,'dissect.target',SimpleNamespace(Target=SimpleNamespace(open=lambda *a,**kw:target)))
    monkeypatch.setitem(sys.modules,'dissect.target.filesystems.xfs',SimpleNamespace(XfsFilesystem=FS))
    monkeypatch.setitem(sys.modules,'dissect.target.filesystems.extfs',SimpleNamespace(ExtFilesystem=FS))
    (tmp_path/'evidence.E01').write_bytes(b'inert')
    run=tmp_path/('RUN-'+'a'*32);run.mkdir();(run/'manifest.json').write_text(json.dumps({'image':'evidence.E01','sources':[]}))
    def read(**scope):
        body=InvestigationToolRequest(evidence_path='evidence.E01',run_id=run.name,
            request={'tool':'read_file','path':'/etc/cron.d/job',**scope})
        return execute_tool(tmp_path,tmp_path,body)
    assert read()['status']=='failed'
    result=read(partition_offset=2097152,inode=42)
    assert result['observations'][0]['fields']['excerpt']=='SECOND_ROOT'
    assert result['observations'][0]['fields']['partition_offset']==2097152
    assert read(partition_offset=2097152,inode=41)['status']=='failed'


def test_context_request_keeps_original_partition_and_inode():
    from workbench.investigation import compact_observation
    o={'id':'o','type':'linux_detection','timestamp':None,'source_location':'image',
        'fields':{'path':'/etc/cron.d/job','partition_offset':2097152,'inode':42,'excerpt':'source'}}
    request=compact_observation(o)['context_request']
    assert request['partition_offset']==2097152 and request['inode']==42
