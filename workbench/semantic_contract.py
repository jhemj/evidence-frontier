"""Literal/source binding is mechanical; it does not establish semantic truth."""
import hashlib
import json

VERSION = 'source-bound-review-1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def scalar(observation, pointer, *, allow_long_text=False):
    if not isinstance(pointer, str) or not pointer.startswith('/fields/'):
        raise ValueError('Fact pointer must address a retained fields scalar')
    value = observation
    for part in pointer[1:].split('/'):
        import re
        if re.search(r'~(?![01])', part):
            raise ValueError('Invalid JSON pointer')
        key = part.replace('~1', '/').replace('~0', '~')
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and key.isdigit() and str(int(key)) == key and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ValueError('Fact pointer not found')
    if isinstance(value, (dict, list)) or (not (allow_long_text and isinstance(value, str))
            and len(json.dumps(value, ensure_ascii=False)) > 4096):
        raise ValueError('Fact must be a bounded scalar')
    return value


def assertion_errors(finding, observations, permitted):
    issues = []
    for fact in finding.get('fact_assertions', []):
        oid = fact.get('observation_id')
        try:
            if oid not in permitted or oid not in finding.get('observation_ids', []):
                raise ValueError('Fact outside finding citation scope')
            expected = fact.get('value')
            op = fact.get('operator', 'equals')
            # A short literal may be inside a long *presented* source field.
            # Bound the asserted text, not its haystack. This never retrieves
            # unpresented originals or relaxes finding/pointer citation scope.
            actual = scalar(observations[oid], fact.get('pointer'), allow_long_text=op == 'contains')
            if op == 'equals':
                valid = type(actual) is type(expected) and actual == expected
            elif op == 'contains':
                valid = (isinstance(actual, str) and isinstance(expected, str)
                         and 0 < len(expected) <= 4096 and expected in actual)
            else:
                valid = False
            if not valid:
                raise ValueError('Literal assertion does not match retained value')
        except (ValueError, KeyError, TypeError) as exc:
            issues.append({'code': 'source_literal_mismatch', 'observation_id': oid, 'detail': str(exc)})
    return issues


def source_facts(observations, limit=12):
    """Offer copyable, bounded literal facts, never facts inferred from prose."""
    rows = []
    for o in observations:
        for name in ('path', 'command', 'event_id', 'user', 'target_user', 'network_state', 'rule_id'):
            value = o['fields'].get(name)
            if type(value) not in (str, int, bool) or value == '' or len(str(value)) > 350:
                continue
            rows.append({'observation_id': o['id'], 'pointer': '/fields/' + name, 'operator': 'equals', 'value': value})
            if len(rows) >= limit:
                return rows
    return rows


def review_key(observations, evidence, runtime):
    return digest({'version': VERSION, 'evidence': [evidence['id'], evidence['signature']],
                   'runtime': runtime, 'observations': sorted(observations, key=lambda o: o['id'])})


def report_gate(document, findings):
    """Every current accepted dossier survives, including explicit exclusions."""
    owners = {d['id']: d for d in document.get('dossiers', []) if d.get('status') == 'reviewed' and d.get('finding')}
    active = {f.get('dossier_id'): f for f in findings}
    dispositions = []
    for did, d in owners.items():
        f = d['finding']
        excluded = f.get('timeline_role') in ('반증됨', 'coverage-only', '범위 설명') or not f.get('observation_ids')
        if not excluded and did not in active:
            raise ValueError('중요 판단이 최종 보고서에서 누락되었습니다: ' + did)
        if not excluded:
            rendered = active[did]
            for key in ('title', 'reason', 'observation_ids', 'counterevidence_ids', 'stages', 'fact_assertions', 'open_objections'):
                if rendered.get(key, [] if key not in ('title', 'reason') else '') != f.get(key, [] if key not in ('title', 'reason') else ''):
                    raise ValueError('최종 보고서의 판단·필수 근거가 검토 결과와 다릅니다: ' + did)
        dispositions.append({'dossier_id': did, 'disposition': 'excluded' if excluded else 'included',
                             'reason': f.get('reason', ''), 'finding_sha256': digest(f)})
    return {'version': VERSION, 'dispositions': dispositions, 'validated': True,
            'scope': 'Current accepted dossier inclusion and literal binding; not semantic truth or source completeness.'}
