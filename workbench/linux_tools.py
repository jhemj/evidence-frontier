"""Allowlisted tools used by the local investigator; never an evidence shell."""
import hashlib
import io
import json
import posixpath
import re
import stat
import tarfile
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from .worker import safe_path, command
from .linux_analysis import indicators


def execute_tool(evidence_root, analysis_root, body):
    with ExitStack() as resources:
        return _execute_tool(evidence_root, analysis_root, body, resources)


def _execute_tool(evidence_root, analysis_root, body, resources):
    if body.target_os=='windows':
        from .windows_tools import execute_tool as windows_tool
        return windows_tool(evidence_root,analysis_root,body)
    start = time.monotonic(); request = body.request
    result = {'tool': request.tool, 'query': request.query, 'path': request.path, 'observations': [], 'complete': False, 'status': 'partial'}
    try:
        image = safe_path(evidence_root, body.evidence_path)
        if not re.fullmatch(r'RUN-[a-f0-9]{32}', body.run_id): raise ValueError('잘못된 조사 ID')
        run = Path(analysis_root) / body.run_id
        manifest = json.loads((run / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('platform','linux')!='linux':raise ValueError('Linux 요청으로 Windows 자료를 조사할 수 없습니다.')
        if manifest['image'] != image.name: raise ValueError('분석 원본이 일치하지 않습니다.')
        if request.tool == 'correlate':
            from .linux_correlate import correlate
            return correlate(run, request)
        if request.tool == 'search':
            from .retrieval import search
            result = search(run, image, manifest, request)
        elif request.tool == 'read_source':
            from .retrieval import read_source
            result = read_source(run, image, manifest, request)
        else:
            from .image_target import open_target
            from dissect.target.filesystems.xfs import XfsFilesystem
            from dissect.target.filesystems.extfs import ExtFilesystem
            path = request.path
            if not path.startswith('/') or '\x00' in path or '..' in path.split('/') or len(path) > 1500:
                raise ValueError('이미지 내부 절대경로만 허용합니다.')
            target = resources.enter_context(open_target(image, apply=False)); target.disks.apply(); candidates = []
            for volume in target.volumes:
                if request.partition_offset is not None and volume.offset != request.partition_offset:continue
                volume.seek(0); header = volume.read(4096); volume.seek(0)
                if header[:4] == b'XFSB': fs = XfsFilesystem(volume)
                elif header[1080:1082] == b'\x53\xef': fs = ExtFilesystem(volume)
                else: continue
                if request.partition_offset is None:
                    try: fs.get('/etc/passwd')
                    except Exception: continue
                try:
                    node = fs.get(path); s = node.lstat()
                    if not stat.S_ISREG(s.st_mode): raise ValueError('일반 파일만 직접 읽을 수 있습니다. 링크는 실제 이미지 내부 경로를 선택하세요.')
                    if request.inode is not None and s.st_ino != request.inode:
                        if request.partition_offset is not None:raise ValueError('선택한 원문 inode가 일치하지 않습니다.')
                        continue
                    candidates.append((volume, s, node))
                except FileNotFoundError: continue
            if not candidates:
                result['failure']={'code':'path_not_resolved','stage':'object_resolution','retryable':False,
                    'resolver_version':'linux-image-resolution-1',
                    'resolution_scope':{'partition_offset':request.partition_offset,'inode':request.inode,'path':path},
                    'limitation':'Only this immutable image and resolver scope; not historical absence or deletion.'}
                raise ValueError('선택한 Linux 파일시스템에서 경로를 찾지 못했습니다. 과거 부재를 뜻하지 않습니다.')
            if len(candidates)>1:raise ValueError('여러 파티션에 같은 경로가 있습니다. partition_offset으로 원문을 지정하세요.')
            volume, s, node = candidates[0]
            with node.open() as stream:
                if request.tool == 'read_file':
                    if request.byte_offset > s.st_size:raise ValueError('파일 크기를 벗어난 읽기 위치')
                    stream.seek(request.byte_offset)
                    data=stream.read(request.byte_length)
                else:data=stream.read(16 * 1024 * 1024)
            derived = run / 'followup'; derived.mkdir(exist_ok=True)
            digest = hashlib.sha256(data).hexdigest(); destination = derived / (digest + '.bin')
            from .disk_budget import require_space
            require_space([(derived,len(data))])
            if not destination.exists(): destination.write_bytes(data)
            start_offset = request.byte_offset if request.tool == 'read_file' else 0
            complete = start_offset == 0 and len(data) == s.st_size
            fields = {'path': path, 'inode': s.st_ino, 'partition_offset': volume.offset, 'size': s.st_size,
                      'file_context': {'uid':s.st_uid,'gid':s.st_gid,'mode':s.st_mode,
                          'mtime':s.st_mtime,'ctime':s.st_ctime,'atime':s.st_atime,
                          'time_basis':'filesystem metadata; not proof of execution',
                          'capability_comparison':'not performed'},
                      'source_sha256': digest, 'artifact_path': f'{run.name}/followup/{digest}.bin', 'source_complete': complete,
                      'byte_offset': 0, 'image_file_byte_offset': start_offset, 'byte_length': len(data), 'judgment': '확정', 'stage': '원문 재확인',
                      'hash_scope': 'full file' if complete else 'extracted byte range only',
                      'interpretation_limit': '원문을 읽기 전용으로 재추출. 실제 실행·통신·악성 판정과 구분'}
            # Preserve parser precision and birth time when the image filesystem
            # exposes them; these are not the extracted host object's timestamps.
            for field,attribute in (('mtime_ns','st_mtime_ns'),('ctime_ns','st_ctime_ns'),('atime_ns','st_atime_ns'),
                                    ('birthtime_ns','st_birthtime_ns'),('birthtime','st_birthtime')):
                value=getattr(s,attribute,None)
                if value is not None:fields['file_context'][field]=value
            if request.tool == 'read_file':
                fields['excerpt'] = data.decode(errors='replace')
                fields['excerpt_truncated'] = False
                fields['next_byte_offset'] = start_offset + len(data) if start_offset + len(data) < s.st_size else None
                fields['requested_range_complete'] = len(data) == min(request.byte_length, s.st_size-start_offset)
                fields['hash_scope'] = f'image file bytes [{start_offset}, {start_offset+len(data)})'
            elif request.tool == 'static_file':
                fields['file_identification'] = command(['file', '-b', str(destination)], timeout=30)
                if data.startswith(b'\x7fELF'):
                    fields['elf_headers'] = command(['readelf', '-h', '-l', '-d', str(destination)], timeout=30)[:14000]
                strings = re.findall(rb'[\x20-\x7e]{8,500}', data)
                fields['selected_strings'] = [v.decode() for v in strings if re.search(rb'(?:stratum|xmrig|cryptonight|/dev/|LD_PRELOAD|https?://|/bin/sh|/tmp/)', v, re.I)][:60]
                fields['indicators'] = indicators('\n'.join(fields['selected_strings']))
                fields['stage'] = 'file·readelf·문자열 정적 교차검사'
            elif request.tool == 'archive_list':
                members = []; archive_limited=False
                with tarfile.open(fileobj=io.BytesIO(data), mode='r|*') as archive:
                    for member in archive:
                        members.append({'name': member.name, 'size': member.size, 'mtime': member.mtime, 'type': str(member.type)})
                        if len(members) >= 1000 or member.offset_data+member.size>64*1024*1024:
                            archive_limited=True;break
                fields.update(members=members, archive_listing_complete=not archive_limited, stage='TAR 내부 목록', interpretation_limit='목록만 읽음. 파일 실행·호스트 경로 추출 없음; 최대 1000개·다음 멤버 전 64 MiB 한도')
                complete=complete and not archive_limited
            event = {'type': 'linux_tool_result', 'timestamp': None,
                     'source_location': f'{image.name}:byte:{volume.offset}:{path}:inode:{s.st_ino}:offset:{start_offset}', 'fields': fields}
            result['observations'] = [event]
            result.update(complete=complete, status='covered' if complete else 'partial')
    except Exception as ex:
        import errno
        if isinstance(ex,(TimeoutError,BlockingIOError)) or isinstance(ex,OSError) and ex.errno in (
                errno.EAGAIN,errno.EBUSY,errno.ETIMEDOUT,errno.ESTALE,errno.ENFILE,errno.EMFILE):
            result['failure']={'code':'transient_read_error','stage':'tool_read','retryable':True,
                'resolver_version':'linux-image-resolution-1',
                'limitation':'Temporary read failure; no conclusion about source absence or content.'}
        result.update(status='failed', complete=False, error=f'{type(ex).__name__}: {ex}')
    result['elapsed_seconds'] = round(time.monotonic() - start, 3)
    return result
