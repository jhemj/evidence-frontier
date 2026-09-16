# Frontier

**로컬 AI 포렌식 워크벤치 · Windows / Ubuntu**

증거를 연결하고 **침해조사 시작**을 누르면 Linux 로그·계정·설정·파일 내용을 수집하고, 로컬 AI가 추가 도구를 선택해 조사합니다. LangGraph가 계획·실행·대조검사·결과 저장을 이어갑니다. 질문 입력은 선택 사항입니다. [설계와 지원 범위](docs/LANGGRAPH_DECISION.md)를 확인하세요.

조사 화면의 **증거 타임라인**에서 핵심 근거와 확인·유력·미확인 판단을 시간순으로 볼 수 있습니다. AI가 반증으로 해석을 철회하면 변경 이유와 이력을 남깁니다. 원문 스트리밍 탐지, 단서별 검토와 실제 후속 검사 범위는 [탐지·타임라인 구현 계약](docs/HUNTING_AND_TIMELINE.md)을 참고하세요.

**현재 버전: 0.1.0 개발 프리뷰.** 실행 가능한 첫 구현입니다. 대형 실제 사건용 전체 Windows/Linux 아티팩트 제품이 완성되었다는 의미는 아닙니다. 상세 범위는 [구현 현황](docs/STATUS.md)에 있습니다.

상단 진행 카드에는 **경과시간, 현재 단계, 단서 처리 막대, 검토 완료·실패·보류·남은 단서**가 표시됩니다. ETA는 충분한 처리 이력이 쌓인 뒤 현재 AI 판단 단계의 평균 처리 속도로 계산하며, 자료 수집과 보고서 저장을 포함한 전체 종료 시각은 아닙니다. 처리율은 작업 큐 기준이며 침해 탐지율이나 분석 완결성을 뜻하지 않습니다.

## 빠른 시작

Windows 11 + Docker Desktop(WSL2, Linux containers), 또는 Ubuntu 22.04/24.04 + Docker Engine/Compose v2가 필요합니다. 웹 UI를 포함한 모든 애플리케이션 의존성은 이미지에 들어갑니다. 최초 빌드는 인터넷이 필요합니다.

Git으로 저장소를 내려받습니다. GitHub의 **Code → Download ZIP**을 사용한다면 압축을 풀고 해당 폴더에서 설치 명령을 실행해도 됩니다.

```bash
git clone https://github.com/jhemj/evidence-frontier.git
cd evidence-frontier
```

Windows PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/setup.ps1
```

Ubuntu:

```bash
bash scripts/setup.sh
```

접속: **http://localhost:8765**

설치 스크립트는 `.env.example`을 바탕으로 로컬 `.env`와 작업자 통신용 임의 `WORKER_TOKEN`을 생성합니다. UI 접속 암호는 기본값이 비어 있으며, `.env`에 `WORKBENCH_TOKEN`을 설정한 경우 그 값을 입력합니다. 이 파일과 토큰은 공유하지 마세요. 로컬 AI 서버와 모델 가중치는 별도로 준비해야 합니다.

첫 화면의 **예제 증거로 흐름 살펴보기**를 누르면 합성 사건을 생성하고 실제 파일 해시·NDJSON 수입·타임라인·경로 연관 조사를 수행합니다. AI가 연결되지 않은 상태는 명확하게 표시합니다.

Windows 스크립트의 실행 정책 옵션은 해당 프로세스에만 적용됩니다. 시스템 실행 정책을 변경하지 않습니다. 회사 정책에서 스크립트를 금지하면 관리 정책에 따르세요.

## 실제 증거 연결

생성된 `.env`의 `EVIDENCE_PATH`를 전용 증거 폴더로 설정한 후 `docker compose up -d`를 실행합니다.

```dotenv
# Windows: forward slash 사용. 공백이 있으면 따옴표로 감쌉니다.
EVIDENCE_PATH="D:/Forensic Evidence"
# Ubuntu 예: EVIDENCE_PATH=/mnt/evidence
```

UI의 새 사건 → 증거 ＋ → 파일 선택 → 조사 시작. E01 분할 이미지는 같은 폴더에 모두 두고 첫 번째 `.E01`을 선택합니다. 이미지 전체를 웹으로 업로드하거나 복제하지 않습니다. 모든 segment를 읽을 수 있어야 합니다. Ubuntu에서 worker UID 10001에 파일 읽기와 상위 폴더 탐색 권한이 필요합니다.

증거 카드의 **연결 해제**와 **다시 연결**로 조사 대상을 관리할 수 있습니다. 원본과 과거 기록은 보존하며 새 분석과 보고서에서만 제외합니다. 진행 중인 읽기 작업은 완료 후 해제할 수 있습니다. [연결 동작과 대용량 XFS 조사 범위](docs/EVIDENCE-CONNECTION.md)를 확인하세요.

원본 폴더는 worker만 `read_only`로 마운트하며 controller에는 마운트하지 않습니다. Windows/Ubuntu 공통으로 사건 DB는 Docker named volume에 저장해 SQLite/NTFS 경로 차이를 피합니다. 이는 원본 저장 매체에 대한 하드웨어 쓰기 방지나 chain-of-custody 절차를 대신하지 않습니다.

## AI 연결

왼쪽 **AI 연결 설정**에서 다음 중 하나를 지정합니다.

- 호스트 Ollama: `http://host.docker.internal:11434`
- 원격 사설망 Ollama: 실제 사설망 URL과 전송 동의 선택
- vLLM 등 호환 API: `http://host.docker.internal:8000/v1`

**연결 및 모델 확인**을 누른 후 설치된 모델 이름을 선택합니다. 모델 파일은 배포물에 포함하지 않으며 자동으로 대용량 모델을 다운로드하지 않습니다. 공유 지시서의 모델 후보는 실제 설치명과 별개이므로 검증되지 않은 태그를 기본값으로 지정하지 않았습니다.

대형 E01의 작업자 메모리 기본값은 4 GiB입니다. `.env`의 `WORKER_MEMORY`로 조정할 수 있습니다. 조사 결과와 추출 원문은 별도 Docker volume에 저장됩니다.

큰 근거 팩의 로컬 AI 응답은 수 분 걸릴 수 있습니다. `.env`의 `MODEL_TIMEOUT`은 기본 300초이며 30~600초로 제한합니다. 단서별 모델 오류는 최대 2회 시도하며 실패·한도 중단을 원장에 남깁니다.

반증 모델을 비워두면 동일 모델을 분리된 문맥으로 사용합니다. 이를 독립 도구 검증이라고 표시하지 않습니다. 로컬 서버가 API 키를 요구하면 `.env`의 `MODEL_API_KEY`를 사용합니다. 공개 외부 모델 URL은 기본적으로 차단합니다.

Windows Docker Desktop은 호스트 별칭을 지원하며 Ubuntu Compose에는 `host-gateway`를 포함했습니다. 호스트 Ollama가 loopback에만 바인딩되어 있으면 컨테이너 접근이 되지 않을 수 있습니다. 신뢰한 Docker 인터페이스에만 모델 서비스를 노출하거나 [로컬 Ollama 구성](docs/DEPLOYMENT.md)을 사용하세요.

## 보고서

조사가 끝나면 AI가 중간 해석을 종합해 **확인 / 유력 / 미확인**으로 판단하고 결과와 보고서를 자동 생성합니다. 사용자의 승인·제외 절차는 필요 없습니다. 확인은 원문이 직접 뒷받침하는 구체적 사실, 유력은 정황상 가장 타당한 설명, 미확인은 자료 부족 또는 상충 상태입니다. 각 판단의 이유·근거·대안 설명·남은 검사를 함께 보존하며 유력한 결과도 보고서에 포함합니다.

기본 ZIP: `report.html`, `report.json`, `manifest.json`, `SHA256SUMS`. Linux 내용 조사에는 `TIMELINE.csv`, `IOC_LIST.csv`, `HYPOTHESES.json`, `EVIDENCE_MANIFEST.csv`, `REPORT_EVIDENCE_MAP.csv`, `RESULT_REVISION.json`과 해시 검증한 추출 원문을 추가합니다. 외부 JS·폰트 없이 오프라인으로 열 수 있습니다. 사용자 양식은 `templates/report.html`로 교체하며 데이터 계약은 [보고서 계약](docs/REPORT_CONTRACT.md)에 있습니다.

공개 저장소에는 프로그램과 합성 예제, 일반적인 사용 문서를 포함합니다. 실제 증거, 사건별 결과 ZIP, 상세 분석·검토 보고서, 로컬 설정과 API 키는 게시하지 않습니다. `artifacts/`, `data/`, `evidence/`, `reports/`, `.env`는 Git 추적에서 제외됩니다.

## 종료와 업데이트

서비스를 멈추거나 다시 실행할 때는 다음 명령을 사용합니다. 조사 중에는 먼저 UI에서 일시정지를 요청하고 현재 작업이 마무리됐는지 확인하세요.

```bash
docker compose stop
docker compose start
```

업데이트는 진행 중인 조사가 종료된 뒤 결과를 보존하고 수행합니다.

```bash
git pull --ff-only origin main
docker compose up --build -d --wait
```

사건별 코드·모델 조합을 기록하므로 업데이트 후 과거 사건의 이어가기가 제한될 수 있습니다. DB의 실행 버전 기록을 수정해 우회하지 마세요. Docker volume에 사건 데이터가 있으므로 보존하려면 `docker compose down -v`를 사용하지 않습니다. 별도 이미지 고정 구성을 쓰는 운영 환경은 [배포 안내](docs/DEPLOYMENT.md)를 따릅니다.

## OpenRelik

OpenRelik을 별도 실행 엔진으로 연결하는 제한형 API gateway와 공식 0.7.0 배포 구성을 포함합니다. 서버 본체는 포크하지 않았습니다. 간결한 기본 UI는 독립 구현하고, 고급 workflow 관리는 upstream UI를 유지합니다. 설치·템플릿 검토·실행은 [OpenRelik 연동](docs/OPENRELIK.md)을 참고하세요.

현재 native controller/worker는 별도로 실행됩니다. OpenRelik workflow 성공을 곧바로 Coverage 성공으로 바꾸지 않습니다. 외부 도구 출력은 지원되는 정규화 형식과 범위 계약을 거쳐야 합니다.

## 개발 및 검증

Python 3.12와 [uv](https://docs.astral.sh/uv/)가 있는 개발 환경:

```bash
uv sync --frozen
uv run pytest -q
uv run uvicorn workbench.api:app --host 127.0.0.1 --port 8765
```

네이티브 개발 서버의 기본 증거 폴더는 `examples/`입니다. E01/TSK 실행은 Docker worker를 권장합니다. Python 의존성은 `uv.lock`, 컨테이너 설치는 해시 검증을 포함한 `requirements*.lock`으로 고정했습니다. UI는 정적 HTML/CSS/JS라 Node 런타임·npm 설치가 필요 없습니다.

```bash
docker compose build
docker compose up -d --wait
uv run python scripts/smoke.py
```

`smoke.py`는 합성 사건을 하나 생성합니다. 실제 사용자 사건을 수정하지 않습니다. Windows/Ubuntu CI 테스트와 Linux 컨테이너 검증은 `.github/workflows/ci.yml`에 있습니다.

실제 로컬 모델과 합성 E01의 자동 조사 재현은 [침해조사 E2E](docs/INVESTIGATION_TESTING.md), 제공된 실제 이미지의 내용 조사 결과와 한계는 [검증 기록](docs/E2E-INVESTIGATION-2026-09-14.md)에 있습니다.

## 저장소 구조

```text
dist/                  간결한 로컬 웹 UI
workbench/             controller, DB, provider, gateway, native workers
profiles/              결정적 조사 범위
recipes/               증거 기반 후속 조사 규칙
templates/             교체 가능한 보고서 양식
config/                승인된 외부 workflow 설정
vendor/openrelik/      upstream 배포 구성 + 출처 + 라이선스
scripts/               Windows/Ubuntu 설치, smoke, 배포 준비
tests/                 근거·범위·복구·API 계약 검증
docs/                  구현 계획, 현황, 운영, 보고서 계약
compose.yaml           공통 실행 구성
Dockerfile             controller / worker 이미지
```

GitHub 게시와 GHCR 릴리스, 백업·이동 방법은 [배포 안내](docs/DEPLOYMENT.md)를 참고하세요. 소스 라이선스는 MIT이며 별도 도구 및 upstream 코드는 각각의 라이선스를 유지합니다. [의존성/라이선스](docs/DEPENDENCIES.md)

## 최신 조사 구조

원문 범위 조회·검색 이어보기, 독립 원문 검토와 후속 반증, 단계별 확인/유력/미확인, 자료 처리 범위와 검토 상태 분리, 합성 평가 기준을 추가했습니다. [변경 내용과 검증 범위](docs/INVESTIGATION_UPGRADE.md)를 확인하세요. 새 실이미지 E2E는 완료 전까지 통과로 표기하지 않습니다.
