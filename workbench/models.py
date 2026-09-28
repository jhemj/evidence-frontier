from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NewCase(Strict):
    name: str = Field(min_length=1,max_length=120)
    question: str = Field(default='',max_length=4000)
    profile: Literal['standard','triage'] = 'standard'
    target_os: Literal['linux','windows'] = 'linux'


class EvidenceRequest(Strict):
    path: str = Field(min_length=1,max_length=1000)
    openrelik_file_id: int | None = Field(default=None,gt=0)


class MessageRequest(Strict):
    message: str = Field(min_length=1,max_length=4000)


class ProviderConfig(Strict):
    protocol: Literal['ollama','openai_compatible'] = 'ollama'
    base_url: str = 'http://host.docker.internal:11434'
    model: str = Field(default='',max_length=200)
    falsifier_model: str = Field(default='',max_length=200)
    trusted_lan: bool = False
    investigator: Literal['native'] = 'native'
    investigation_strategy: Literal['guided','baseline'] = 'guided'
    think: Literal['off','on','auto','low','medium','high','xhigh'] = 'off'
    num_ctx: int = Field(default=32768, ge=4096, le=131072, strict=True)
    assistant_num_ctx: int = Field(default=16384, ge=4096, le=131072, strict=True)
    num_predict: int = Field(default=4000, ge=256, le=32768, strict=True)
    assistant_num_predict: int = Field(default=2500, ge=256, le=32768, strict=True)
    temperature: float = Field(default=0.1, ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode='after')
    def generation_limits(self):
        if self.num_predict >= self.num_ctx or self.assistant_num_predict >= self.assistant_num_ctx:
            raise ValueError('출력 한도는 문맥 한도보다 작아야 합니다.')
        if self.protocol != 'ollama' and self.think != 'off':
            raise ValueError('추론 모드 조절은 현재 Ollama 연결에서만 지원합니다.')
        return self


class Candidate(Strict):
    text: str = Field(min_length=1,max_length=2000)
    observation_ids: list[str] = Field(min_length=1,max_length=20)
    claim_type: Literal['observation','interpretation','absence'] = 'interpretation'
    alternatives: list[str] = Field(default_factory=list,max_length=8)
    uncertainty: str = Field(max_length=2000)


class Analysis(Strict):
    summary: str = Field(max_length=6000)
    claims: list[Candidate] = Field(default_factory=list,max_length=8)


class ClaimStage(Strict):
    stage: Literal['configuration','invocation','execution','connection','objective','intent']
    judgment: Literal['확인','유력','미확인']
    statement: str = Field(min_length=1, max_length=350)
    observation_ids: list[str] = Field(default_factory=list, max_length=8)
    network_state: Literal['configured','attempted','failed','indeterminate','succeeded','objective'] | None = None


class CheckAssessment(Strict):
    basis: Literal['positive_evidence','absence'] = 'positive_evidence'
    check_id: str = Field(max_length=100)
    dossier_id: str = Field(default='', max_length=100)
    contract_id: str = Field(default='', max_length=100)
    outcome: Literal['supports','refutes','inconclusive']
    reason: str = Field(min_length=1, max_length=500)
    observation_ids: list[str] = Field(default_factory=list, max_length=8)


class FactAssertion(Strict):
    observation_id: str = Field(min_length=1, max_length=100)
    pointer: str = Field(min_length=8, max_length=500)
    operator: Literal['equals', 'contains'] = 'equals'
    value: Annotated[str, Field(max_length=4096)] | int | bool | None = Field(default=None)


class JudgmentFinding(Strict):
    dossier_id: str = Field(default='', max_length=100)
    timeline_role: Literal['핵심','참고','반증됨'] = '핵심'
    title: str = Field(min_length=1, max_length=240,description='카드만 읽어도 알 수 있는 제목: 구체적 대상·계정 + 실제 관측 행위/설정 + 중요한 범위. 탐지명 복사 금지. 권장 25~65자, 설정·호출·실행·성공을 구별.')
    card_summary: str = Field(default='',max_length=280,description='카드 전용 2~3문장: 무엇이 어디서 관측됐는지 + 사건 관련 의미 + 아직 미확인인 핵심 한계. 원문 근거만 사용. 제목 반복·막연한 위험 표현·근거 없는 악성/정상 확정 금지. 100~220자 권장. reason의 상세 논증과 분리.')
    judgment: Literal['확인', '유력', '미확인']
    reason: str = Field(min_length=1, max_length=700)
    change_reason: str = Field(default='',max_length=500,description='이전 판단이 바뀌었다면 새 근거 또는 재해석으로 무엇을 정정했는지 간단히 설명. 최초 판단은 빈 문자열 허용.')
    observation_ids: list[str] = Field(default_factory=list, max_length=8)
    alternatives: list[str] = Field(default_factory=list, max_length=3)
    remaining_checks: list[str] = Field(default_factory=list, max_length=3)
    stages: list[ClaimStage] = Field(default_factory=list, max_length=6)
    basis: Literal['positive_evidence','absence'] = 'positive_evidence'
    counterevidence_ids: list[str] = Field(default_factory=list, max_length=8)
    fact_assertions: list[FactAssertion] = Field(default_factory=list, max_length=3)


class RetrievalScope(Strict):
    source_offset: int | None = Field(default=None, ge=0)
    cursor: str = Field(default='', max_length=2000)
    limit: int = Field(default=30, ge=1, le=60)
    byte_offset: int = Field(default=0, ge=0)
    byte_length: int = Field(default=8192, ge=256, le=65536)
    partition_offset: int | None = Field(default=None, ge=0)
    inode: int | None = Field(default=None, ge=0)
    account: str = Field(default='', max_length=200)
    time_from: str = Field(default='', max_length=80)
    time_to: str = Field(default='', max_length=80)


class JudgmentCheck(RetrievalScope):
    tool: Literal['search', 'read_file', 'read_source', 'static_file', 'archive_list', 'correlate']
    query: str = Field(default='', max_length=200)
    path: str = Field(default='', max_length=1500)
    reason: str = Field(min_length=1,max_length=1000)
    hypothesis_id: str = Field(min_length=1,max_length=100)
    success_condition: str = Field(min_length=1,max_length=500)
    refutation_condition: str = Field(default='', max_length=500)
    inconclusive_condition: str = Field(default='Partial, missing or ambiguous evidence cannot decide the hypothesis.', max_length=500)


class JudgmentReport(Strict):
    summary: str = Field(min_length=1, max_length=1200)
    findings: list[JudgmentFinding] = Field(min_length=1, max_length=10)
    next_checks: list[JudgmentCheck] = Field(default_factory=list,max_length=4)
    check_assessments: list[CheckAssessment] = Field(default_factory=list,max_length=12)


class SynthesisReport(JudgmentReport):
    supporting_evidence_ids: list[str] = Field(default_factory=list,max_length=8)
    refuting_evidence_ids: list[str] = Field(default_factory=list,max_length=8)


class Falsification(Strict):
    alternatives: list[str] = Field(max_length=8, description='문자열 배열: 주장 범위에 맞는 경쟁 설명. 반대 ID를 인용하면 그 자료가 정확히 어떤 주장과 양립하지 않는지 ID와 함께 설명.')
    contradicting_observation_ids: list[str] = Field(max_length=20, description='제시된 원문 중 해당 주장과 직접 양립하지 않는 양성 근거 ID만. 지지 자료·동일 기록 복제·성공 미확인·정상 가능성은 반증이 아님. 직접 반대 근거가 없으면 빈 배열.')
    missing_checks: list[str] = Field(max_length=8, description='문자열 배열: 아직 필요한 판별 검사와 확인하지 못한 범위. 미실시 검사를 이미 실패/반박된 것으로 쓰지 않음.')


class Decision(Strict):
    decision: Literal['approve','reject']
    rationale: str = Field(min_length=3,max_length=2000)


class WorkerRequest(Strict):
    action: Literal['integrity','inventory','normalize','timeline','crosscheck','linux_scan','windows_scan']
    path: str


class InvestigationTool(RetrievalScope):
    tool: Literal['search', 'read_file', 'read_source', 'static_file', 'archive_list', 'correlate']
    query: str = Field(default='', max_length=200)
    path: str = Field(default='', max_length=1500)
    reason: str = Field(default='', max_length=1000)


class HypothesisAssessment(Strict):
    number: int | None = Field(default=None, ge=1, description='기본 조사 영역 번호. 자연 발생 가설은 생략하며 하네스가 번호를 부여합니다.')
    hypothesis_id: str = Field(default='', max_length=120)
    action: Literal['create','update','reinforce','refute','hold'] = 'update'
    hypothesis_card_id: str = Field(default='', max_length=120)
    title: str = Field(default='', max_length=240)
    card_summary: str = Field(default='', max_length=280)
    change_reason: str = Field(default='', max_length=1000)
    basis: Literal['positive_evidence','absence'] = 'positive_evidence'
    judgment: Literal['확정', '유력', '미확인']
    reasoning: str = Field(max_length=1500)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    refuting_evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    remaining_checks: list[str] = Field(default_factory=list, max_length=6)


class InvestigationPlan(Strict):
    summary: str = Field(max_length=4000)
    tool_calls: list[InvestigationTool] = Field(default_factory=list, max_length=4)
    claims: list[Candidate] = Field(default_factory=list, max_length=5)
    remaining_questions: list[str] = Field(default_factory=list, max_length=10)
    hypotheses: list[HypothesisAssessment] = Field(default_factory=list, max_length=3)


class InvestigationToolRequest(Strict):
    evidence_path: str
    run_id: str = Field(pattern=r'^RUN-[a-f0-9]{32}$')
    request: InvestigationTool
    target_os: Literal['linux','windows'] = 'linux'


class WorkerJobRequest(WorkerRequest):
    action: Literal['integrity','inventory','normalize','timeline','crosscheck','linux_scan','windows_scan','investigation_tool']
    job_key: str = Field(pattern=r'^[a-f0-9]{64}$')
    signature: str = Field(min_length=1, max_length=200)
    investigation: InvestigationToolRequest | None = None
