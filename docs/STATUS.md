# 0.1.0 구현 현황

## 구현하고 실행 검증한 범위

- 사건 생성, 전용 폴더 증거 등록, 조사 시작/일시정지/재시도.
- 증거 연결 해제·재연결, 기존 기록 보존 및 새 분석·보고서 대상 제외.
- SQLite 영속 저장, 불변 관측/도구 영수증/보고서, 감사 기록.
- LangGraph + SQLite checkpoint, 단계별 재개, 영속 worker job/attempt, 중복 결과 방지, 예산 유지, 실행 불명의 자동 재실행 차단.
- E01 numeric segment 순서/누락 확인, 각 segment SHA-256, ewfverify.
- TSK mmls 파티션 조사, fls 파일 목록 및 파일시스템 시간 이벤트 adapter.
- Dissect 독립 파티션 offset/size 비교. 파일시스템 전체·아티팩트 의미의 교차 검증으로 확대 해석하지 않음.
- XFS/ext Linux 내용 수집: 로그·계정·cron·history·audit·wtmp/btmp·ELF 정적 정보. 원문 해시·행/바이트 위치·수집 제외 범위를 보존.
- 필수 설정/호출/명령 대조와 native audit 사건 묶음. 로컬 AI가 검색·원문 재읽기·file/readelf·TAR 목록 도구를 추가 선택.
- 주요 후보 최대 5건에 실제 원문 대조·검색 후 경쟁 설명 검토. 미완료 검사는 보존하고 최종 AI 판단 단계로 전달.
- 마지막 AI 호출은 완료된 도구 결과 정리에 예약. 모델의 추가 제안이 실행되지 않았으면 미실행 항목으로 남김.
- NDJSON/JSONL 관측 수입, 시간대 검증, UTC 정규화, 타임라인.
- 동일 관측 중복 제거 + 각 도구 receipt 연결 보존.
- 같은 경로의 관측을 연결하는 결정적 recipe 1개와 후보 가설 기록.
- Ollama native / OpenAI-compatible 설정·모델 목록·bounded structured output adapter.
- 좁은 evidence pack 기반 중간 해석, 별도 문맥 반증, 근거 ID 검증.
- AI 최종 판단 단계가 확인·유력·미확인 결과를 자동 정리. 사용자 승인·제외 절차 없이 보고서에 포함하며 이유·대안·남은 검사를 보존.
- HTML/JSON/manifest/checksum ZIP 보고서와 템플릿 교체 지점.
- 원본 read-only worker, 원본 미마운트 controller, worker 외부망 차단, 인증 토큰, shell-free 도구 실행, 자원 한도.
- OpenRelik 제한형 template 제출/상태 adapter, 승인 template hash 검증, 원본 SHA-256/사건 소속 검증.
- Windows/Ubuntu 설치 스크립트, lockfiles, Docker, CI, GHCR 릴리스 정의.

## 검증 결과

현재 릴리스의 확인 범위는 [조사 고도화](INVESTIGATION_UPGRADE.md)와 [검증 방법](INVESTIGATION_TESTING.md)을 참조하세요. 개별 사건 기록은 공개 저장소에서 제외합니다.

## 지시서 전체 대비 남은 범위

이번 프리뷰는 **전체 아티팩트 자동 포렌식의 완성판이 아닙니다**.

- 이번 약 300GB 이미지 외 다양한 실이미지의 성능/저장공간/손상/암호화/비표준 EWF segment 검증.
- OS·볼륨·사용자별 전체 EVTX/Registry/Prefetch/Amcache/브라우저 등 세부 Coverage 생성 및 각 독립 파서 조합.
- Plaso/Hayabusa/YARA/capa/PhotoRec 등 전체 OpenRelik workflow의 실제 환경 통합 및 결과 범위 증명 normalizer.
- 임의 신규 아티팩트 발견에 대한 전체 recipe 묶음, 분산 job lease, 다중 worker 병렬 조사, 모델 역할 풀/예산 공유.
- 다양한 forensic golden-case의 탐지율·오탐률·자연어 해석 품질. 이번 합성 사건의 구조화 불변식과 근거 연결 검사는 일반 사건 정확도를 입증하지 않음.
- Ubuntu 호스트에서의 실기동. 현재 검증은 Windows Docker Desktop 위 Linux 컨테이너이며 Ubuntu 설치 스크립트와 동일 Compose를 제공함.
- 전체 호스트 인터넷 차단 상태의 로컬 AI E2E. worker 외부 통신 차단과 로컬 GPU 실행은 확인했으나 호스트 전체 오프라인은 별도 검증 필요.
- OpenRelik 전체 서버/DB/worker 스택의 실제 기동·원본 ID 등록·API 키·실 workflow 실행. 현재는 계약 테스트와 Compose 정합성 확인.
- 사용자 최종 보고서 양식.

표준 조사라는 UI 프로파일 명칭은 현재 등록된 기본 셀에 대한 범위입니다. 완료 표시가 전체 디스크의 모든 가능한 증거 검토 완료를 뜻하지 않습니다. 미지원/차단/실패는 조사 한계로 남습니다. 대규모 타임라인은 현재 SQLite/JSON 기반이며 DuckDB/Parquet 인덱스는 아직 포함하지 않았습니다.
