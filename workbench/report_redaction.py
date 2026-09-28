"""Distribution-only credential masking. Never mutates the evidence ledger."""
import re

MASK='[민감값 삭제]'
SECRET_KEY=re.compile(r'(?i)(password|passwd|passphrase|secret|token|api[_-]?key|private[_-]?key|authorization|cookie|비밀번호|암호|인증키)')
PRIVATE=re.compile(r'-----BEGIN (?:[A-Z ]*PRIVATE KEY)-----[\s\S]*?-----END (?:[A-Z ]*PRIVATE KEY)-----')
ASSIGN=re.compile(r'''(?ix)(?:password|passwd|passphrase|secret|(?:access|refresh|session|api)[_-]?(?:token|key)|token|api[_-]?key|비밀번호|암호)\s*["']?\s*[:=]\s*(?:"([^"\n]+)"|'([^'\n]+)'|([^\s,;<>]+))''')
BEARER=re.compile(r'(?i)\b(?:Bearer|Basic)\s+([A-Za-z0-9_+/.=-]+)')
URL_AUTH=re.compile(r'\b[a-z]+://([^\s/:@]+):([^\s/@]+)@',re.I)
CLI=re.compile(r'''(?ix)(?:--password(?:=|\s+)|sshpass\s+-p\s+|(?:curl\s+.*?\s|^)-u\s+[^:\s]+:)(?:"([^"\n]+)"|'([^'\n]+)'|([^\s;]+))''')
COOKIE=re.compile(r'(?im)(?:^|\n)(?:Cookie|Set-Cookie|Authorization)\s*:\s*([^\n]+)')
TOKEN=re.compile(r'\b(?:eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,})\b')
CONTROL=re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]')


def redact(value):
    secrets=set()
    def collect(v,key=''):
        if isinstance(v,dict):
            for k,item in v.items():collect(item,str(k))
        elif isinstance(v,list):
            for item in v:collect(item,key)
        elif isinstance(v,str):
            if SECRET_KEY.search(key) and v:secrets.add(v)
            for pattern in (ASSIGN,BEARER,CLI,COOKIE):
                for m in pattern.finditer(v):secrets.update(g for g in m.groups() if g)
            for m in URL_AUTH.finditer(v):secrets.add(m[2])
            secrets.update(PRIVATE.findall(v));secrets.update(TOKEN.findall(v))
    collect(value)
    # Replace even unlabelled echoes of a discovered credential in model prose.
    pattern=re.compile('|'.join(re.escape(v) if len(v)>=4 else r'(?<![\w-])'+re.escape(v)+r'(?![\w-])'
        for v in sorted(secrets,key=len,reverse=True))) if secrets else None
    def scrub(v,key=''):
        if SECRET_KEY.search(key) and v is not None:return MASK
        if isinstance(v,dict):return {k:scrub(item,str(k)) for k,item in v.items()}
        if isinstance(v,list):return [scrub(item,key) for item in v]
        if isinstance(v,str):
            v=PRIVATE.sub(MASK,v)
            v=pattern.sub(MASK,v) if pattern else v
            # Forensic excerpts can contain control bytes that OOXML forbids.
            # Preserve an explicit escaped representation, not hidden content.
            return CONTROL.sub(lambda m:'\\u'+format(ord(m[0]),'04x'),v)
        return v
    return scrub(value)
