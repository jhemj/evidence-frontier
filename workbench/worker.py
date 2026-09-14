"""Deterministic, read-only adapters. No command string supplied by an LLM."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_EVENTS = 50000
MAX_LINE = 1024 * 1024
DISK_SUFFIXES={'.e01','.raw','.dd','.img'}


def partition_inventory(path):
    raw=command(['mmls',str(path)])
    unit=re.search(r'Units are in (\d+)-byte sectors',raw)
    if not unit:raise ValueError('TSK sector 단위를 확인할 수 없습니다.')
    sector=int(unit.group(1));partitions=[]
    for line in raw.splitlines():
        match=re.match(r'\s*\d+:\s+(\d+:\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s+(.*)',line)
        if match:
            slot,start,end,length,description=match.groups()
            partitions.append({'slot':slot,'offset_sectors':int(start),'offset':int(start)*sector,'size':int(length)*sector,'description':description})
    if not partitions:raise NotImplementedError('지원하는 할당 파티션을 찾지 못했습니다. 암호화·볼륨 구조를 확인하세요.')
    return {'raw_output':raw,'sector_size':sector,'partitions':partitions}


def filesystem_events(path):
    inventory=partition_inventory(path);observations=[]
    for partition in inventory['partitions']:
        raw=command(['fls','-r','-p','-m','/','-o',str(partition['offset_sectors']),str(path)],timeout=900)
        for number,line in enumerate(raw.splitlines(),1):
            fields=line.split('|')
            if len(fields)<11:raise ValueError('TSK bodyfile 출력을 해석할 수 없습니다.')
            if len(observations)>=MAX_EVENTS:raise ValueError('파일 목록 예산을 초과했습니다. 더 좁은 파티션 범위의 외부 workflow가 필요합니다.')
            _,name,inode,mode,owner,group,size,*times=fields
            observations.append({'type':'filesystem_entry','timestamp':None,'source_location':f'{path.name}:sector:{partition["offset_sectors"]}:inode:{inode}',
                'fields':{'path':name,'inode':inode,'mode':mode,'uid':owner,'gid':group,'size':int(size),'atime_epoch':times[0],'mtime_epoch':times[1],'ctime_epoch':times[2],'crtime_epoch':times[3]}})
    return observations


def safe_path(root, relative):
    base = Path(root).resolve()
    if Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('증거 폴더 내부 상대 경로만 허용됩니다.')
    p = (base / relative).resolve(strict=True)
    if not p.is_relative_to(base) or not p.is_file():
        raise ValueError('증거 범위를 벗어난 경로입니다.')
    return p


def sha(path,progress=None):
    h = hashlib.sha256()
    processed=0
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(4*1024*1024),b''):
            h.update(chunk)
            processed+=len(chunk)
            if progress:progress(processed)
    return h.hexdigest()


def command(args, timeout=300):
    if not shutil.which(args[0]):
        raise NotImplementedError(f'{args[0]} 도구가 설치되지 않았습니다.')
    # Spool output to disk, not unbounded process memory. Never execute a shell.
    with tempfile.TemporaryFile() as output:
        start=time.monotonic()
        p = subprocess.Popen(args,stdout=output,stderr=subprocess.STDOUT,shell=False)
        try:
            while p.poll() is None:
                if time.monotonic()-start > timeout or os.fstat(output.fileno()).st_size > 32*1024*1024:
                    p.kill(); p.wait()
                    raise TimeoutError('도구 시간 또는 출력 예산을 초과했습니다.')
                time.sleep(.1)
            output.seek(0)
            raw=output.read(32*1024*1024)
        finally:
            if p.poll() is None:
                p.kill();p.wait()
    if p.returncode:
        raise RuntimeError(f'{args[0]} 종료 코드 {p.returncode}: {raw[-2000:].decode(errors="replace")}')
    return raw.decode(errors='replace')


def segments(path):
    if path.suffix.lower() not in ('.e01','.ex01'):
        return [path]
    # Only explicitly supported numeric E01..E99 segments. Other encodings fail closed.
    if path.suffix.lower()=='.ex01':
        raise NotImplementedError('Ex01 segment 무결성 검증은 아직 지원하지 않습니다.')
    candidates = sorted(path.parent.glob(path.stem+'.*'))
    relevant = [p for p in candidates if re.fullmatch(r'\.e\d{2}',p.suffix,re.I)]
    if any(re.fullmatch(r'\.e[a-z]{2}',p.suffix,re.I) for p in candidates):
        raise NotImplementedError('EAA 이후 segment 순서는 별도 검증이 필요합니다.')
    numbers = sorted(int(p.suffix[2:]) for p in relevant)
    if numbers != list(range(1,len(numbers)+1)):
        raise ValueError('E01 segment가 중간에 누락되었습니다.')
    return sorted(relevant,key=lambda p:int(p.suffix[2:]))


def read_events(path):
    if path.suffix.lower() not in ('.ndjson','.jsonl'):
        raise NotImplementedError('이 파일은 전용 포렌식 파서가 필요합니다. NDJSON/JSONL 관측 교환 형식만 직접 읽습니다.')
    events=[]
    with path.open('r',encoding='utf-8-sig') as f:
        for number in range(1,MAX_EVENTS+2):
            line=f.readline(MAX_LINE+1)
            if not line:
                break
            if len(line)>MAX_LINE or number>MAX_EVENTS:
                raise ValueError('관측 입력 예산을 초과했습니다. 파일을 분할하여 등록하세요.')
            if not line.strip():
                continue
            item=json.loads(line)
            if not isinstance(item,dict) or not isinstance(item.get('fields'),dict) or not isinstance(item.get('source_location'),str) or not item['source_location']:
                raise ValueError(f'{number}번째 줄에 fields/source_location이 필요합니다.')
            timestamp=item.get('timestamp')
            if timestamp:
                d=datetime.fromisoformat(timestamp.replace('Z','+00:00'))
                if d.tzinfo is None:
                    raise ValueError(f'{number}번째 줄의 시간대가 없습니다.')
                timestamp=d.astimezone(timezone.utc).isoformat()
            events.append(dict(type=str(item.get('type','event'))[:100],timestamp=timestamp,source_location=item['source_location'],fields=item['fields'],line=number))
    return events


def execute(root, action, relative, progress=None):
    path=safe_path(root,relative)
    start=time.monotonic()
    result={'status':'covered','observations':[],'tool':'frontier-native','version':'0.1.0','complete':True,'truncated':False}
    try:
        if action=='integrity':
            parts=segments(path)
            manifest=[]
            total=sum(p.stat().st_size for p in parts);done=0
            def update(**fields):
                if progress:progress(**fields)
            update(stage='segment_hash',bytes_done=0,total_bytes=total,segment_count=len(parts))
            for part in parts:
                if part.is_symlink() or part.resolve().parent!=path.parent:
                    raise ValueError('segment 경로가 증거 범위를 벗어났습니다.')
                before=part.stat()
                digest=sha(part,lambda n:update(stage='segment_hash',bytes_done=done+n,total_bytes=total,segment=part.name,segment_index=len(manifest)+1,segment_count=len(parts)))
                after=part.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
                    raise ValueError('해시 계산 중 증거 파일이 변경되었습니다.')
                manifest.append({'name':part.name,'sha256':digest,'size':after.st_size})
                done+=after.st_size
            result['manifest']=manifest
            if path.suffix.lower()=='.e01':
                update(stage='ewf_verify',bytes_done=done,total_bytes=total,segment_count=len(parts))
                result['verification']=command(['ewfverify','-d','sha256',str(path)],int(os.getenv('EWF_TIMEOUT','7200')))
                result['tool']='libewf ewfverify'
                result['version']=command(['ewfverify','-V']).strip()[:200]
            result['observations']=[dict(type='integrity',source_location=relative,fields={'segments':manifest},timestamp=None)]
        elif action=='linux_scan':
            if path.suffix.lower() not in DISK_SUFFIXES:raise NotImplementedError('Linux 디스크 이미지에만 적용합니다.')
            output_root=Path(os.getenv('ANALYSIS_ROOT','/analysis'))
            output_root.mkdir(parents=True,exist_ok=True)
            from .evidence_access import metadata as evidence_metadata
            signature=evidence_metadata(root,relative)['signature']
            implementation=''.join(sha(Path(__file__).with_name(name)) for name in ('linux_scan.py','linux_analysis.py','hunt_stream.py','detection.py','package_audit.py'))
            cache_key=hashlib.sha256(json.dumps([signature,implementation,os.getenv('HUNT_BUDGET_GIB','8')]).encode()).hexdigest()
            cache_file=output_root/('cache-'+cache_key+'.json')
            if cache_file.exists():
                cached=json.loads(cache_file.read_text(encoding='utf-8'))
                cached.update(reused_result=True,cache_basis='동일 세그먼트 구성·mtime·크기 및 분석기 구현 해시',elapsed_seconds=round(time.monotonic()-start,3))
                return cached
            raw=command([sys.executable,'-m','workbench.linux_scan',str(path),str(output_root)],timeout=7200)
            run_id=json.loads(raw.strip().splitlines()[-1])['run_id']
            if not re.fullmatch(r'RUN-[a-f0-9]{32}',run_id):raise ValueError('잘못된 분석 결과 식별자')
            result=json.loads((output_root/run_id/'result.json').read_text(encoding='utf-8'))
            if evidence_metadata(root,relative)['signature']!=signature:raise ValueError('내용 조사 중 원본 구성이 변경되었습니다.')
            cache_file.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
        elif action=='inventory':
            if path.suffix.lower() in DISK_SUFFIXES:
                inventory=partition_inventory(path)
                result['tool']='TSK mmls';result['version']=command(['mmls','-V']).strip()[:200]
                result['observations']=[dict(type='partition_inventory',source_location=relative,fields=inventory,timestamp=None)]
            else:
                result['observations']=[dict(type='file_metadata',source_location=relative,fields={'name':path.name,'size':path.stat().st_size,'format':path.suffix},timestamp=None)]
        elif action in ('normalize','timeline'):
            disk=path.suffix.lower() in DISK_SUFFIXES
            fallback=None
            try:events=filesystem_events(path) if disk else read_events(path)
            except RuntimeError as ex:
                if not disk or 'Cannot determine file system type' not in str(ex):raise
                raw=command([sys.executable,'-m','workbench.dissect_files',str(path)],timeout=900)
                fallback=json.loads(raw.strip().splitlines()[-1]);events=fallback['observations']
            result['tool']='TSK fls' if disk else ('ndjson-import' if action=='normalize' else 'utc-timeline')
            if disk:result['version']=command(['fls','-V']).strip()[:200]
            if fallback:
                result.update(tool='Dissect XFS',version=fallback['version'],status='partial',complete=False,
                    error=fallback['error'],scopes=fallback['scopes'])
            if action=='timeline':
                if disk:
                    expanded=[]
                    for event in events:
                        for field in ('atime_epoch','mtime_epoch','ctime_epoch','crtime_epoch'):
                            value=int(event['fields'][field])
                            if value>0:
                                expanded.append({**event,'type':'filesystem_time','timestamp':datetime.fromtimestamp(value,timezone.utc).isoformat(),'fields':{'path':event['fields']['path'],'time_type':field,'value':value}})
                            if len(expanded)>MAX_EVENTS:raise ValueError('타임라인 예산을 초과했습니다.')
                    events=expanded
                events=sorted([e for e in events if e['timestamp']],key=lambda e:e['timestamp'])
            result['observations']=events
            if not events and not fallback: result['status']='covered_zero'
        elif action=='crosscheck':
            if path.suffix.lower() not in DISK_SUFFIXES:
                raise NotImplementedError('이 입력 형식의 독립 파서 교차 검증은 아직 지원하지 않습니다.')
            tsk=partition_inventory(path)
            raw=command([sys.executable,'-m','workbench.dissect_probe',str(path)],timeout=300)
            # Dissect may write diagnostic log lines; only the last JSON document is parsed.
            independent=json.loads(raw.strip().splitlines()[-1])
            primary={(p['offset'],p['size']) for p in tsk['partitions']}
            secondary={(p['offset'],p['size']) for p in independent['volumes']}
            if primary!=secondary:raise ValueError('TSK와 Dissect의 파티션 범위가 일치하지 않습니다. 수동 검토가 필요합니다.')
            result['tool']='TSK + Dissect';result['version']=independent['version']
            result['observations']=[{'type':'partition_crosscheck','timestamp':None,'source_location':relative,'fields':{'scope':'파티션 offset 및 size 비교만','tsk':tsk['partitions'],'dissect':independent['volumes']}}]
        else:
            raise ValueError('허용되지 않은 작업입니다.')
    except NotImplementedError as e:
        result.update(status='unsupported',complete=False,error=str(e))
    except Exception as e:
        result.update(status='failed',complete=False,error=str(e))
    result['elapsed_seconds']=round(time.monotonic()-start,3)
    return result
