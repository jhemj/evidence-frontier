"""Reversible within-source excerpt dictionary; no variable masking or sampling."""
import re
from copy import deepcopy
from .semantic_contract import digest
from .review_context import serialize

SYSLOG = re.compile(r'^(?P<prefix>(?:<\d{1,3}>)?[A-Z][a-z]{2} +\d{1,2} \d\d:\d\d:\d\d (?P<host>\S+) (?P<program>[^\s:\[]+)(?:\[\d+\])?: )(?P<payload>[^\r\n]*)(?P<ending>\r?\n)?$')


def group(pack):
    """Keep every observation/locator; prefix includes exact raw timestamp/PID."""
    if pack.get('structured_log_groups'):
        return
    groups = {}
    for o in pack.get('observations', []):
        f = o['fields']; raw = f.get('excerpt')
        if not isinstance(raw, str) or f.get('excerpt_truncated') or '\ufffd' in raw:
            continue
        match = SYSLOG.fullmatch(raw)
        if (not match or not isinstance(f.get('source_sha256'),str) or not re.fullmatch(r'[a-fA-F0-9]{64}',f['source_sha256'])
            or not isinstance(f.get('artifact_path'),str) or not f['artifact_path']):
            continue
        scope = [o.get('evidence_id'), f.get('partition_offset'), f.get('os_instance'),
                 f['artifact_path'], f['source_sha256'], f.get('path'), match['host'], match['program'], match['payload']]
        key = digest(scope)
        groups.setdefault(key, []).append((o, match))
    original = deepcopy(pack)
    catalog = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        catalog.append({'id': key, 'payload': members[0][1]['payload'], 'observation_ids': [o['id'] for o, _ in members],
                        'record_count': len(members), 'source_complete': False})
        for o, match in members:
            o['fields'].pop('excerpt')
            o['fields']['structured_excerpt'] = {'group_id': key, 'prefix': match['prefix'], 'ending': match['ending'] or ''}
    if not catalog:
        return
    pack['structured_log_groups'] = catalog
    pack['structured_log_scope'] = 'Exact repeated payloads within one retained source. Reconstruct prefix + payload + ending. Every record identity remains. Not whole-source review or benign classification.'
    if len(serialize(pack)) >= len(serialize(original)):
        pack.clear(); pack.update(original)


def expand(pack):
    """Round-trip helper also used before last-resort context shortening."""
    catalog = {g['id']: g['payload'] for g in pack.get('structured_log_groups', [])}
    for o in pack.get('observations', []):
        ref = o['fields'].pop('structured_excerpt', None)
        if ref:
            o['fields']['excerpt'] = ref['prefix'] + catalog[ref['group_id']] + ref['ending']
    pack.pop('structured_log_groups', None)
    pack.pop('structured_log_scope', None)
