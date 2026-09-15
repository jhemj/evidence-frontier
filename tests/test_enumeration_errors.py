import io,json,sys
from types import SimpleNamespace
from workbench.linux_scan import scan


def test_root_io_failure_is_preserved_and_missing_paths_are_distinct(tmp_path,monkeypatch):
    volume=io.BytesIO(b'XFSB'+b'\0'*4092);volume.offset=4096;volume.size=4096
    class FS:
        def __init__(self,*a):pass
        def get(self,path):
            if path=='/':return SimpleNamespace(scandir=lambda:[])
            if path=='/etc/passwd':return SimpleNamespace()
            if path=='/var':raise OSError('synthetic damaged directory')
            raise FileNotFoundError(path)
    target=SimpleNamespace(disks=SimpleNamespace(apply=lambda:None),volumes=[volume])
    monkeypatch.setitem(sys.modules,'dissect.target',SimpleNamespace(Target=SimpleNamespace(open=lambda *a,**k:target)))
    monkeypatch.setitem(sys.modules,'dissect.target.filesystems.xfs',SimpleNamespace(XfsFilesystem=FS))
    monkeypatch.setitem(sys.modules,'dissect.target.filesystems.extfs',SimpleNamespace(ExtFilesystem=FS))
    result=scan(tmp_path/'fixture.E01',tmp_path/'analysis')
    manifest=json.loads((tmp_path/'analysis'/result['run_id']/'manifest.json').read_text())
    errors=manifest['enumeration_errors']
    assert any(e['path']=='/var' and e['partition_offset']==4096 and e['operation']=='root_lookup' for e in errors)
    assert not any(e['path']=='/opt' for e in errors)
    assert all(r['status']=='enumeration_incomplete' for r in result['observations'][0]['fields']['coverage_map']['source_matrix'] if r['status']!='unsupported')
