# 보고서 템플릿 계약

`templates/report.html`은 Jinja2 고정 템플릿입니다. 사용자 양식을 받을 때 이 파일과 CSS를 교체하고 `workbench/reporting.py`의 데이터 계약을 유지합니다. 템플릿 변경은 과거 snapshot을 수정하지 않습니다.

`ReportDocument 1.1` 필드:

| 필드 | 의미 |
|---|---|
| id / generated_at / schema_version | 보고서 식별·생성시각·스키마 버전 |
| case | 사건 이름·질문·프로파일·실행 상태 |
| evidence | 원본, 크기, 각 segment SHA-256 |
| coverage / limitations | 조사 범위와 미완료 영역 |
| judgments | AI 자동 최종 판단. summary와 확인/유력/미확인 findings, 이유·원문 ID·대안·남은 검사·모델 receipt |
| automatic_findings | 중간 조사에서 생성한 해석 기록. 최종 판단과 별도 보존 |
| claims | 과거 버전의 분석가 판단 기록. 새 작업에 승인 절차를 요구하지 않음 |
| observations | 불변 관측, 원본 위치, receipt/cell ID |
| lineage | 동일 관측에 연결된 도구 receipt들 |
| hypotheses | 미확인 가설과 이유 |
| tool_receipts | 도구 이름·버전·종료/오류·완결성·출력 수 |

Jinja autoescape를 유지하세요. 증거 문자열에 `safe` 필터를 적용하지 마세요. 외부 script, 원격 font/CDN은 넣지 않습니다. AI가 HTML 자체를 생성하지 않습니다.

AI 최종 판단은 사람의 승인 없이 보고서에 포함합니다. 유력·미확인 항목도 이유와 한계를 함께 표시하며 미완료 검사 때문에 자동 제외하지 않습니다. 확인은 명시한 좁은 사실에 대한 직접 근거, 유력은 정황상 가장 타당한 설명, 미확인은 자료 부족·상충 상태입니다. 확인·유력 판단은 제공된 원문 ID를 반드시 참조합니다. 입력 ID 검증이 자연어 해석의 정확도를 보장하지는 않습니다. `covered_zero`는 오직 해당 수집 범위의 0건 영수증이지 전역적 부재 증명이 아닙니다.

`manifest.json`은 HTML/JSON와 템플릿 hash를 포함하고 `SHA256SUMS`는 manifest까지 포함합니다. SHA-256은 변조 탐지용이며 전자서명·타임스탬프 공증은 아닙니다. DB의 report record는 ZIP hash와 연결됩니다.
