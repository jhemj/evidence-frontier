import os
import hashlib
import json
import re
from pathlib import Path
import httpx
from .worker import safe_path,segments

class ExecutionUnknown(ValueError):
    """No automatic retry or disconnect until external execution is reconciled."""


def worker_request(method,path,**kwargs):
    timeout=kwargs.pop('timeout',30)
    with httpx.Client(timeout=timeout,trust_env=False,follow_redirects=False) as client:
        r=client.request(method,os.environ['WORKER_URL']+path,headers={'X-Worker-Token':os.environ['WORKER_TOKEN']},**kwargs)
        r.raise_for_status();return r.json()


def metadata(root,path):
    if os.getenv('WORKER_URL'):return worker_request('GET','/metadata',params={'path':path})
    p=safe_path(root,path);stat=p.stat()
    if re.fullmatch(r'\.e\d{2}',p.suffix,re.I) and p.suffix.lower()!='.e01':
        raise ValueError('분할 이미지의 첫 파일 E01을 선택하세요. 나머지 세그먼트는 함께 연결됩니다.')
    if p.suffix.lower()=='.e01':
        parts=segments(p);state=[]
        for part in parts:
            if part.is_symlink() or part.resolve().parent!=p.parent:raise ValueError('세그먼트 경로가 증거 범위를 벗어났습니다.')
            s=part.stat();state.append([part.name,s.st_size,s.st_mtime_ns])
        return {'path':path,'name':p.name,'size':stat.st_size,'total_size':sum(s[1] for s in state),'segment_count':len(parts),
                'signature':'ewf-stat-v1:'+hashlib.sha256(json.dumps(state).encode()).hexdigest()}
    return {'path':path,'name':p.name,'size':stat.st_size,'signature':f'{stat.st_size}:{stat.st_mtime_ns}'}


def catalog(root):
    if os.getenv('WORKER_URL'):return worker_request('GET','/files')
    root=Path(root)
    if not root.exists():return []
    output=[]
    for p in root.rglob('*'):
        if len(output)>=1000:break
        if p.is_file() and not p.is_symlink() and p.resolve().is_relative_to(root.resolve()):
            if re.fullmatch(r'\.e\d{2}',p.suffix,re.I) and p.suffix.lower()!='.e01':continue
            item={'path':p.relative_to(root).as_posix(),'size':p.stat().st_size}
            if p.suffix.lower()=='.e01':
                try:item.update(metadata(root,item['path']))
                except (ValueError,NotImplementedError,OSError) as ex:item['error']=str(ex)
            output.append(item)
    return output
