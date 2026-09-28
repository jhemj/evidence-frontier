"""Case-scoped platform selection; existing cases retain their Linux behavior."""
WINDOWS_VERSION = 'windows-hunt-1'
WINDOWS_EXTENSIONS = ('.e01','.raw','.dd','.img','.vhd','.vhdx','.evtx','.zip','.ndjson','.jsonl')


def target_os(case):
    value = case.get('target_os', 'linux')
    if value not in ('linux', 'windows'):
        raise ValueError('Unsupported investigation OS')
    return value


WINDOWS_HYPOTHESES = [
    ('최초 유입 경로', ['windows_event', 'windows_prior_interpretation'], '정상 설치·업무 배포·복사', '최초 파일시각은 최초 감염 시각이 아님'),
    ('계정과 관리자 세션 오용', ['windows_event', 'windows_process'], '승인된 관리자 작업', 'SID·PID·시간 근접성만으로 행위자 귀속 불가'),
    ('프로세스와 PowerShell 실행', ['windows_process', 'windows_powershell_start', 'windows_srum_application'], '정상 스크립트·보안 점검', '엔진 시작과 명령 완료는 별도'),
    ('의심 파일의 정체와 복사 경로', ['windows_file', 'windows_task'], '서명된 도구의 정상 배치·복사', '정상 해시는 경로별 정상 행위 보증이 아님'),
    ('예약작업과 지속성', ['windows_task', 'windows_registry', 'windows_powershell_start'], '정상 업데이트·마이그레이션', '등록·설정·실행을 분리; 삭제 작업 복구 미지원'),
    ('네트워크 시도와 성공', ['windows_network', 'windows_srum_network', 'windows_scriptblock'], '설정만 존재·명시 실패·정상 통신', '보존기간 밖 결과는 미상; SRUM은 목적지 증거가 아님'),
    ('방어회피와 로그 변경', ['windows_registry', 'windows_event'], '정상 보존·회전·백신 재설치', '현재 예외 값과 설정자·생성 시각은 별도'),
    ('수평이동과 원격 접근', ['windows_event', 'windows_network'], '정상 내부 관리·원격 지원', '상대 시스템 원본과 행위자 확인 자료 미제공'),
    ('자료 수집과 유출', ['windows_process', 'windows_network', 'windows_srum_network'], '백업·사후 수집', '명령·바이트 계수는 목적지와 유출 성공 증명이 아님'),
    ('정상 운영과 사후 대응', ['windows_prior_interpretation', 'windows_event', 'windows_file'], '침해 흔적의 정상 도구 위장', '사후 대응 태그는 근거·cutover 필요; 시각만으로 귀속 불가'),
]
