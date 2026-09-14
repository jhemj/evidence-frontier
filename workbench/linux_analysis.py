"""Read-only Linux artefact interpretation. Recorded events are not attribution.

Parsers retain original lines/byte offsets; a syslog year inferred from rotation
or mtime is explicitly distinguished from a timestamp present in the source.
No command or programme obtained from evidence is ever executed.
"""
import hashlib
import ipaddress
import posixpath
import re
import struct
from datetime import datetime, timezone

VERSION = 'linux-investigation-1'
MONTHS = {m: i + 1 for i, m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}
CHECK_TOOL = re.compile(r'(?:bpfdoor_inspect|rootkit.*scan|ldpreload_scan|scanner|forensic_dd|security_linux|itam_scan|Linux_26)', re.I)
SIGNALS = {
    'download': re.compile(r'\b(?:curl|wget|ftp|tftp)\b', re.I),
    'transfer': re.compile(r'\b(?:scp|sftp|rsync|nc|ncat|socat)\b', re.I),
    'archive': re.compile(r'\b(?:tar|zip|7z|gzip)\b', re.I),
    'log_change': re.compile(r'(?:history\s+-c|(?:rm|truncate|shred)\b.*(?:/var/log|history)|>\s*/var/log)', re.I),
    'execution': re.compile(r'(?:\bnohup\b|\bchmod\s+.*\+x|\./[^\s]+|/dev/tcp/|\bbase64\b)', re.I),
    'miner_marker': re.compile(r'(?:stratum\+tcp|xmrig|cryptonight|pwnrig)', re.I),
    'preload': re.compile(r'(?:LD_PRELOAD|ld\.so\.preload)', re.I),
    'inspection': re.compile(r'(?:bpfdoor|rootkit|rpm\s+-V|rpm\s+-qa|ldpreload_scan|forensic_dd|tcpdump|\bps\s|netstat|\bss\s)', re.I),
}


def indicators(text):
    values = []
    for raw in dict.fromkeys(re.findall(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?::\d{1,5})?(?![\d.])', text)):
        host, _, port = raw.partition(':')
        try:
            ip = ipaddress.ip_address(host)
            if port and int(port) > 65535: continue
        except ValueError: continue
        values.append({'type': 'ip', 'value': host, 'port': port or None,
                       'scope': 'loopback' if ip.is_loopback else 'external' if ip.is_global else 'internal_or_reserved'})
    for url in dict.fromkeys(re.findall(r'(?:https?|ftp|stratum\+tcp)://[^\s\x00<>"\']+', text)):
        values.append({'type': 'url', 'value': url[:500], 'scope': 'unclassified'})
    return values[:30]


def stamp(line, mtime, tz, path):
    audit = re.search(r'audit\((\d{9,12}(?:\.\d+)?):', line)
    if audit:
        return datetime.fromtimestamp(float(audit[1]), timezone.utc).isoformat(), 'audit Unix epoch'
    iso = re.search(r'\b(20\d\d-\d\d-\d\d)[T ](\d\d:\d\d:\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?', line)
    if iso:
        try:
            value = datetime.fromisoformat(iso[0].replace('Z', '+00:00'))
            if value.tzinfo: return value.isoformat(), '원문 날짜·시간대'
            if tz: return value.replace(tzinfo=tz).isoformat(), '원문 날짜 + 이미지 시간대'
        except ValueError: pass
    syslog = re.match(r'([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d\d:\d\d:\d\d)', line)
    if syslog and tz and syslog[1] in MONTHS:
        anchor = datetime.fromtimestamp(mtime, tz)
        rotation = re.search(r'(20\d{2})(\d{2})(\d{2})', path)
        year = int(rotation[1]) if rotation else anchor.year
        try:
            value = datetime(year, MONTHS[syslog[1]], int(syslog[2]), *map(int, syslog[3].split(':')), tzinfo=tz)
            # Rotation dates are end boundaries, not a guarantee of event year.
            boundary = datetime.strptime(rotation[0], '%Y%m%d').replace(tzinfo=tz) if rotation else anchor
            if (value - boundary).days > 2: value = value.replace(year=year - 1)
            return value.isoformat(), '연도 추정(회전 파일명)' if rotation else '연도 추정(파일 mtime); 이미지 시간대'
        except ValueError: pass
    return None, '원문에 확정 가능한 시각·시간대 없음'


def normalize_command(command, cwd=None):
    paths = []
    for token in re.findall(r'(?<!\w)(?:/|\./|\.\./)[\w./+@%=-]+', command):
        if token.startswith('/'):
            path = posixpath.normpath(token)
        elif cwd:
            path = posixpath.normpath(posixpath.join(cwd, token))
        else: path = None
        paths.append({'original': token, 'absolute': path, 'basis': '기록된 작업경로' if cwd else '절대경로 또는 작업경로 미확인'})
    return paths[:20]


def text_events(path, data, mtime, tz):
    """Yield significant records; coverage separately records every scanned line."""
    name = posixpath.basename(path)
    history = name in ('.bash_history', '.history', '.zsh_history')
    persistence = bool(re.search(r'^/etc/(?:cron|anacrontab|systemd/system|init\.d|rc\.|profile|bashrc|ld\.so\.preload|pam\.d)|^/var/spool/(?:cron|at)|/\.(?:bashrc|bash_profile|profile)$', path))
    config = path.startswith('/etc/') and not persistence
    inspection_source = bool(CHECK_TOOL.search(name))
    inspection_report = bool(re.search(r'(?:report|result|BPFDoor).*\.(?:txt|log)$', name, re.I))
    byte_offset = 0; history_time = None
    for number, raw in enumerate(data.splitlines(keepends=True), 1):
        offset = byte_offset; byte_offset += len(raw)
        line = raw.decode('utf-8', errors='replace').rstrip('\r\n')
        if len(line) > 16000: continue
        if history and re.fullmatch(r'#\d{9,12}', line):
            try: history_time = datetime.fromtimestamp(int(line[1:]), timezone.utc).isoformat()
            except (ValueError, OverflowError, OSError): history_time = None
            continue
        if not line.strip(): continue
        timestamp, time_basis = stamp(line, mtime, tz, path)
        fields = {'path': path, 'line': number, 'byte_offset': offset, 'byte_length': len(raw),
                  'excerpt': line[:1800], 'time_basis': time_basis, 'judgment': '확정',
                  'interpretation_limit': '기록 존재에 대한 판정이며 행위자·악성 여부·목적 달성을 뜻하지 않음'}
        kind = None
        command = re.search(r'CMD:\s+\S+\s+(\S+)\s+.*?\s(\d+)\s+(/\S*)\s+C=\s*\d+\s+(.*)', line)
        cron = re.search(r'CROND?\[(\d+)\]:\s+\(([^)]+)\)\s+CMD\s+\((.*)\)', line)
        auth = re.search(r'(Accepted|Failed) (\S+) for (?:invalid user )?(\S+) from ([\d.]+) port (\d+)', line)
        if command:
            kind = 'linux_command'
            fields.update(user=command[1], pid=command[2], cwd=command[3], command=command[4], stage='명령 기록')
        elif cron:
            kind = 'linux_cron_call'
            fields.update(pid=cron[1], user=cron[2], command=cron[3], stage='예약 호출',
                          interpretation_limit='cron 호출 기록. 실제 프로그램 시작·종료코드·통신 성공은 별도 확인 필요')
        elif auth:
            kind = 'linux_authentication'
            fields.update(user=auth[3], method=auth[2], address=auth[4], port=int(auth[5]),
                          outcome='accepted' if auth[1] == 'Accepted' else 'failed', stage='인증 성공' if auth[1] == 'Accepted' else '인증 실패')
        elif re.search(r'(?:session (?:opened|closed)|sudo:.*COMMAND=|authentication failure|PAM.*(?:faulty|unable))', line):
            kind = 'linux_session'; fields['stage'] = '세션·권한 기록'
        elif history:
            kind = 'linux_command'; fields.update(command=line, user=path.split('/')[1] if path.startswith('/root/') else path.split('/')[2], stage='셸 이력')
            timestamp, fields['time_basis'] = history_time, 'bash history epoch' if history_time else '셸 이력 시각 미보존'
            history_time = None
        elif persistence and not line.lstrip().startswith(('#', ';')):
            kind = 'linux_persistence'; fields.update(command=line, stage='설정 등록',
                interpretation_limit='설정 내용. 실행·통신·목적 달성 증거와 구분; 스크립트 내부 명령의 도달 여부 미확인')
        elif path == '/etc/passwd' and len(line.split(':')) == 7:
            user, _, uid, gid, gecos, home, shell = line.split(':')
            kind = 'linux_account'; fields.update(user=user, uid=uid, gid=gid, home=home, shell=shell)
            fields['excerpt'] = ':'.join([user, '[credential field omitted]', uid, gid, gecos, home, shell])
        elif re.search(r'/(?:authorized_keys|known_hosts)(?:$|\.)', path):
            kind = 'linux_ssh_trust'; fields.update(stage='키·호스트 등록', key_record_sha256=hashlib.sha256(raw).hexdigest())
            fields['excerpt'] = (line.split()[0] if line.split() else '') + ' [키 본문은 추출 원문 참조]'
        elif inspection_report and re.search(r'(?:종합판정|판정\s*:|\[(?:DETECT|REVIEW|WARN|PASS|FAIL|정상|탐지)\]|suspicious|not found)', line, re.I):
            kind = 'linux_inspection_result'; fields.update(stage='기존 점검 결과',
                interpretation_limit='이미지에 보존된 이전 점검 결과. 현재 분석기의 직접 검증이나 시스템 정상 확정이 아님')
        elif not inspection_source and re.search(r'type=(?:EXECVE|SYSCALL|SOCKADDR|CWD|PATH|USER_CMD|USER_AUTH)\b', line):
            kind = 'linux_audit'; fields['stage'] = 'audit 기록'
            fields['audit_id'] = (re.search(r'audit\(([^)]+)\)', line) or ['', ''])[1]
        elif not inspection_source and re.search(r'\b(?:ESTABLISHED|SYN_SENT|LISTEN|CLOSE_WAIT)\b', line) and re.match(r'^\s*(?:tcp|udp|ESTAB|SYN-SENT|LISTEN)\s', line):
            kind = 'linux_network'; fields['stage'] = '소켓 상태 저장본'
            fields['state'] = (re.search(r'\b(?:ESTABLISHED|SYN_SENT|LISTEN|CLOSE_WAIT)\b', line) or ['', 'unknown'])[0]
            fields['interpretation_limit'] = '저장본의 기록 시점·프로세스 귀속 확인 필요; 세션 성립은 목적 달성 증거가 아님'
        elif re.match(r'^\s*\S+\s+\d+\s+\d+\s+(?:\d|[A-Z])', line) and ('ps' in name.lower() or 'process' in name.lower()):
            kind = 'linux_process'; fields['stage'] = '프로세스 목록 저장본'
        elif config and (path in ('/etc/hostname', '/etc/redhat-release', '/etc/os-release', '/etc/hosts', '/etc/resolv.conf', '/etc/fstab', '/etc/sudoers', '/etc/ssh/sshd_config', '/etc/logrotate.conf') or path.startswith(('/etc/sysconfig/network', '/etc/logrotate.d/', '/etc/sudoers.d/'))):
            if not line.lstrip().startswith('#'): kind = 'linux_configuration'; fields['stage'] = '설정값'
        elif not inspection_source and re.search(r'(?:Starting |Started |Stopped |shutdown|reboot|Out of memory|Killed process|segfault|link.*down|time.*changed)', line, re.I):
            kind = 'linux_system_event'; fields['stage'] = '시스템 기록'
        if kind:
            text = fields.get('command', line)
            fields['signals'] = [k for k, pattern in SIGNALS.items() if pattern.search(text)]
            fields['inspection_context'] = inspection_source or inspection_report or bool(CHECK_TOOL.search(text))
            fields['indicators'] = indicators(text)
            if 'command' in fields: fields['referenced_paths'] = normalize_command(fields['command'], fields.get('cwd'))
            yield {'type': kind, 'timestamp': timestamp, 'fields': fields}


def utmp_events(path, data):
    """Linux glibc utmp (384 bytes, little endian); reject unknown layouts."""
    if len(data) % 384: raise ValueError('384-byte Linux utmp 레코드 크기와 불일치')
    for offset in range(0, len(data), 384):
        b = data[offset:offset + 384]; kind = struct.unpack_from('<h', b)[0]
        if kind not in range(10): raise ValueError('지원하지 않는 utmp 레코드 형식')
        if kind not in (2, 7, 8): continue
        epoch = struct.unpack_from('<i', b, 340)[0]
        if epoch <= 0: continue
        def cstr(a, z): return b[a:z].split(b'\0')[0].decode(errors='replace')
        yield {'type': 'linux_login_record', 'timestamp': datetime.fromtimestamp(epoch, timezone.utc).isoformat(),
               'fields': {'path': path, 'byte_offset': offset, 'byte_length': 384, 'record': offset // 384 + 1,
                          'pid': struct.unpack_from('<i', b, 4)[0], 'user': cstr(44, 76), 'host': cstr(76, 332), 'terminal': cstr(8, 40),
                          'record_type': kind, 'stage': '실패 로그인 기록' if 'btmp' in path else '로그인·로그아웃·부팅 기록',
                          'time_basis': 'utmp Unix epoch', 'judgment': '확정', 'indicators': indicators(cstr(76, 332)),
                          'interpretation_limit': '보존된 로그인 회계 기록; 행위 귀속·명령·침해 여부는 별도 검토'}}
