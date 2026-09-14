"""Local hunting rules: observations of code/configuration, never an infection verdict.

Rules operate on every supplied line, not on the model's selected context.
No evidence text is executed. Source kind and opposing context remain explicit.
"""
import hashlib
import posixpath
import re
import shlex

VERSION = "linux-hunt-2"
RULES = [
    ("download_execute", "다운로드와 셸 실행 연결", "high", r"(?:curl|wget)\b[^\n]{0,1000}\|\s*(?:/bin/)?(?:ba)?sh\b"),
    ("reverse_shell", "외부 소켓과 셸 입출력 연결", "high", r"(?:/dev/tcp/[^\s;]+|\bnc(?:at)?\b[^\n]{0,250}\s-e\s+(?:/bin/)?(?:ba)?sh\b|\bsocat\b[^\n]{0,250}EXEC:[^\n]{0,80}(?:sh|bash))"),
    ("python_socket_shell", "소켓·파일서술자·셸 연결 코드", "high", r"socket\.[^\n]{0,1500}(?:dup2|subprocess)[^\n]{0,1000}(?:/bin/sh|/bin/bash)"),
    ("history_clear", "명령 이력 제거 또는 기록 억제", "medium", r"(?:\bhistory\s+-c\b|\bHISTFILE\s*=\s*(?:/dev/null|[\"\']{2})|\bunset\s+HISTFILE\b)"),
    ("log_remove", "로그 파일 삭제·비우기 명령", "high", r"(?:\b(?:rm|shred|truncate)\b[^\n]{0,180}/var/log/|(?:>|cp\s+/dev/null\s+)\s*/var/log/)"),
    ("preload_environment", "라이브러리 사전 로드 지정", "medium", r"\bLD_PRELOAD\s*="),
    ("firewall_disable", "방화벽 규칙 초기화·중지 명령", "medium", r"(?:\biptables\s+(?:-[A-Za-z]+\s+)*-F\b|\bsystemctl\s+(?:stop|disable)\s+firewalld\b)"),
    ("webshell_code", "웹 입력에서 명령 실행 함수로 연결", "high", r"(?:eval|assert|system|shell_exec|passthru|popen)\s*\([^\n]{0,250}\$_(?:POST|GET|REQUEST|COOKIE)\s*\["),
]
COMPILED = [(a,b,c,re.compile(d,re.I)) for a,b,c,d in RULES]
LITERAL_COMMANDS = {'echo','printf','grep','egrep','fgrep','rg','strings'}


def executable_text(line):
    """Exclude pure comments/literal inspection commands, without trusting filenames."""
    stripped=line.strip()
    if not stripped or stripped.startswith('#'): return '', 'comment'
    try:
        lexer=shlex.shlex(stripped,posix=True,punctuation_chars=';&|')
        lexer.whitespace_split=True
        tokens=list(lexer)
        segments=[]; current=[]
        for token in tokens:
            if token in (';','&&','||','|','&'):
                if current: segments.append(current)
                current=[]
            else:current.append(token)
        if current:segments.append(current)
        if segments and all(posixpath.basename(s[0]) in LITERAL_COMMANDS for s in segments):return '', 'literal inspection/output'
    except ValueError:pass
    return stripped, 'code_or_record'


def text_hits(path, data, base_offset=0, first_line=1):
    offset=base_offset
    for number, raw in enumerate(data.splitlines(keepends=True), first_line):
        start=offset;offset+=len(raw)
        line=raw.decode('utf-8',errors='replace')
        if not re.search(r'curl|wget|/dev/tcp|nc(?:at)?|socat|socket|history|HISTFILE|/var/log|LD_PRELOAD|iptables|firewalld|eval|assert|system|shell_exec|passthru|popen|/tmp/|/dev/shm/',line,re.I) and path not in ('/etc/passwd','/etc/ld.so.preload'):continue
        code,context=executable_text(line)
        if not code:continue
        rules=[]
        for rule,title,severity,pattern in COMPILED:
            if pattern.search(code):rules.append((rule,title,severity))
        if path=='/etc/ld.so.preload' and code.split('#')[0].strip():rules.append(('preload_config','전역 라이브러리 사전 로드 설정','high'))
        if path=='/etc/passwd':
            parts=code.split(':')
            if len(parts)>=7 and parts[2]=='0' and parts[0]!='root':rules.append(('extra_uid_zero','root 이외 UID 0 계정','high'))
        if re.search(r'/cron(?:\.|/)|/systemd/(?:system|user)/|/rc\.local$',path) and re.search(r'(?:/tmp/|/var/tmp/|/dev/shm/)',code):
            rules.append(('writable_persistence','쓰기 쉬운 경로의 파일을 자동 호출하는 설정','high'))
        for rule,title,severity in rules:
            yield {'rule_id':rule,'title':title,'severity':severity,'rule_version':VERSION,
                   'path':path,'line':number,'byte_offset':start,'byte_length':len(raw),
                   'excerpt':line[:2400], 'context':context,
                   'stage':'설정 내용' if path.startswith('/etc/') else '명령 기록' if '/log/' in path or 'history' in path else '파일 코드',
                   'referenced_paths':sorted(set(re.findall(r'/(?:[A-Za-z0-9_.@+-]+/)*[A-Za-z0-9_.@+-]+',code)))[:12],
                   'interpretation_limit':'패턴이 있는 원문을 발견. 실행·악성 의도·목적 달성은 별도 관련 근거로 판단.'}


def rule_digest():
    return hashlib.sha256(repr(RULES).encode()).hexdigest()
