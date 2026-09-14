# Evidence Frontier 구현 계획

기준: 2026-09-14 사용자 공유 대화, (private design consultation omitted)

## 제품 흐름
1. 사건 만들기: 이름, 조사 목적, 조사 프로파일 선택.
2. 증거 등록: 전용 읽기 전용 폴더의 파일을 선택. 대형 E01은 브라우저 업로드/복사하지 않는다.
3. 조사: 한 화면에서 질문, 실행/일시정지, 현재 단계, 근거와 공백 확인.
4. 보고서: 근거가 연결된 Claim만 분석가가 승인. 고정 템플릿으로 HTML/JSON/manifest/checksum을 생성.

## 구조
- 독립 로컬 웹 UI: 채팅 중심, 조사/근거/보고서 3개 화면. OpenRelik 원본 UI는 고급 작업용으로 유지한다. 사용자 요청의 간결한 UX를 위해 UI 전체를 포크하는 대신 얇은 독립 UI를 둔다.
- FastAPI Controller + SQLite: 사건, 유한 Epoch, Coverage, Frontier, immutable Observation, Claim, model/tool receipt, report snapshot.
- 제한형 OpenRelik Gateway: 서버 설정에 등록된 template ID와 사건에 귀속된 file ID만 허용. LLM은 arbitrary workflow/shell/path를 사용할 수 없다.
- 로컬 worker: SHA-256, E01 segment 검사/ewfverify, TSK 조사, 표준 NDJSON 관측 수입. 컨테이너에서 증거 읽기 전용, 데이터 저장소 분리.
- Ollama native/OpenAI-compatible: 모델 목록 확인, 짧고 분리된 증거 묶음, structured JSON, timeout/output budget. 모델명은 실제 provider 목록에서 지정.
- Claim gate: 근거 ID closure, covered_zero만 부재 주장 가능, 반증 검토와 분석가 승인 분리.
- Reports: 버전별 JSON/HTML/manifest/checksum. 사용자 템플릿은 templates/report.html 교체와 계약 유지로 반영.

## 구현 순서 및 검증
1. 로컬 실행 가능한 수직 흐름 + 예제 증거 + 지속 저장.
2. 결정적 큐/중복 차단/유한 실행/오류 복구/조사 공백.
3. 실제 도구 adapter, provider adapter, OpenRelik API adapter.
4. 근거 기반 후보 Claim/반증/보고서.
5. lockfile, Docker Compose, CI, GHCR release workflow, 설치/운영/제약 문서.
6. 합성 증거로 controller/worker/report 경로와 보안 경계 테스트. Docker 이미지 빌드 및 HTTP smoke test.

## 승인 기준
- 모델이 완료를 선언하거나 도구 명령을 직접 실행할 수 없다.
- 미지원, 실패, 차단을 정상 조사 또는 미발견으로 집계하지 않는다.
- 중단/재시작 이후 미완료 작업과 receipt가 보존된다.
- 실제 E01 및 연결된 모델/OpenRelik 검증이 없으면 그 상태를 명시한다.
- 대형 이미지의 전체 아티팩트 조사 품질은 golden image와 실제 worker 조합으로 별도 검증한다.
