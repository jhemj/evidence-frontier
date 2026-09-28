"""Shipped, immutable-per-process investigation guidance, not permissions.

These are application playbooks, not executable Codex/Hermes skills. The code
identity and runtime binding pin the exact loaded guidance for each case.
"""
from functools import lru_cache
import hashlib
from pathlib import Path

VERSION = 'forensic-procedures-9'
FILES = ('common.md', 'linux.md', 'windows.md', 'source_triage.md', 'follow_through.md', 'reader_brief.md', 'hypothesis_evolution.md', 'temporal_reconstruction.md')


@lru_cache(maxsize=1)
def snapshot():
    root = Path(__file__).with_name('procedures')
    items = tuple((name, (root / name).read_text(encoding='utf-8')) for name in FILES)
    if any(len(text) > 12000 for _, text in items):
        raise ValueError('조사 절차 문서가 허용 크기를 초과했습니다.')
    digest = hashlib.sha256()
    for name, content in items:
        digest.update(name.encode()); digest.update(b'\0'); digest.update(content.encode()); digest.update(b'\0')
    return items, digest.hexdigest()


def identity(strategy='guided'):
    return {'version': VERSION, 'sha256': snapshot()[1], 'enabled': strategy == 'guided'}


def instructions(target_os, strategy='guided', role='investigator'):
    if strategy != 'guided':
        return ''
    if target_os not in ('linux', 'windows'):
        raise ValueError('지원하지 않는 조사 대상 OS입니다.')
    documents = dict(snapshot()[0])
    names=['common.md','source_triage.md',target_os+'.md','follow_through.md','temporal_reconstruction.md']
    if role in ('investigator','judgment','synthesis'):names.append('reader_brief.md')
    if role=='investigator':names.append('hypothesis_evolution.md')
    return '\n'.join(documents[name] for name in names)
