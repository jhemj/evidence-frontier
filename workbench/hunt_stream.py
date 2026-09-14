"""Bounded streaming hunting with separate source-family budgets and exact excerpts."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
from .detection import text_hits, VERSION, rule_digest

CHUNK = 1024 * 1024
MAX_FILE = 256 * CHUNK
YARA_SOURCE = r'''
rule ELF_socket_filter_and_process_disguise {
 meta: purpose = "Hunting trait combination, not a malware family attribution"
 strings:
  $a = "setsockopt" ascii
  $b = "socket" ascii
  $c = "prctl" ascii
  $d = "/bin/sh" ascii
 condition: uint32(0) == 0x464c457f and all of them
}
rule ELF_directory_hooking_traits {
 strings:
  $a = "dlsym" ascii
  $c = "readdir" ascii
  $d = "getdents" ascii
 condition: uint32(0) == 0x464c457f and $a and ($c or $d)
}
rule ELF_mining_protocol_traits {
 strings:
  $a = "stratum+tcp" ascii nocase
  $b = "cryptonight" ascii nocase
  $c = "randomx" ascii nocase
 condition: uint32(0) == 0x464c457f and $a and ($b or $c)
}
'''

def elf_details(data):
    from elftools.elf.elffile import ELFFile
    elf=ELFFile(io.BytesIO(data)); symbols=[]; needed=[]; sections=[]
    for section in elf.iter_sections():
        sections.append(section.name)
        if section.name in ('.dynsym','.symtab'):
            symbols.extend(s.name for s in section.iter_symbols() if s.name)
        if section.name=='.dynamic':
            needed.extend(t.needed for t in section.iter_tags() if t.entry.d_tag=='DT_NEEDED')
    suspicious=('socket','connect','bind','listen','accept','setsockopt','prctl','execve','system','popen','dlsym','readdir','getdents','ptrace','dlopen')
    return {'elf_type':elf.header.e_type,'machine':elf.header.e_machine,
        'capability_symbols':sorted({s for s in symbols if any(x in s for x in suspicious)})[:80],
        'needed_libraries':needed[:40],'sections':sections[:80]}


class Hunter:
    def __init__(self, run, emit, timezone=None):
        import yara
        self.rules=yara.compile(source=YARA_SOURCE)
        self.run=Path(run); self.emit=emit; self.sources=[]; self.files=[]; self.seen=set();self.timezone=timezone
        scale=max(1,min(16,int(os.getenv('HUNT_BUDGET_GIB','8'))))*1024**3
        self.remaining={'config':scale//8,'logs':scale*3//8,'binary':scale*3//8,'other':scale//8}
        self.initial=dict(self.remaining);self.count=0;self.findings=0
        self.rule_hash=hashlib.sha256((rule_digest()+YARA_SOURCE).encode()).hexdigest()

    def keep(self,item,data,offset,fields):
        digest=hashlib.sha256(data).hexdigest();relative='objects/'+digest+'.bin'
        dest=self.run/relative
        if not dest.exists():dest.write_bytes(data)
        derived=bool(fields.get('locator_basis'))
        source={**item,'relative_path':relative,'sha256':digest,'complete':not derived and offset==0 and len(data)==item['size'],
            'status':'hunt_excerpt','source_offset':offset,'extracted_bytes':len(data),'hash_scope':'retained byte range',
            'locator_basis':fields.get('locator_basis','original file bytes'),
            'reason':f"헌팅 원문 범위 [{offset}, {offset+len(data)}); 전체 파일과 구분"}
        self.sources.append(source)
        fields.update(path=item['path'],image_file_byte_offset=offset+fields.get('byte_offset',0),
            rule_version=VERSION,rule_sha256=self.rule_hash,byte_length=fields.get('byte_length',len(data)),
            source_range_start=offset)
        self.emit({'type':'linux_detection','timestamp':None,'fields':fields},source);self.findings+=1

    def parse(self,item,data,offset,line,compressed=False):
        from .linux_analysis import text_events
        # The full parsed stream goes to events.ndjson; interactive grouping is separate.
        events=list(text_events(item['path'],data,item['mtime'],self.timezone))
        if not events:return
        digest=hashlib.sha256(data).hexdigest();relative='objects/'+digest+'.bin'
        dest=self.run/relative
        if not dest.exists():dest.write_bytes(data)
        source={**item,'relative_path':relative,'sha256':digest,'complete':not compressed and offset==0 and len(data)==item['size'],
            'status':'stream_parsed','source_offset':offset,'hash_scope':'retained byte range',
            'locator_basis':'gzip decompressed bytes' if compressed else 'original file bytes',
            'reason':f"정규화 원문 범위 [{offset}, {offset+len(data)}); 전체 파일과 구분"}
        self.sources.append(source)
        for event in events:
            f=event['fields'];f['image_file_byte_offset']=offset+f.get('byte_offset',0)
            if 'line' in f:f['line']+=line-1
            if compressed:f['locator_basis']='gzip decompressed bytes'
            self.emit(event,source)

    def scan(self,node,item):
        path=item['path']; category='config' if path.startswith(('/etc/','/var/spool/')) else 'logs' if '/log/' in path or 'history' in path else 'binary' if item['mode']&0o111 or path.startswith(('/lib','/usr/lib','/bin/','/sbin/')) else 'other'
        status={k:item[k] for k in ('path','partition_offset','inode','size')};status.update(category=category,bytes_scanned=0,status='pending')
        self.files.append(status);self.count+=1
        allowance=min(MAX_FILE,self.remaining[category])
        if allowance<=0:status.update(status='deferred',reason='source family byte budget');return
        with node.open() as original:
            head=original.read(4);original.seek(0)
            if head==b'\x7fELF':
                data=original.read(min(32*CHUNK,allowance));self.remaining[category]-=len(data)
                status.update(bytes_scanned=len(data),status='scanned' if len(data)==item['size'] else 'partial')
                if len(data)!=item['size']:
                    status.update(yara_status='not_scanned',reason='ELF exceeds whole-file size budget');return
                matches=self.rules.match(data=data,timeout=10);status['yara_status']='matched' if matches else 'no_match'
                if matches:
                    try:details=elf_details(data)
                    except Exception as ex:details={'elf_parser_error':str(ex)[:200]}
                    for match in matches:
                        self.keep(item,data,0,{'rule_id':match.rule,'title':'ELF 행위 특성 조합 발견','severity':'high',
                            'stage':'YARA 및 ELF 정적 검사','yara_version':__import__('yara').__version__,
                            'matched_strings':[s.identifier for s in match.strings],**details,
                            'interpretation_limit':'정적 특성 조합. 정상 보안/관리 도구도 일치 가능; 실제 실행·악성 여부는 관련 근거로 판단.'})
                if len(data)==item['size']:status.update(sha256=hashlib.sha256(data).hexdigest(),md5=hashlib.md5(data).hexdigest())
                return
            if head[:2]!=b'\x1f\x8b' and b'\0' in original.read(4096):status.update(status='format_unparsed',reason='non-ELF binary');return
            original.seek(0);compressed=head[:2]==b'\x1f\x8b'
            # Gzip is checked before binary rejection in the caller below.
            stream=gzip.GzipFile(fileobj=original) if compressed else original
            if compressed:
                probe=stream.read(4096)
                if b'\0' in probe or probe[257:262]==b'ustar':
                    self.remaining[category]-=len(probe)
                    status.update(status='format_unparsed',compression='gzip',bytes_scanned=len(probe),
                        reason='gzip contains binary/archive data; text log parser not applicable')
                    return
                stream.seek(0)
            position=0;number=1;pending=b'';fullhash=hashlib.sha256();md5=hashlib.md5()
            while position<allowance:
                block=stream.read(min(CHUNK,allowance-position))
                if not block:break
                self.remaining[category]-=len(block);position+=len(block);fullhash.update(block);md5.update(block)
                combined=pending+block; cut=combined.rfind(b'\n')+1
                if not cut and len(combined)<2*CHUNK:pending=combined;continue
                if not cut:
                    cut=len(combined);status['long_line_split']=True
                data=combined[:cut];base=position-len(combined)
                self.parse(item,data,base,number,compressed)
                for hit in text_hits(path,data,base,number):
                    key=(item['partition_offset'],path,hit['rule_id'],hit['excerpt'].strip())
                    if key in self.seen:continue
                    self.seen.add(key)
                    local=hit['byte_offset']-base; start=max(0,local-256);end=min(len(data),local+hit['byte_length']+256)
                    hit['byte_offset']=local-start
                    if compressed:hit['locator_basis']='gzip decompressed bytes'
                    self.keep(item,data[start:end],base+start,hit)
                number+=data.count(b'\n');pending=combined[cut:]
            if pending:
                self.parse(item,pending,position-len(pending),number,compressed)
                for hit in text_hits(path,pending,position-len(pending),number):
                    key=(item['partition_offset'],path,hit['rule_id'],hit['excerpt'].strip())
                    if key in self.seen:continue
                    self.seen.add(key)
                    hit['byte_offset']-=position-len(pending)
                    if compressed:hit['locator_basis']='gzip decompressed bytes'
                    self.keep(item,pending,position-len(pending),hit)
            status.update(bytes_scanned=position,status='scanned' if not stream.read(1) else 'partial',compression='gzip' if compressed else None)
            status['text_stream_parsed']=True
            if status.get('long_line_split'):status.update(status='partial',reason='oversized line split; boundary-spanning pattern may be missed')
            if status['status']=='scanned' and not compressed:status.update(sha256=fullhash.hexdigest(),md5=md5.hexdigest())

    def manifest(self):
        return {'engine':VERSION,'rules_sha256':self.rule_hash,'files':len(self.files),'detections':self.findings,
            'bytes_by_family':{k:self.initial[k]-v for k,v in self.remaining.items()},
            'status_counts':{k:sum(f['status']==k for f in self.files) for k in {f['status'] for f in self.files}},
            'max_file_bytes':MAX_FILE,'elf_max_bytes':32*CHUNK,
            'scope':'선택한 할당 파일의 스트리밍 패턴 탐지. 읽은 범위와 형식 미지원·예산 연기를 별도 기록.'}
