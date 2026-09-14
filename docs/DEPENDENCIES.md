# 의존성과 출처

## 애플리케이션

Python 3.12 base image는 Dockerfile에 registry digest 고정. Python 런타임 패키지는 uv.lock 및 hash-enforced requirements.lock/requirements-worker.lock으로 고정됩니다. FastAPI, Uvicorn, HTTPX, Pydantic, Jinja2, PyYAML, Dissect와 전이 의존성을 포함합니다. UI는 외부 패키지 없이 브라우저 표준 기능으로 실행됩니다.

## 포렌식 worker

- libewf `ewf-tools`: Debian bookworm 패키지. SHA-256과 EWF 내부 검증을 별개로 기록합니다.
- The Sleuth Kit `sleuthkit`: 파티션/파일 메타데이터 분석. 실제 설치 버전을 도구 receipt에 기록합니다.
- `dissect.target` 및 전이 라이브러리: requirements-worker.lock. 현재 교차 검증 범위는 파티션 geometry입니다.
- [`dissect.xfs`](https://github.com/fox-it/dissect.xfs) 3.13: XFS 파일 메타데이터의 제한된 읽기. 버전과 배포 파일 해시를 잠금 파일에 포함합니다. 범위와 한계는 [증거 연결 문서](EVIDENCE-CONNECTION.md)를 참고하세요.
- optional upstream OpenRelik worker 이미지는 각각 별도 라이선스를 유지합니다.
- `yara-python` 4.5.4 및 `pyelftools` 0.33: 전체 파일 YARA 패턴 검사와 ELF 구조·심볼 분석. worker 잠금 파일에 해시를 포함합니다.
- Debian `rpm`: 이미지에서 복사한 패키지 메타데이터를 읽고 수집 파일의 해시와 대조합니다. 원본 데이터베이스를 수정하지 않습니다.

Debian 패키지는 기본 이미지 digest만으로 모든 future apt 의존성이 고정되는 것은 아닙니다. 최종 배포는 빌드된 worker **image digest**를 고정하고 SBOM을 함께 보존하세요. GHCR release workflow가 이미지 SBOM/provenance를 생성합니다.

도구 라이선스는 앱의 MIT 라이선스로 바뀌지 않습니다. libewf의 LGPL, TSK 구성 파일별 라이선스, Dissect AGPL 등 도구별 재배포 의무를 적용해야 합니다. 설치된 배포물의 license/copyright와 대응 소스를 함께 확인하고 보존하세요. binary image 공개 전 최종 SBOM에 대한 라이선스 검토가 필요합니다.

## 공식 참조

- [OpenRelik server](https://github.com/openrelik/openrelik-server)
- [OpenRelik deploy](https://github.com/openrelik/openrelik-deploy)
- [Dissect Target](https://github.com/fox-it/dissect.target)
- [The Sleuth Kit](https://github.com/sleuthkit/sleuthkit)
- [libewf](https://github.com/libyal/libewf)
- [Debian ewf-tools](https://packages.debian.org/bookworm/amd64/ewf-tools)
- [Ollama](https://github.com/ollama/ollama)

OpenRelik에서 복사한 파일의 라이선스 전문과 정확한 source commit은 `vendor/openrelik/`에 포함되어 있습니다. Dissect/TSK/libewf 소스는 수정하지 않았습니다. 실제 증거, 모델 weights, 계정 키는 저장소나 배포 ZIP에 포함하지 않습니다.
