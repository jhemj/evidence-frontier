"""Lossless work partitioning, not a representative sample of large baselines.

Grouping keys are review conveniences, never proof of common actor/session.
Every input record belongs to exactly one work unit, including undated records.
"""
from collections import defaultdict
import hashlib
import json

BASELINE_FAMILIES = {'인증·SSH·권한': 'access', '예약작업·시스템 지속성': 'persistence',
                     '실행·통신·시스템 변경': 'execution','Windows 인증·계정·시스템':'access',
                     'Windows 예약작업·방어 설정':'persistence','Windows 실행·통신':'execution'}
PARTITION_VERSION = 'source-units-1'


def partition(title, items, key, size=12):
    unique = {o['id']: o for o in items}
    if not unique:
        return [(title, list(unique.values()), key)]
    buckets = defaultdict(list)
    for o in unique.values():
        f = o['fields']
        # Never join different images/partitions; a missing session stays missing.
        partition_scope=(f.get('partition_offset'),f.get('os_instance')) if o['type'].startswith('windows_') else f.get('partition_offset')
        scope = (o.get('evidence_id'), partition_scope, f.get('user', ''),
                 f.get('session_id', ''), (o.get('timestamp') or 'undated')[:10],
                 f.get('target_path') or f.get('path', ''))
        buckets[scope].append(o)
    if len(buckets)==1 and len(unique)<=size:
        return [(title,list(unique.values()),key)]
    result = []
    for scope, records in sorted(buckets.items(), key=lambda x: str(x[0])):
        records.sort(key=lambda o: (o.get('timestamp') or '', o['fields'].get('byte_offset') or 0, o['id']))
        scope_hash = hashlib.sha256(json.dumps(scope, ensure_ascii=False).encode()).hexdigest()[:16]
        for offset in range(0, len(records), size):
            chunk = records[offset:offset + size]
            label = ' / '.join(str(x) for x in (scope[2] or '계정 미상', scope[4], scope[5]) if x)
            result.append((f'{title} · {label} · {offset // size + 1}', chunk,
                           (key[0], key[1], f'{key[2]}::{scope_hash}:{offset // size}')))
    return result
