"""Describe exactly what a successful integrity operation did and did not test."""
import re


def assess(verification='', reused=False):
    digests = {}
    for algorithm, width in (('MD5', 32), ('SHA256', 64)):
        stored = re.search(r'^' + algorithm + r' hash stored in file:\s*([^\r\n]+)', verification, re.M)
        calculated = re.search(r'^' + algorithm + r' hash calculated over data:\s*([^\r\n]+)', verification, re.M)
        clean = lambda match: match.group(1).strip().lower() if match and re.fullmatch(r'[a-fA-F0-9]{'+str(width)+'}', match.group(1).strip()) else None
        expected, actual = clean(stored), clean(calculated)
        digests[algorithm.lower()] = {'embedded': expected, 'calculated': actual,
            'comparison': 'not_available' if not expected or not actual else 'matched' if expected == actual else 'mismatch'}
    return {'physical_segment_hashes_fresh': True, 'logical_verification_reused': reused,
        'logical_digests': digests,
        'external_acquisition_hash_compared': False,
        'scope_note': ('이번 입력 바이트의 SHA-256을 계산했습니다. ' +
            ('EWF 논리 데이터 검증은 동일 세그먼트 해시의 이전 성공 기록을 재사용했습니다. ' if reused else
             'EWF 논리 데이터를 모두 읽고 검증 해시를 계산했습니다. ' if verification else '') +
            '별도 제공된 취득 당시 해시와의 대조는 수행하지 않았습니다. 내부 저장 해시가 없는 경우 내부 해시 일치 검증도 아닙니다.')}
