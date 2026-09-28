"""Record failures and iterator failures have different, explicit denominators."""
import hashlib
import json

VERSION = 'parser-degradation-1'


def consume(records, parse, limit=50000, retain_failure=None):
    rows, failures = [], []
    seen = degraded = 0
    exhausted = False
    iterator = iter(records)
    while seen < limit:
        try:
            raw = next(iterator)
        except StopIteration:
            exhausted = True
            break
        except Exception as ex:
            failures.append({'scope': 'iterator', 'after_records': seen,
                             'error': f'{type(ex).__name__}: {ex}'[:600]})
            break  # a closed generator cannot be safely resumed at an invented offset
        seen += 1
        try:
            row = parse(raw, seen)
            if row is not None:
                rows.append(row)
                degraded += bool(row.get('fields', {}).get('parser_degraded'))
        except Exception as ex:
            encoded = json.dumps(raw, ensure_ascii=False, default=str).encode()
            failure = {'scope': 'record', 'ordinal': seen, 'raw_sha256': hashlib.sha256(encoded).hexdigest(),
                       'error': f'{type(ex).__name__}: {ex}'[:600]}
            if retain_failure:
                failure['raw_locator'] = retain_failure(encoded, seen)
            failures.append(failure)
    # Reaching a limit is deliberately partial, even if the next record might be EOF.
    partial = bool(failures) or degraded > 0 or not exhausted
    return {'rows': rows, 'failures': failures, 'records_seen': seen, 'parsed_records': len(rows),
            'failed_records': sum(f['scope'] == 'record' for f in failures),
            'degraded_records': degraded, 'enumeration_complete': exhausted,
            'unread_records': None if not exhausted else 0, 'limit_reached': seen >= limit and not exhausted,
            'status': 'partial' if partial else 'covered' if rows else 'covered_zero',
            'complete': not partial, 'absence_is_refutation': False, 'version': VERSION}
