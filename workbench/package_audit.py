"""Compare image files with its own package metadata; this is not a trusted baseline."""
import hashlib
import json
from pathlib import Path
from .worker import command

def audit(fs,offset,hunter):
    run=hunter.run;result={'status':'unsupported','compared':0,'mismatches':0,'uncompared':0,
        'trust':'이미지 내부 패키지 DB와 비교. 외부의 신뢰된 동일 버전 원본 검증이 아님.'}
    known={f['path']:f for f in hunter.files if f['partition_offset']==offset and f.get('sha256')}
    rows=[]
    try:
        directory=fs.get('/var/lib/dpkg/info')
        for entry in directory.scandir():
            node=entry.get()
            if not node.path.endswith('.md5sums'):continue
            with node.open() as stream:data=stream.read(8*1024*1024)
            if len(data)<node.lstat().st_size:
                result.setdefault('metadata_partial',[]).append(node.path)
            sha=hashlib.sha256(data).hexdigest();rel='objects/'+sha+'.bin';(run/rel).write_bytes(data)
            info=node.lstat();hunter.sources.append({'path':node.path,'partition_offset':offset,'inode':info.st_ino,
                'relative_path':rel,'sha256':sha,'complete':len(data)==info.st_size,'status':'package_metadata','hash_scope':'retained byte range'})
            for line in data.decode(errors='replace').splitlines():
                parts=line.split(None,1)
                if len(parts)==2 and len(parts[0])==32:rows.append((node.path,'md5','/'+parts[1].lstrip('/'),parts[0]))
        result['backend']='dpkg md5sums'
    except FileNotFoundError:pass
    if not rows:
        try:
            directory=fs.get('/var/lib/rpm');work=run/('package-db-'+str(offset));work.mkdir(exist_ok=True)
            copied=0
            for entry in directory.scandir():
                node=entry.get();name=Path(node.path).name
                if name.startswith('__db.') or not node.is_file():continue
                with node.open() as stream:data=stream.read(128*1024*1024+1)
                if len(data)>128*1024*1024 or copied+len(data)>256*1024*1024:raise ValueError('package DB copy byte budget')
                copied+=len(data);(work/name).write_bytes(data)
                sha=hashlib.sha256(data).hexdigest();rel='objects/'+sha+'.bin';(run/rel).write_bytes(data)
                info=node.lstat();hunter.sources.append({'path':node.path,'partition_offset':offset,'inode':info.st_ino,
                    'relative_path':rel,'sha256':sha,'complete':True,'status':'package_metadata','hash_scope':'full file'})
            args=['rpm','--dbpath',str(work)]
            if (work/'Packages').exists():args+=['--define','_db_backend bdb_ro']
            args+=['-qa','--qf','[%{=NAME}\t%{=FILEDIGESTALGO}\t%{FILENAMES}\t%{FILEDIGESTS}\n]']
            text=command(args,timeout=120)
            for line in text.splitlines():
                parts=line.split('\t')
                if len(parts)!=4 or not parts[2].startswith('/'):continue
                package,algo,path,expected=parts
                if expected and algo in ('1','8','(none)'):rows.append((package,'sha256' if algo=='8' else 'md5',path,expected))
            result['backend']='rpm copied database'
        except Exception as ex:
            result['error']=str(ex)[:800]
    result['metadata_rows']=len(rows)
    for package,algo,path,expected in rows:
        actual=known.get(path,{}).get(algo)
        if not actual:result['uncompared']+=1;continue
        result['compared']+=1
        if actual.lower()==expected.lower():continue
        result['mismatches']+=1
        node=fs.get(path);info=node.lstat()
        payload=json.dumps({'package':package,'algorithm':algo,'expected':expected,'actual':actual,'path':path,
            'trust':result['trust']},ensure_ascii=False).encode()
        hunter.keep({'path':path,'partition_offset':offset,'inode':info.st_ino,'size':info.st_size},payload,0,
            {'rule_id':'package_digest_mismatch','title':'패키지 DB와 파일 내용 해시 불일치','severity':'high',
             'stage':'패키지 메타데이터 대조','algorithm':algo,'expected_digest':expected,'actual_digest':actual,
             'artifact_kind':'derived comparison receipt','locator_basis':'comparison JSON; not original file bytes',
             'interpretation_limit':result['trust'],'referenced_paths':[path]})
    if result['compared']:result['status']='partial' if result['uncompared'] else 'covered'
    result['partition_offset']=offset
    (run/('package_audit-'+str(offset)+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result
