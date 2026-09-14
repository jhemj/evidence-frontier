# Windows / Ubuntu / GitHub 배포

## 공통 실행

Docker Engine은 Linux 컨테이너를 실행해야 합니다. Windows에서는 Docker Desktop WSL2 backend를 사용합니다. WSL 내부에 소스를 둘 수도 있고 Windows 경로에서 PowerShell로 실행할 수도 있습니다. 원본 폴더의 경로는 실제 호스트에 맞추세요. PostgreSQL/SQLite는 named volume에 저장합니다.

기본 포트는 `127.0.0.1:8765`입니다. 누구나 **자기 PC에 설치해서** 사용할 수 있는 배포 형태입니다. 민감한 증거를 공유하는 공용 멀티테넌트 SaaS는 구현 범위가 아닙니다. 외부 접근은 별도 인증·TLS와 접근 정책을 설계한 후 구성해야 합니다. GitHub Pages는 Python/worker를 실행하지 않으므로 전체 앱 배포 대상으로 사용할 수 없습니다.

## 로컬 Ollama 컨테이너 (선택)

```bash
docker compose -f compose.yaml -f compose.ollama.yaml --profile local-ai up -d
docker compose -f compose.yaml -f compose.ollama.yaml exec ollama ollama pull YOUR_VERIFIED_MODEL
```

모델 이름을 실제 원하는 모델 태그로 바꾸세요. 앱 설정 URL은 `http://ollama:11434`, 사설망 전송 동의를 사용합니다. GPU 가속은 NVIDIA Container Toolkit 및 호스트 장치 설정에 따라 별도 Compose override로 지정합니다. CPU 실행도 가능하지만 대형 모델은 매우 느릴 수 있습니다. 모델 이미지 태그는 배포 전에 `scripts/pin_images.py`로 digest를 고정하세요.

## GitHub / GHCR

현재 작업에서는 GitHub 원격 저장소를 만들거나 소스를 공개하지 않았습니다. 원하는 저장소를 만든 뒤 이 소스를 push하면 CI가 Windows/Ubuntu 테스트와 Docker 빌드를 수행합니다.

`v0.1.0` 같은 태그를 push하면 `release.yml`이 아래 두 이미지를 GHCR에 생성합니다.

```text
ghcr.io/<owner>/<repo>-controller:v0.1.0
ghcr.io/<owner>/<repo>-worker:v0.1.0
```

패키지 공개 설정은 GitHub 계정에서 관리합니다. secret을 소스에 넣지 마세요. 릴리스 이미지에는 SBOM과 provenance를 붙입니다. 고객 환경에서는 생성된 digest로 고정한 image override를 사용하고, 이미 빌드된 이미지는 `docker compose up --no-build`로 실행할 수 있습니다. registry 인증이 필요한 private 패키지는 사용자가 로그인해야 합니다.

배포 전 점검은 `uv run pytest -q`, `docker compose build`, `docker compose up -d --wait`, `python scripts/smoke.py`입니다. 일반 CI fixture와 실제 사건 데이터는 분리합니다.

## 중지 / 재실행

```bash
docker compose stop
docker compose start
docker compose logs --tail 100
```

일시정지는 현재 도구가 반환한 후 다음 작업 실행을 멈춥니다. 실행 중 서버가 강제로 종료되면 해당 작업은 재시작 시 실패로 남으며 분석가가 재시도를 선택합니다. `docker compose down`은 컨테이너를 없애지만 기본적으로 named volume은 유지합니다. `down -v`는 사건 저장소까지 삭제하므로 보존할 데이터가 있으면 사용하지 마세요.

## 백업 / PC 이동

사건 처리를 중지한 상태에서 named volume `evidence-frontier_case-data`를 백업합니다. 이 안에는 DB, 도구 영수증, 보고서가 있습니다. Docker Desktop의 volume export 또는 조직의 volume 백업 도구를 사용할 수 있습니다. 원본 증거 폴더와 `.env`, 별도 OpenRelik runtime은 별도로 백업합니다. 보고서 ZIP만으로 실행 상태를 복구할 수는 없습니다.

다른 PC에서 같은 버전 이미지를 실행하고 volume과 원본 폴더를 복원합니다. 등록 시 원본 size/mtime가 고정되므로 복원 방식이 mtime를 바꾸면 재사용이 거부됩니다. 원본 메타데이터를 보존하거나 새 사건으로 등록·재검증하세요.

## 오프라인 실행

네트워크가 있는 빌드 환경에서 controller/worker 이미지와 필요한 모델/선택 OpenRelik 이미지를 준비합니다. `docker save`로 export하고 대상 PC에서 `docker load`로 import한 후 실행합니다. 앱 정적 UI에는 외부 CDN/폰트/추적 코드가 없습니다. 기본 worker는 인터넷을 사용하지 않습니다.
