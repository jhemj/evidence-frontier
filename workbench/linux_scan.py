"""Linux content collection from forensic filesystems into a separate volume.

The source image is never mounted or executed. Output filenames are content
hashes, not untrusted server paths. Every extraction and exclusion is inventoried.
"""
import gzip
import hashlib
import json
import os
import posixpath
import re
import stat
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .linux_analysis import VERSION, CHECK_TOOL, indicators, text_events, utmp_events

MAX_ENTRIES = 1000000
MAX_FILE = 16 * 1024 * 1024
MAX_BYTES = 512 * 1024 * 1024
MAX_GROUPS = 16000
ROOTS = ['/etc', '/var', '/root', '/home', '/tmp', '/usr', '/opt', '/srv', '/lib', '/lib64', '/bin', '/sbin', '/boot']
EXCLUDE = {'/proc', '/sys', '/dev', '/run', '/lost+found'}


def candidate(path, mode):
    name = posixpath.basename(path)
    # Private keys/password databases are unnecessary for these record parsers.
    if name in ('shadow', 'gshadow', 'id_rsa', 'id_dsa', 'id_ecdsa', 'id_ed25519') or name.endswith(('.key', '.pem')):
        return False
    if path.startswith(('/etc/', '/var/spool/cron/', '/var/spool/at/')): return True
    if path.startswith('/var/log/') or name in ('.bash_history', '.history', '.bashrc', '.bash_profile', '.profile', 'authorized_keys', 'known_hosts'):
        return True
    if path.startswith(('/bin/','/sbin/','/lib/','/lib64/','/usr/bin/','/usr/sbin/','/usr/lib/','/usr/lib64/','/var/lib/rpm/','/var/lib/dpkg/')):return True
    return bool(stat.S_ISREG(mode) and (mode & 0o111 or re.search(r'\.(?:php\d?|phtml|jsp|py|pl|so(?:\.[\d.]+)?|log(?:\.\d+)*|txt|sh|conf|ini|service|socket|timer|json|yaml|yml|pcap|cap|tar|tgz|gz|bz2)$', name, re.I)))


def priority(item):
    path = item['path']
    if path.startswith(('/etc/', '/var/spool/')): p = 0
    elif re.search(r'/\.(?:bash_history|history|ssh/)|^/var/log/(?:secure|auth|bash_his|wtmp|btmp|cron|audit/)', path): p = 1
    elif path.startswith(('/root/', '/tmp/', '/var/tmp/')): p = 2
    elif path.startswith('/var/log/'): p = 3
    else: p = 4
    return p, -item['mtime'], path


def scan(image, output_root):
    from dissect.target import Target
    from dissect.target.filesystems.xfs import XfsFilesystem
    from dissect.target.filesystems.extfs import ExtFilesystem
    root = Path(output_root); root.mkdir(parents=True, exist_ok=True)
    run = root / ('RUN-' + uuid.uuid4().hex); run.mkdir(); (run / 'objects').mkdir()
    started = time.monotonic(); progress_path = root / 'progress.json'
    def progress(stage, **kw):
        temp = run / 'progress.tmp'
        temp.write_text(json.dumps({'stage': stage, 'elapsed_seconds': round(time.monotonic() - started), **kw}), encoding='utf-8')
        temp.replace(progress_path)
    target = Target.open(str(image), apply=False); target.disks.apply()
    filesystems = []; scopes = []; entries = []; errors = []; groups = {}; processed = 0; source_count = 0
    for vol in target.volumes:
        vol.seek(0); header = vol.read(4096); vol.seek(0)
        scope = {'partition_offset': vol.offset, 'size': vol.size}; scopes.append(scope)
        try:
            if header[:4] == b'XFSB': fs = XfsFilesystem(vol); scope['filesystem'] = 'xfs'
            elif header[1080:1082] == b'\x53\xef': fs = ExtFilesystem(vol); scope['filesystem'] = 'ext'
            else:
                scope.update(status='excluded', reason='swap/지원하지 않는 파일시스템. 원시 영역·삭제 파일 복구 미수행'); continue
            try: fs.get('/etc/passwd')
            except FileNotFoundError:
                scope.update(status='excluded', reason='Linux 루트가 아님. 별도 마운트 볼륨의 파일 경로 연결 미수행'); continue
            scope['status'] = 'read_only'; filesystems.append((fs, vol.offset))
        except Exception as ex: scope.update(status='error', reason=str(ex))
    if not filesystems: raise ValueError('분석 가능한 Linux 루트 파일시스템을 찾지 못했습니다.')
    fs_by_offset = dict((off, fs) for fs, off in filesystems)
    candidate_nodes = {}; unclassified_samples=[]
    env = {'timezone': None, 'timezone_basis': '미확인', 'hostname': None, 'os': None}
    tz = None
    fs = filesystems[0][0]
    for path, key in (('/etc/hostname', 'hostname'), ('/etc/os-release', 'os'), ('/etc/redhat-release', 'os')):
        try:
            with fs.get(path).open() as fh: env[key] = fh.read(4096).decode(errors='replace').strip()
        except Exception: pass
    try:
        node = fs.get('/etc/localtime')
        if node.is_symlink():
            link = str(node.readlink()); zone = link.split('zoneinfo/')[-1]
            tz = ZoneInfo(zone); env.update(timezone=zone, timezone_basis='/etc/localtime → ' + link)
        else:
            with node.open() as fh: tz = ZoneInfo.from_file(fh)
            env.update(timezone='이미지 TZif', timezone_basis='/etc/localtime TZif')
    except Exception as ex: env['timezone_error'] = str(ex)
    total_entries=0
    inventory = (run / 'filesystem_inventory.ndjson').open('w', encoding='utf-8')
    for fs, offset in filesystems:
        roots = list(ROOTS)
        try:
            for de in fs.get('/').scandir():
                node = de.get()
                if node.path not in roots and node.path not in EXCLUDE: roots.append(node.path)
        except Exception as ex:errors.append({'partition_offset':offset,'path':'/','operation':'root_enumeration','error':str(ex)[:500]})
        visited = set(); count = 0
        for path in roots:
            try:pending = [fs.get(path)]
            except FileNotFoundError:continue
            except Exception as ex:
                errors.append({'partition_offset':offset,'path':path,'operation':'root_lookup','error':str(ex)[:500]});continue
            while pending and count < MAX_ENTRIES:
                node = pending.pop();path = node.path
                try:
                    s = node.lstat()
                    item = {'path': path, 'partition_offset': offset, 'inode': s.st_ino, 'mode': s.st_mode,
                            'uid': s.st_uid, 'gid': s.st_gid, 'size': s.st_size, 'mtime': s.st_mtime, 'ctime': s.st_ctime,
                            'atime': s.st_atime, 'birthtime': getattr(s, 'st_birthtime', None), 'allocation':'allocated namespace entry'}
                    if stat.S_ISLNK(s.st_mode): item['symlink'] = str(node.readlink())
                    inventory.write(json.dumps(item, ensure_ascii=False) + '\n'); count += 1
                    if stat.S_ISDIR(s.st_mode) and s.st_ino not in visited:
                        visited.add(s.st_ino)
                        pending.extend(sorted((de.get() for de in node.scandir()),key=lambda n:n.path, reverse=True))
                    elif stat.S_ISREG(s.st_mode) and candidate(path, s.st_mode):
                        entries.append(item);candidate_nodes[(offset,path)]=node
                    elif stat.S_ISREG(s.st_mode) and posixpath.basename(path) not in ('shadow','gshadow','id_rsa','id_dsa','id_ecdsa','id_ed25519') and not path.endswith(('.key','.pem')):
                        # Fixed, content-independent sample of files outside the
                        # candidate rules. No AI-selected keyword controls it.
                        rank=hashlib.sha256(f'{offset}:{path}'.encode()).hexdigest()
                        unclassified_samples.append((rank,item,node))
                        unclassified_samples.sort(key=lambda x:x[0]);del unclassified_samples[16:]
                    elif stat.S_ISLNK(s.st_mode) and path.startswith('/etc/'):
                        entries.append(item)
                    if count % 2000 == 0: progress('linux_discovery', entries=count, candidates=len(entries), path=path)
                except Exception as ex: errors.append({'partition_offset':offset,'path': path, 'operation': 'enumeration', 'error': str(ex)[:500]})
            if pending: errors.append({'path': path, 'operation': 'enumeration', 'error': 'namespace budget reached'})
        total_entries+=count
    count=total_entries
    inventory.close()
    for _,item,node in unclassified_samples:
        entries.append({**item,'baseline_sample':True});candidate_nodes[(item['partition_offset'],item['path'])]=node
    events_file = (run / 'events.ndjson').open('w', encoding='utf-8')
    ledger = []; event_count = 0; group_overflow = 0; type_counts = {}; decoded_total = 0

    def record(event, source):
        nonlocal event_count, group_overflow
        event['source_location'] = f"{Path(image).name}:byte:{source['partition_offset']}:{source['path']}:inode:{source['inode']}:offset:{event['fields'].get('image_file_byte_offset', event['fields'].get('byte_offset', 0))}"
        fields = event['fields']
        fields.update(source_sha256=source['sha256'], source_complete=source['complete'],
                      artifact_path=f"{run.name}/{source['relative_path']}", partition_offset=source['partition_offset'], inode=source['inode'])
        events_file.write(json.dumps(event, ensure_ascii=False) + '\n'); event_count += 1
        # Keep all records on disk; the interactive index groups repetitions.
        key = json.dumps([source['partition_offset'],source['inode'],source['path'], event['type'], fields.get('command'), fields.get('cwd'), fields.get('user'),
                          fields.get('address'), fields.get('outcome'), fields.get('state'),
                          fields.get('excerpt') if event['type'] not in ('linux_authentication', 'linux_cron_call', 'linux_command', 'linux_login_record') else None,
                          fields.get('host'), fields.get('record_type')], ensure_ascii=False)
        if key in groups:
            old = groups[key]['fields']; old['occurrences'] += 1
            ts = event['timestamp']
            if ts:
                old['first_observed'] = min(old.get('first_observed') or ts, ts)
                old['last_observed'] = max(old.get('last_observed') or ts, ts)
            old['last_byte_offset'] = fields.get('byte_offset'); old['last_line'] = fields.get('line')
        elif event['type']=='linux_detection' or len(groups) < MAX_GROUPS and type_counts.get(event['type'],0) < 1800:
            fields.update(occurrences=1, first_observed=event['timestamp'], last_observed=event['timestamp'])
            groups[key] = event
            type_counts[event['type']] = type_counts.get(event['type'],0)+1
        else: group_overflow += 1

    from .hunt_stream import Hunter
    hunter=Hunter(run,record,tz)
    progress('linux_contents', entries=count, candidates=len(entries), files_done=0)
    for item in sorted(entries, key=priority):
        path = item['path']; fs = fs_by_offset[item['partition_offset']]
        source = {**item, 'relative_path': None, 'sha256': None, 'complete': False, 'status': 'excluded', 'records': 0}
        ledger.append(source)
        if 'symlink' in item:
            source.update(status='symlink', reason='링크 대상은 별도 증거 경로로 해석; 호스트 경로로 따라가지 않음'); continue
        try:
            hunter.scan(candidate_nodes[(item['partition_offset'],path)],item)
        except Exception as ex:
            hunter.files[-1].update(status='error',reason=str(ex)[:500])
        if hunter.count%100==0:progress('linux_hunt',files_done=hunter.count,candidates=len(entries),detections=hunter.findings,bytes_read=sum(hunter.initial[k]-v for k,v in hunter.remaining.items()))
        if processed >= MAX_BYTES:
            source['reason'] = '기본 파서 원문 보존 예산 도달. 별도 스트리밍 헌팅 범위는 hunt_coverage 참조'
            candidate_nodes.pop((item['partition_offset'],path),None)
            continue
        try:
            with candidate_nodes[(item['partition_offset'],path)].open() as fh: data = fh.read(min(MAX_FILE, MAX_BYTES - processed))
            processed += len(data); source_count += 1
            digest = hashlib.sha256(data).hexdigest(); relative = 'objects/' + digest + '.bin'
            destination = run / relative
            if not destination.exists(): destination.write_bytes(data)
            source.update(relative_path=relative, sha256=digest, extracted_bytes=len(data),
                          complete=len(data) == item['size'], status='extracted',
                          hash_scope='full file' if len(data) == item['size'] else 'extracted prefix only')
            if not source['complete']: source['reason'] = '파일 크기 예산: 앞부분만 보존·분석'
            decoded = data
            if data.startswith(b'\x1f\x8b'):
                import io
                available=min(MAX_FILE,MAX_BYTES-decoded_total)
                if available<=0:
                    source.update(parser='none',reason='압축 해제 총 512 MiB 예산 도달. 압축 원문만 보존');continue
                with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream: decoded = stream.read(available + 1)
                source['compression'] = 'gzip'; source['decoded_complete'] = len(decoded) <= available
                if not source['decoded_complete']: decoded = decoded[:available]
                decoded_total+=len(decoded)
                # Store exactly the bytes to which line/offset locators refer.
                digest = hashlib.sha256(decoded).hexdigest(); rel = 'objects/' + digest + '.bin'
                (run / rel).write_bytes(decoded)
                source.update(compressed_sha256=source['sha256'], compressed_relative_path=source['relative_path'],
                              sha256=digest, relative_path=rel, complete=source['complete'] and source['decoded_complete'],
                              locator_basis='gzip decompressed bytes')
            if decoded.startswith(b'\x7fELF'):
                strings = re.findall(rb'[\x20-\x7e]{6,500}', decoded)
                hits = [s.decode() for s in strings if re.search(rb'(?:stratum|xmrig|cryptonight|/dev/tcp|LD_PRELOAD|https?://)', s, re.I)][:30]
                event = {'type': 'linux_binary', 'timestamp': None, 'fields': {'path': path, 'byte_offset': 0,
                    'format': 'ELF', 'bits': 64 if decoded[4] == 2 else 32, 'machine': int.from_bytes(decoded[18:20], 'little' if decoded[5] == 1 else 'big'),
                    'size': item['size'], 'strings': hits, 'indicators': indicators('\n'.join(hits)), 'stage': '정적 파일 확보',
                    'judgment': '확정', 'interpretation_limit': '실행하지 않음. 문자열·ELF 형식은 악성 여부·실제 행위의 증거가 아님'}}
                record(event, source); source['parser'] = 'ELF header + bounded strings'; continue
            if re.search(r'/(?:wtmp|btmp)(?:[-.]|$)', path) and not path.endswith('.gz'):
                iterator = utmp_events(path, decoded); source['parser'] = 'Linux glibc utmp'
            elif b'\0' in decoded[:8192]:
                source.update(parser='none', reason='바이너리 형식: 원문 보존, 전용 파서 미지원'); continue
            else:
                # Stream parsing already retained these events. A second read is not
                # an independent occurrence and must not inflate event counts.
                if hunter.files[-1].get('text_stream_parsed'):
                    source['parser']='stream parser ledger';continue
                iterator = text_events(path, decoded, item['mtime'], tz); source['parser'] = VERSION
            minimum = maximum = None
            for event in iterator:
                if event['timestamp']:
                    minimum = min(minimum or event['timestamp'], event['timestamp']); maximum = max(maximum or event['timestamp'], event['timestamp'])
                record(event, source); source['records'] += 1
            source.update(lines=decoded.count(b'\n'), first_observed=minimum, last_observed=maximum,
                          negative_search='지정된 파서에서 행위 레코드 미발견; 전체 행위 부재를 뜻하지 않음' if not source['records'] else None)
        except Exception as ex:
            source.update(status='error', reason=f'{type(ex).__name__}: {ex}'[:800])
        finally:
            candidate_nodes.pop((item['partition_offset'],path),None)
            if source_count % 20 == 0: progress('linux_contents', files_done=source_count, candidates=len(entries), bytes_read=processed, records=event_count, path=path)
    from .package_audit import audit as package_audit
    package_results=[package_audit(fs,offset,hunter) for fs,offset in filesystems]
    ledger.extend(hunter.sources)
    from .coverage_map import summarize, source_matrix
    coverage_map=summarize(hunter.files,ledger)
    coverage_map['source_matrix']=source_matrix(run/'filesystem_inventory.ndjson',hunter.files,scopes,errors)
    (run/'hunt_files.ndjson').write_text(''.join(json.dumps(f,ensure_ascii=False)+'\n' for f in hunter.files),encoding='utf-8')
    events_file.close()
    manifest = {'version': VERSION, 'image': Path(image).name, 'run_id': run.name, 'environment': env, 'scopes': scopes,
                'sources': ledger, 'enumeration_errors': errors, 'entries': count, 'event_count': event_count,
                'hunt_coverage':hunter.manifest(), 'coverage_map':coverage_map, 'package_audit':package_results, 'indexed_groups': len(groups), 'index_omitted_records': group_overflow, 'bytes_extracted': processed,'bytes_decompressed':decoded_total,
                'limitations': ['할당 파일을 읽는 내용 조사. 삭제 inode·미할당·swap·메모리 복구 미수행',
                    '별도 데이터 볼륨의 fstab 마운트 연결·컨테이너 내부 계층 분석 미수행',
                    'sar·journal·패킷·TAR 전용 파서 미지원; 패키지 DB 대조 지원 여부는 package_audit 참조',
                    '연도 없는 syslog는 회전명/mtime와 이미지 시간대에 따른 추정',
                    '기본 파서: 파일 16 MiB·보존 512 MiB. 별도 헌팅: 파일 256 MiB·자료군별 총 8 GiB 기본값, ELF 32 MiB. 열거 최대 100만 항목. 초과·미지원은 원장에 명시'],
                'elapsed_seconds': round(time.monotonic() - started, 3)}
    (run / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    sums = {str(p.relative_to(run)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in run.rglob('*') if p.is_file() and p.name != 'progress.tmp'}
    (run / 'SHA256SUMS.json').write_text(json.dumps(sums, sort_keys=True), encoding='utf-8')
    # Compact inventory summaries remain searchable in the application.
    obs = list(groups.values())
    obs.insert(0, {'type': 'linux_environment', 'timestamp': None, 'source_location': Path(image).name,
                  'fields': {**env, 'hunt_coverage':hunter.manifest(), 'coverage_map':coverage_map, 'run_id': run.name, 'entries': count, 'sources': source_count, 'events': event_count,
                             'artifact_path': f'{run.name}/manifest.json', 'source_sha256': sums['manifest.json']}})
    obs.append({'type': 'linux_coverage', 'timestamp': None, 'source_location': Path(image).name,
                'fields': {'run_id': run.name, 'limits': manifest['limitations'], 'errors': len(errors),
                           'truncated_files': sum(not s['complete'] and s['status'] == 'extracted' for s in ledger),
                           'unparsed_files': sum(s.get('parser') == 'none' for s in ledger),
                           'excluded_files': sum(s['status'] in ('error', 'excluded') for s in ledger),
                           'artifact_path': f'{run.name}/manifest.json', 'source_sha256': sums['manifest.json']}})
    result = {'status': 'partial', 'complete': False, 'truncated': bool(group_overflow), 'tool': VERSION, 'version': '1',
              'observations': obs, 'run_id': run.name, 'manifest_sha256': sums['manifest.json'],
              'event_count': event_count, 'source_count': source_count, 'elapsed_seconds': manifest['elapsed_seconds'],
              'error': 'Linux 원문 조사·행위 기록 추출 완료. 미지원·미보존·예산 제외 영역 및 가설 검토는 별도 확인 필요.'}
    (run / 'result.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    progress('linux_done', run_id=run.name, files_done=source_count, records=event_count)
    return result


if __name__ == '__main__':
    result = scan(sys.argv[1], sys.argv[2])
    # The parent reads the result from the derived volume, avoiding stdout limits.
    print(json.dumps({'run_id': result['run_id']}))
