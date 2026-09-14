"""Bounded XFS metadata read in an isolated subprocess; never mounts the image."""
import json
import stat
import sys
from pathlib import Path
from importlib.metadata import version


def scan(path,limit=5000):
    from dissect.target import Target
    from dissect.target.filesystems.xfs import XfsFilesystem
    target=Target.open(str(path),apply=False)
    target.disks.apply()
    events=[];scopes=[];found=False
    for vol in target.volumes:
        vol.seek(0);header=vol.read(4096);vol.seek(0)
        scope={'offset':vol.offset,'size':vol.size,'examined_entries':0}
        scopes.append(scope)
        if header[:4]!=b'XFSB':
            scope.update(filesystem='swap' if header[-10:]==b'SWAPSPACE2' else 'unknown',status='excluded',reason='XFS 파일 목록 범위 밖')
            continue
        found=True;scope.update(filesystem='xfs',status='partial')
        try:
            fs=XfsFilesystem(vol)
            pending=[fs.get('/')];visited=set()
            while pending:
                directory=pending.pop()
                for entry in directory.scandir():
                    if len(events)>=limit:
                        scope['reason']=f'최대 {limit}개 파일 메타데이터 예산에 도달';pending.clear();break
                    node=entry.get();s=node.lstat()
                    events.append({'type':'filesystem_entry','timestamp':None,
                        'source_location':f'{Path(path).name}:byte:{vol.offset}:inode:{s.st_ino}',
                        'fields':{'path':node.path,'inode':str(s.st_ino),'mode':stat.filemode(s.st_mode),'uid':str(s.st_uid),'gid':str(s.st_gid),'size':s.st_size,
                            'atime_epoch':str(int(s.st_atime)),'mtime_epoch':str(int(s.st_mtime)),'ctime_epoch':str(int(s.st_ctime)),
                            'crtime_epoch':str(int(getattr(s,'st_birthtime',0))),
                            'atime_ns':s.st_atime_ns,'mtime_ns':s.st_mtime_ns,'ctime_ns':s.st_ctime_ns,
                            'filesystem':'xfs','partition_offset':vol.offset}})
                    scope['examined_entries']+=1
                    if stat.S_ISDIR(s.st_mode) and s.st_ino not in visited:
                        visited.add(s.st_ino);pending.append(node)
            if 'reason' not in scope:scope.update(status='listed',reason='접근 가능한 디렉터리 항목 목록; 삭제된 inode 및 미할당 영역 제외')
        except Exception as ex:
            scope.update(status='partial',reason=f'파일 시스템 읽기 오류: {type(ex).__name__}: {ex}')
    if not found:raise ValueError('XFS 파일 시스템을 찾지 못했습니다.')
    return {'observations':events,'scopes':scopes,'version':version('dissect.xfs'),
            'error':'XFS의 제한된 파일 메타데이터를 수집했습니다. 삭제 기록·미할당 영역·swap 및 예산으로 제외된 범위는 미확인입니다.'}


if __name__=='__main__':
    print(json.dumps(scan(sys.argv[1]),ensure_ascii=False))
