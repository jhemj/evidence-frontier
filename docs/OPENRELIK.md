# OpenRelik 연동

기본 워크벤치 실행 후 추가하는 선택형 실행 엔진입니다. 별도 OpenRelik 인스턴스가 이미 있다면 새로 설치할 필요가 없습니다.

## 새 인스턴스 준비

```bash
uv run python scripts/prepare_openrelik.py
docker compose --project-directory vendor/openrelik/runtime -f vendor/openrelik/runtime/compose.yaml -f vendor/openrelik/runtime/compose.pinned.yaml up -d
```

도구는 출처가 고정된 upstream Compose에서 별도 runtime을 만들고 로컬 secret을 생성합니다. 기존 runtime이 있으면 덮어쓰지 않습니다. 이미지 고정은 registry metadata 조회만 수행합니다. 최초 실행은 이미지 용량과 메모리를 충분히 확보하세요. 기본 upstream에는 서버, UI, mediator, PostgreSQL, Redis, strings/grep/Plaso/extraction worker가 들어 있습니다.

DB migration과 사용자 생성은 upstream 지침에 따라 처리합니다. 이 프로젝트의 기본 설치 스크립트는 OpenRelik 계정을 자동 생성하지 않습니다.

```bash
docker compose --project-directory vendor/openrelik/runtime exec -w /app/openrelik/datastores/sql openrelik-server alembic upgrade head
docker compose --project-directory vendor/openrelik/runtime exec openrelik-server python admin.py create-user
```

UI: http://localhost:8711, 서버: http://localhost:8710. CLI가 이름·암호를 요구하면 직접 입력하세요. 실행 서버 버전에 따라 설정·명령 계약을 재확인해야 합니다.

## Gateway 연결

1. OpenRelik에서 사건 폴더와 원본을 등록하고 API access token을 생성합니다. gateway는 파일의 SHA-256이 없으면 제출을 거부하므로 서버 측 해시 기록을 준비하세요.
2. `.env`에 `OPENRELIK_URL=http://host.docker.internal:8710/api/v1`, `OPENRELIK_TOKEN=...`을 넣습니다. Docker 호스트 loopback의 접근 가능 여부에 따라 private Docker network 연결을 사용하세요.
3. OpenRelik UI에서 **검토한 고정 Workflow Template**을 만듭니다.
4. 환경변수에 동일한 URL/token을 지정하고 아래 도구로 해당 spec을 직접 검토·등록합니다.

```bash
uv run python scripts/approve_template.py filesystem_inventory 7
```

`7`은 실제 template ID로 바꿉니다. 이 명령은 실행하지 않고 spec hash만 저장합니다. `config/openrelik-templates.json`은 로컬에서만 유지하며 container의 `/app/config`에 읽기 전용 마운트됩니다.

5. 로컬 사건에서 증거를 API로 등록할 때 `openrelik_file_id`를 연결합니다. 동일 사건에 속한 로컬 증거의 원본 SHA-256과 OpenRelik 파일 해시/폴더 소속이 일치해야 합니다.

```json
POST /api/cases/{case_id}/evidence
{"path":"disk.E01","openrelik_file_id":31}

POST /api/cases/{case_id}/openrelik
{"capability":"filesystem_inventory","folder_id":4,"evidence_ids":["EVIDENCE-..."]}

POST /api/openrelik/{remote_job_id}/refresh
```

API 쓰기 요청은 `X-Requested-With: frontier`와 설정된 경우 `X-Workbench-Token`이 필요합니다. capability는 등록한 이름만 허용합니다. gateway는 임의 shell/spec/parameter/path 요청을 받지 않습니다. 제출 도중 연결이 끊기면 자동 재제출하지 않고 `needs_review`로 남겨 중복 실행을 막습니다.

## 현재 계약 범위

OpenRelik workflow 실행·status는 연동되어 있지만 그 성공 상태를 본체 Coverage의 `covered`로 자동 간주하지 않습니다. 외부 worker가 `SUCCESS`여도 파서 미지원, 일부 입력 실패, truncation 등이 있을 수 있기 때문입니다. 0.1.0에서는 검증된 NDJSON/JSONL 관측을 별도 증거로 등록할 수 있습니다. task/output file lineage와 범위 영수증을 자동 전달하는 전체 normalizer는 후속 구현 범위입니다.

공식 ADK agent나 무제한 MCP는 자동으로 활성화하지 않습니다. 카빙·YARA·capa를 연결하려면 해당 worker 이미지/라이선스/입력권한을 검토한 고정 template를 추가하세요.
