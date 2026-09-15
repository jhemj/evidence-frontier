from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class NewCase(Strict):
    name: str = Field(min_length=1,max_length=120)
    question: str = Field(default='',max_length=4000)
    profile: Literal['standard','triage'] = 'standard'


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


class CheckAssessment(Strict):
    basis: Literal['positive_evidence','absence'] = 'positive_evidence'
    check_id: str = Field(max_length=100)
    dossier_id: str = Field(default='', max_length=100)
    contract_id: str = Field(default='', max_length=100)
    outcome: Literal['supports','refutes','inconclusive']
    reason: str = Field(min_length=1, max_length=500)
    observation_ids: list[str] = Field(default_factory=list, max_length=8)


class JudgmentFinding(Strict):
    dossier_id: str = Field(default='', max_length=100)
    timeline_role: Literal['핵심','참고','반증됨'] = '핵심'
    title: str = Field(min_length=1, max_length=240)
    judgment: Literal['확인', '유력', '미확인']
    reason: str = Field(min_length=1, max_length=700)
    observation_ids: list[str] = Field(default_factory=list, max_length=8)
    alternatives: list[str] = Field(default_factory=list, max_length=3)
    remaining_checks: list[str] = Field(default_factory=list, max_length=3)
    stages: list[ClaimStage] = Field(default_factory=list, max_length=6)


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


class Falsification(Strict):
    alternatives: list[str] = Field(max_length=8)
    contradicting_observation_ids: list[str] = Field(max_length=20)
    missing_checks: list[str] = Field(max_length=8)


class Decision(Strict):
    decision: Literal['approve','reject']
    rationale: str = Field(min_length=3,max_length=2000)


class WorkerRequest(Strict):
    action: Literal['integrity','inventory','normalize','timeline','crosscheck','linux_scan']
    path: str


class InvestigationTool(RetrievalScope):
    tool: Literal['search', 'read_file', 'read_source', 'static_file', 'archive_list', 'correlate']
    query: str = Field(default='', max_length=200)
    path: str = Field(default='', max_length=1500)
    reason: str = Field(default='', max_length=1000)


class HypothesisAssessment(Strict):
    number: int = Field(ge=1, le=10)
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


class WorkerJobRequest(WorkerRequest):
    action: Literal['integrity','inventory','normalize','timeline','crosscheck','linux_scan','investigation_tool']
    job_key: str = Field(pattern=r'^[a-f0-9]{64}$')
    signature: str = Field(min_length=1, max_length=200)
    investigation: InvestigationToolRequest | None = None
