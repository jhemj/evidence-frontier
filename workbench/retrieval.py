"""Bounded, resumable inspection of immutable retained sources."""
import base64
import codecs
import hashlib
import json
import re
from datetime import datetime, timezone

SCAN_BUDGET = 64 * 1024 * 1024
LINE_LIMIT = 1024 * 1024


def literal_byte_hit(raw, query, minimum_end=0):
    """Locate a casefolded UTF-8 literal in ORIGINAL bytes, including ß -> ss."""
    prefix=0
    while prefix<min(3,len(raw)) and 0x80<=raw[prefix]<=0xbf:prefix+=1
    try:text=codecs.getincrementaldecoder('utf-8')().decode(raw[prefix:],final=False)
    except UnicodeDecodeError:
        # Byte-exact ASCII lookup remains safe in otherwise undecodable logs.
        if not query.isascii():return -1
        start=0
        while True:
            found=raw.lower().find(query.encode(),start)
            if found<0 or found+len(query)>minimum_end:return found
            start=found+1
    folded=text.casefold();start=0
    while True:
        found=folded.find(query,start)
        if found<0:return -1
        folded_pos=0;byte_pos=prefix;hit=None;end=0
        for char in text:
            following=folded_pos+len(char.casefold())
            if hit is None and following>found:hit=byte_pos
            byte_pos+=len(char.encode('utf-8'));folded_pos=following
            if folded_pos>=found+len(query):end=byte_pos;break
        if end>minimum_end:return hit
        start=found+1


def tool_scope(call):
    from .models import InvestigationTool
    return InvestigationTool(**{k:v for k,v in call.items() if k in InvestigationTool.model_fields}).model_dump()


def fingerprint_scope(call):
    return {k:v for k,v in tool_scope(call).items() if k != 'reason'}


def time_value(value):
    if not value:return None
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:raise ValueError('시간 필터에는 시간대가 필요합니다.')
    return parsed.astimezone(timezone.utc)


def source_origin(observation):
    f = observation.get('fields', {})
    # Conservatively treat repeated extraction and parsed/raw copies of one file
    # as ONE origin. This does not assert independence of different files.
    identity = [observation.get('evidence_id'), f.get('partition_offset'), f.get('path'), f.get('inode')]
    if not f.get('path'):identity += [observation.get('source_location')]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()[:24]


def read_source(run, image, manifest, request):
    relative=request.path.removeprefix(run.name+'/')
    source=next((s for s in manifest['sources'] if s.get('relative_path')==relative),None)
    if source is None:raise ValueError('보존 원장에 등록된 원문만 조회할 수 있습니다.')
    path=(run/relative).resolve()
    if not path.is_relative_to(run.resolve()):raise ValueError('보존 원문 경로 범위 오류')
    with path.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=source['sha256']:raise ValueError('보존 원문 해시 불일치')
        stream.seek(0,2);size=stream.tell()
        if request.byte_offset>size:raise ValueError('보존 원문 크기를 벗어난 읽기 위치')
        stream.seek(request.byte_offset);data=stream.read(request.byte_length)
    end=request.byte_offset+len(data)
    fields={k:source.get(k) for k in ('path','partition_offset','inode','locator_basis')}
    fields.update(artifact_path=f'{run.name}/{relative}',source_sha256=source['sha256'],
        source_complete=source['complete'],byte_offset=request.byte_offset,byte_length=len(data),
        image_file_byte_offset=source.get('source_offset',0)+request.byte_offset,
        excerpt=data.decode(errors='replace'),next_byte_offset=end if end<size else None,
        hash_scope='full retained artifact, not necessarily full original file',
        stage='보존 원문 지정 구간',interpretation_limit='좌표 기준을 유지한 읽기. 압축 해제 좌표는 압축 파일 바이트 위치가 아님')
    return {'tool':'read_source','observations':[{'type':'linux_tool_result','timestamp':None,
        'source_location':f'{image.name}:{source["path"]}:retained:{relative}:offset:{request.byte_offset}','fields':fields}],
        'complete':request.byte_offset==0 and end==size and source['complete'],
        'status':'covered' if request.byte_offset==0 and end==size and source['complete'] else 'partial'}


def search(run, image, manifest, request):
    query = request.query.casefold().strip()
    if len(query) < 2:raise ValueError('검색어는 2글자 이상이어야 합니다.')
    lower, upper = time_value(request.time_from), time_value(request.time_to)
    if lower and upper and lower > upper:raise ValueError('검색 시간 범위가 역순입니다.')
    files = [(run/'events.ndjson', None), (run/'filesystem_inventory.ndjson', None)]
    for source in manifest['sources']:
        if not source.get('relative_path'):continue
        path = (run/source['relative_path']).resolve()
        if not path.is_relative_to(run.resolve()):raise ValueError('원문 경로 범위 오류')
        files.append((path, source))
    binding = hashlib.sha256(json.dumps(['retained-search-3',run.name, manifest, fingerprint_scope({**request.model_dump(), 'cursor':''}),
        [(p.name,p.stat().st_size,p.stat().st_mtime_ns) for p,_ in files]], sort_keys=True).encode()).hexdigest()
    fi=offset=line_number=watermark=0
    if request.cursor:
        try:
            c=json.loads(base64.urlsafe_b64decode(request.cursor.encode()))
            if c['binding'] != binding:raise ValueError('검색 범위 또는 원문이 변경되었습니다.')
            fi,offset,line_number=c['file'],c['offset'],c['line']
            watermark=c.get('watermark',0)
            if any(type(v) is not int or v < 0 for v in (fi,offset,line_number)) or fi >= len(files) or offset > files[fi][0].stat().st_size:raise ValueError('잘못된 검색 위치')
            if type(watermark) is not int or watermark<0 or watermark>files[fi][0].stat().st_size:raise ValueError('잘못된 검색 범위')
        except (KeyError,TypeError,ValueError) as ex:raise ValueError('유효하지 않은 검색 이어보기: '+str(ex)) from ex
    observations=[]; examined=0; unknown_time=0; matched=0; clipped=0; raw_files=0
    def continuation():
        return base64.urlsafe_b64encode(json.dumps({'binding':binding,'file':fi,'offset':offset,'line':line_number,'watermark':watermark}).encode()).decode()
    next_cursor=''
    while fi < len(files):
        path,source=files[fi]
        if source:
            # Verify before admitting ANY excerpts from this source.
            with path.open('rb') as stream:
                digest=hashlib.file_digest(stream,'sha256').hexdigest()
            if digest != source['sha256']:raise ValueError('검색 원문 해시 불일치')
            raw_files+=1
        with path.open('rb') as stream:
            stream.seek(offset)
            while True:
                before=stream.tell();raw=stream.readline(LINE_LIMIT)
                if not raw:break
                if examined and examined+len(raw)>SCAN_BUDGET:
                    next_cursor=continuation();break
                examined+=len(raw)
                text=raw.decode(errors='replace')
                fragment=not raw.endswith(b'\n') and len(raw)==LINE_LIMIT
                if fragment:clipped+=1
                item=None
                hit=literal_byte_hit(raw,query,max(0,watermark-before)) if source else -1
                if hit>=0 if source else query in text.casefold():
                    if source:
                        window_start=max(0,hit-1500)
                        window=raw[window_start:window_start+6000]
                        item={'type':'linux_literal_match','timestamp':None,
                            'source_location':f"{image.name}:byte:{source['partition_offset']}:{source['path']}:inode:{source['inode']}:offset:{source.get('source_offset',0)+before+window_start}",
                            'fields':{**{k:source.get(k) for k in ('path','partition_offset','inode')},
                                'line':line_number+1,'byte_offset':before+window_start,'image_file_byte_offset':source.get('source_offset',0)+before+window_start,
                                'locator_basis':source.get('locator_basis','retained original file bytes'),
                                'byte_length':len(window),'excerpt':window.decode(errors='replace'), 'excerpt_truncated':len(raw)>len(window),
                                'artifact_path':f"{run.name}/{source['relative_path']}",'source_sha256':source['sha256'],
                                'source_complete':source['complete'],'search_query':request.query,
                                'stage':'보존 원문 문자열 일치','interpretation_limit':'문자열 존재와 실행·성공·악성 의도는 별도'}}
                    else:
                        if len(raw)==LINE_LIMIT and not raw.endswith(b'\n'):raise ValueError('색인 행 한도 초과: 파서 입력을 분할해야 합니다.')
                        item=json.loads(text)
                        if 'fields' not in item:item={'type':'linux_path_match','timestamp':None,'source_location':f"{image.name}:byte:{item['partition_offset']}:{item['path']}:inode:{item['inode']}",'fields':item}
                    f=item['fields'];p=f.get('path','')
                    if request.path and not (p==request.path or p.startswith(request.path.rstrip('/')+'/')):item=None
                    if item and request.account and not re.search(r'(?<![\w.-])'+re.escape(request.account)+r'(?![\w.-])',text):item=None
                    if item and (lower or upper):
                        try:t=time_value(item.get('timestamp'))
                        except (ValueError,TypeError):t=None
                        if t is None:unknown_time+=1;item=None
                        elif (lower and t<lower) or (upper and t>upper):item=None
                if item:
                    matched+=1
                    if len(observations)>=request.limit:
                        next_cursor=continuation();break
                    item['fields']['source_origin']=source_origin(item)
                    observations.append(item)
                watermark=max(watermark,before+len(raw))
                if fragment and source:
                    # A UTF-8 character uses at most four bytes. Overlap retains
                    # all possible boundary-spanning literal matches.
                    stream.seek(max(before+1,stream.tell()-max(4,len(query.encode('utf-8'))*4)))
                else:line_number+=1
                offset=stream.tell()
            if next_cursor:break
        fi+=1;offset=line_number=watermark=0
    complete=not next_cursor and not clipped
    return {'tool':'search','query':request.query,'path':request.path,'observations':observations,
        'matches':matched,'matches_scope':'this page scan only; not total matches', 'returned':len(observations),
        'omitted_matches':max(0,matched-len(observations)), 'next_cursor':next_cursor,
        'complete':complete,'truncated':not complete,'status':'partial' if not complete else 'covered' if observations else 'covered_zero',
        'raw_files':raw_files,'raw_bytes':examined,'unknown_time_excluded':unknown_time,'long_line_fragments':clipped,
        'query_semantics':'case-insensitive literal substring; spaces are literal, no AND/OR/regex',
        'scope':'This page of retained sources only; cursor continues exact same filters. Time filters exclude undated raw lines. Account is a literal token, not verified identity.',
        'negative_search':None if observations else '이 검색 페이지에서 일치 없음. 이미지 전체 또는 이전 페이지의 부재 아님'}
