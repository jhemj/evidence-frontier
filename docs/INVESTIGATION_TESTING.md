# 실제 서비스 검증

Python 단위 검사와 Docker E2E는 별도로 실행한다. 모델 응답을 흉내 낸 단위 검사는 실제 모델·포렌식 도구 검증을 대체하지 않는다.

## Windows / Ubuntu 공통 재현

Docker Compose의 `!override` 지원 버전, Python 3.12, 이미 설치된 로컬 Ollama 모델이 필요하다. 먼저 해당 OS의 setup 스크립트로 환경을 준비하고 `docker compose build`로 이미지를 만든다.

```text
python scripts/run_investigation_e2e.py --model <설치된-로컬-모델명>
```

스크립트는 인터넷 없는 worker 컨테이너에서 합성 ext4/MBR E01을 생성한다. 그 증거만 연결한 별도 Docker 프로젝트와 저장 볼륨을 localhost:8767에 실행하고 실제 모델을 호출한다. 사용자 원본 증거와 사건 DB는 사용하지 않는다. 소요 시간은 모델 성능에 따라 달라진다.

`.env`에 `WORKBENCH_TOKEN`을 설정했다면 스크립트를 실행하는 터미널 환경 변수에도 같은 값을 설정한다. 비밀 값은 명령 인자나 검증 기록에 넣지 않는다.

## 독립적으로 정의한 정답

- SSH 인증 성공 기록이 있다.
- agent 예약 작업에는 설정·호출·명령 기록이 있다. audit의 동일 native 사건에 SYSCALL/EXECVE/CWD 세 행이 있다.
- dormant 예약 작업은 설정만 있다. 이를 실제 실행으로 승격하면 안 된다.
- 검사 스크립트의 악성코드 이름은 탐지 문자열이다. 이를 감염으로 승격하면 안 된다.
- scp 명령이 있지만 별도 전송 실패 기록이 있다. 명령만으로 유출 성공을 확정하면 안 된다.

`verify_investigation_e2e.py`는 자동 보고서 생성, 실제 모델·도구·반증 영수증, 위 핵심 구조화 사실, 후보의 근거 참조, 패키지 전체 파일 해시와 결과 revision을 검증한다. 자연어 주장 전체의 정확도·침해 탐지율을 증명하는 시험은 아니다.

## 복구와 범위 검사

`tests/test_investigation_graph.py`는 실제 SQLite checkpointer를 매 단계 다시 열어 계획·예산·결과를 재사용하는지, 잘못된 모델 JSON을 제한된 예산으로 재시도하는지 확인한다. `tests/test_worker_jobs.py`는 중복 요청의 단일 결과, 변조 차단, 재시작한 실행 불명의 자동 재실행 차단을 검사한다.

브라우저에서는 일시정지 요청 → 진행 작업 마무리 → 일시정지, 서버 재시작 후 계속, 원문 근거 열기, 보고서 내려받기, 연결 해제와 재연결을 검사한다. 실제 Ubuntu 호스트와 Windows Docker Desktop 검증은 각각 실행 기록을 남겨야 한다.
