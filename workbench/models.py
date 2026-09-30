from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator, create_model
from functools import lru_cache


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


class ModelConnection(Strict):
    protocol: Literal['ollama','openai_compatible'] = 'ollama'
    base_url: str = 'http://host.docker.internal:11434'
    model: str = Field(default='',max_length=200)
    falsifier_model: str = Field(default='',max_length=200)
    trusted_lan: bool = False
    model_digest: str | None = Field(default=None,pattern=r'^(unverified|[a-f0-9]{64})$')
    think: Literal['off','on','auto','low','medium','high','xhigh'] = 'off'
    structured_output: Literal['json_schema','json'] = 'json_schema'
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


class ProviderConfig(ModelConnection):
    # Local Ollama stays serial by default. Test-only transports may explicitly
    # opt into two independent dossier requests; the global scheduler still
    # caps all model requests at two.
    review_concurrency: int = Field(default=1, ge=1, le=2, strict=True)
    second_review_policy: Literal['always','conditional-v1'] = 'always'
    review_queue_policy: Literal['question-priority-v1','returned-first-v1'] = 'question-priority-v1'
    test_contract_policy: Literal['legacy','purpose-outcomes-v2'] = 'legacy'
    recovery_policy: Literal['disabled','bounded-v1'] = 'disabled'
    investigator: Literal['native'] = 'native'
    investigation_strategy: Literal['guided','baseline'] = 'guided'
    secondary: ModelConnection | None = None
    role_routes: dict[Literal['analyst','investigator','judgment','synthesis','falsifier'],Literal['primary','secondary']] = Field(default_factory=dict)

    @model_validator(mode='after')
    def valid_routes(self):
        if 'secondary' in self.role_routes.values() and (self.secondary is None or not self.secondary.model):
            raise ValueError('보조 모델로 역할을 배정하려면 두 번째 모델을 먼저 설정하세요.')
        if self.secondary and any(c.falsifier_model and c.falsifier_model!=c.model for c in (self,self.secondary)):
            raise ValueError('두 모델 연결에서는 별도 반증 모델 이름 대신 역할 배정을 사용하세요.')
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
    statement: str = Field(min_length=1, max_length=350, description='Exact narrow proposition being judged; a recorded attempt is not a successful outcome.')
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
    evidence_span_ids: list[str] = Field(default_factory=list, max_length=12)


class FactAssertion(Strict):
    observation_id: str = Field(min_length=1, max_length=100)
    pointer: str = Field(min_length=8, max_length=500, pattern=r'^/fields/',
        description='Exact JSON pointer into this observation, e.g. /fields/path or /fields/excerpt. Copy a literal_fact_candidates pointer; never use dot notation, source ID or a natural-language label.')
    operator: Literal['equals', 'contains'] = 'equals'
    value: Annotated[str, Field(max_length=4096)] | int | bool | FiniteFloat | None = Field(default=None)


class IncidentRelevance(Strict):
    level: Literal['direct','indirect','context','undetermined'] = 'undetermined'
    reason: str = Field(default='', max_length=500)
    observation_ids: list[str] = Field(default_factory=list, max_length=8)


class JudgmentFinding(Strict):
    dossier_id: str = Field(default='', max_length=100)
    timeline_role: Literal['핵심','참고','반증됨'] = '참고'
    incident_relevance: IncidentRelevance = Field(default_factory=IncidentRelevance,
        description='Relevance to the actual case question, separate from confidence in the narrow fact and from investigation priority. Direct/indirect require cited source-backed explanation. A detector hit, pending work or many hypotheses cannot establish relevance.')
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
    evidence_span_ids: list[str] = Field(default_factory=list, max_length=12,
        description='For paged sources, exact SPAN IDs actually supporting or contradicting this finding. Preserve material contrary spans; an observation ID alone does not identify the selected field range.')
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


class TestDesign(Strict):
    purpose: Literal['discover','discriminate','verify_reliability'] = 'discover'
    immediate_observable: Literal['source_content','matching_records','static_properties','archive_members','record_association','trusted_baseline_comparison','execution_outcome','network_outcome','authorization'] = 'source_content'
    required_observation_ids: list[str] = Field(default_factory=list,max_length=8)
    baseline_observation_ids: list[str] = Field(default_factory=list,max_length=8)
    expected_update: str = Field(default='',max_length=500,
        description='What this immediate result changes in the question or next feasible test. Discovery may locate evidence without deciding the final hypothesis.')
    reopen_on: str = Field(default='',max_length=400,
        description='Changed evidence/object/resolution context needed to justify repeating a failed or already assessed test. Rephrasing the same condition is not progress.')
    discrimination_target: 'ExplanationDiscriminator | None' = Field(default=None,
        description='Optional exact adopted proposition this test discriminates. Copy claim_ref and target_scope exactly from accepted_claim_refs supplied in this input. Omit when not supplied. A dossier owner, shared question or shared source is not this semantic link; this field grants no additional execution or judgment authority.')


class TestObjectRef(Strict):
    kind: Literal['dossier','hypothesis','claim','case_question','observation','evidence']
    id: str = Field(min_length=1,max_length=180)
    version: str = Field(pattern=r'^[a-f0-9]{64}$',description='Copy the Controller-offered record version exactly; no latest-version substitution.')


class TestTargetScope(Strict):
    case_id: str = Field(min_length=1,max_length=180)
    task_id: str = Field(min_length=1,max_length=180)
    evidence_id: str = Field(min_length=1,max_length=180)
    generation: int = Field(ge=0,strict=True)
    source_run: str = Field(min_length=1,max_length=180)


class RequiredTestInput(Strict):
    ref: TestObjectRef
    role: Literal['subject','context','baseline','counterevidence','reliability']
    required_view: Literal['metadata','body_excerpt','full_body']
    trust_basis: Literal['unassessed','retained_source','independent_baseline']
    scope: TestTargetScope


TEST_OUTCOME_UNSUPPORTED = 'unsupported_by_this_test'
TEST_PURPOSE_OUTCOMES = {
    'discover': frozenset(('found','no_match_in_scope','partial','unavailable')),
    'discriminate': frozenset(('supports','refutes','inconclusive')),
    'verify_reliability': frozenset(('supports','refutes','inconclusive')),
}


class TestOutcomeRules(Strict):
    """All 7 rules required. Unused rules = exact unsupported_by_this_test.
    allowed_outcomes = all non-sentinel keys.
    """
    supports: str = Field(min_length=1,max_length=500)
    refutes: str = Field(min_length=1,max_length=500)
    inconclusive: str = Field(min_length=1,max_length=500,
        description='Discover: exact unsupported_by_this_test; otherwise a scoped non-decisive condition.')
    found: str = Field(min_length=1,max_length=500)
    no_match_in_scope: str = Field(min_length=1,max_length=500)
    partial: str = Field(min_length=1,max_length=500)
    unavailable: str = Field(min_length=1,max_length=500)


class TestResultLineage(Strict):
    parent_contract_id: str = Field(pattern=r'^[a-f0-9]{64}$')
    result_revision: str = Field(pattern=r'^[a-f0-9]{64}$')


class TestDesignV2(Strict):
    """Purpose/outcome crossing uses the system protocol, validated after JSON schema."""
    version: Literal[2] = 2
    purpose: Literal['discover','discriminate','verify_reliability']
    immediate_observable: TestDesign.model_fields['immediate_observable'].annotation
    owner_ref: TestObjectRef
    question_ref: TestObjectRef = Field(description='Copy offered case_question ref/version; never an owner ref.')
    target_ref: TestObjectRef | None
    target_scope: TestTargetScope
    target_proposition: str = Field(max_length=2000)
    required_inputs: list[RequiredTestInput] = Field(max_length=12)
    baseline_ref: TestObjectRef | None
    required_result_view: Literal['metadata','body_excerpt','full_body']
    outcome_rules: TestOutcomeRules
    allowed_outcomes: list[Literal['supports','refutes','inconclusive','found','no_match_in_scope','partial','unavailable']] = Field(min_length=1,max_length=7,
        description='Exactly the unique rule keys whose values are not unsupported_by_this_test.')
    design_timing: Literal['before_result','after_result']
    uses_existing_result: bool
    lineage: TestResultLineage | None
    expected_update: str = Field(min_length=1,max_length=500)
    reopen_on: str = Field(max_length=400)

    @model_validator(mode='after')
    def purpose_contract(self):
        unsupported=TEST_OUTCOME_UNSUPPORTED
        allowed=set(self.allowed_outcomes)
        if len(allowed)!=len(self.allowed_outcomes):raise ValueError('Duplicate allowed outcome')
        rules=self.outcome_rules.model_dump()
        supported={k for k,v in rules.items() if v!=unsupported}
        permitted=TEST_PURPOSE_OUTCOMES[self.purpose]
        if allowed!=supported:
            raise ValueError('allowed_outcomes must exactly match supported outcome rules: '
                f'non-sentinel keys={sorted(supported)}, allowed_outcomes={sorted(allowed)}; '
                f'unsupported rules must equal {unsupported!r} exactly; '
                f'purpose={self.purpose!r} permits only {sorted(permitted)}. '
                'Reassess the actual purpose and conditions; do not relabel a collection result as hypothesis support.')
        if self.purpose=='discover':
            if allowed-permitted:
                raise ValueError('Discovery collects observations, not hypothesis support/refutation: '
                    f'supports/refutes/inconclusive rules must be exactly {unsupported!r}; '
                    f'allowed outcomes are a supported subset of {sorted(permitted)}.')
            if self.target_ref is not None:raise ValueError('Discovery does not discriminate a target proposition')
        else:
            if not self.target_proposition.strip() or self.target_ref is None:
                raise ValueError('Discrimination/reliability requires an exact target and proposition')
            if allowed-permitted or not allowed&{'supports','refutes'}:
                raise ValueError('At least one discriminating side must be supported: '
                    f'permit only {sorted(permitted)} with a meaningful supports or refutes rule; '
                    f'found/no_match_in_scope/partial/unavailable must be exactly {unsupported!r}.')
        if self.question_ref.kind!='case_question':raise ValueError('question_ref must reference a question')
        baselines=[r.ref for r in self.required_inputs if r.role=='baseline']
        if (self.baseline_ref is None and baselines) or (self.baseline_ref is not None and self.baseline_ref not in baselines):
            raise ValueError('baseline_ref must explicitly select a required baseline or be null')
        if self.design_timing=='after_result':
            if not self.uses_existing_result or self.lineage is None:
                raise ValueError('After-result design requires explicit existing-result lineage')
        elif self.uses_existing_result or self.lineage is not None:
            raise ValueError('Before-result design cannot claim result reuse')
        return self


class JudgmentCheck(RetrievalScope):
    question_id: str = Field(default='',max_length=100,description='Copy a supplied current question ID when this test advances it; never invent an ID.')
    tool: Literal['search', 'read_file', 'read_source', 'static_file', 'archive_list', 'correlate']
    query: str = Field(default='', max_length=200)
    path: str = Field(default='', max_length=1500)
    reason: str = Field(min_length=1,max_length=1000)
    hypothesis_id: str = Field(min_length=1,max_length=100)
    success_condition: str = Field(min_length=1,max_length=500)
    refutation_condition: str = Field(default='', max_length=500)
    inconclusive_condition: str = Field(default='Partial, missing or ambiguous evidence cannot decide the hypothesis.', max_length=500)
    test_design: TestDesign = Field(default_factory=TestDesign)


class ObjectionAssessment(Strict):
    objection_id: str = Field(min_length=1, max_length=100)
    outcome: Literal['open', 'resolved']
    reason: str = Field(min_length=1, max_length=700)
    basis: Literal['positive_evidence', 'absence'] = 'positive_evidence'
    observation_ids: list[str] = Field(default_factory=list, max_length=8)


class ExcerptSelection(Strict):
    observation_id: str = Field(min_length=1, max_length=100)
    source_span_id: str = Field(default='', max_length=100,
        description='Copy parent SPAN ID when selecting within source_span/excerpt_spans; empty for a whole excerpt field.')
    quote: str = Field(min_length=1, max_length=4096,
        description='Exact contiguous text from fields.excerpt or one presented excerpt_spans.text. No ellipses, normalization, paraphrase or invented text.')
    occurrence: int = Field(default=0, ge=0,
        description='Zero-based occurrence of this exact quote in that presented field/span; overlapping matches count.')
    reason: str = Field(min_length=1, max_length=500)


class SourceMetadataSelection(Strict):
    observation_id: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=1,max_length=500,
        description='Why only non-excerpt fields support the narrow proposition. This explicitly omits the body; it cannot support content, execution or absence claims.')


class SourcePassage(Strict):
    source_span_id: str = Field(default='',max_length=100,
        description='Parent SPAN ID when multiple spans are presented. With exactly one presented field/span, empty uniquely identifies that field; a supplied wrong ID is always rejected.')
    quote: str = Field(min_length=1,max_length=4096,
        description='Exact contiguous decoded text from the presented excerpt, never serialized metadata or padding.')
    occurrence: int = Field(default=0,ge=0)


class SourceExcerptChoice(Strict):
    mode: Literal['excerpt']
    observation_id: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=1,max_length=500)
    passages: list[SourcePassage] = Field(min_length=1)


class SourceMetadataChoice(Strict):
    mode: Literal['metadata_only']
    observation_id: str = Field(min_length=1,max_length=100)
    reason: str = Field(min_length=1,max_length=500,
        description='Only non-excerpt fields support this proposition. The omitted body cannot support content, execution or absence claims.')


SourceChoice = Annotated[SourceExcerptChoice | SourceMetadataChoice,Field(discriminator='mode')]


class ExplanationProposal(Strict):
    question_id: str = Field(default='',max_length=100,
        description='Supplied current question ID; empty only for a case-root follow-up.')
    explanation: str = Field(min_length=1,max_length=700,
        description='A new possible explanation, not an established fact or a verdict. A trigger does not prove this explanation.')
    discriminating_question: str = Field(min_length=1,max_length=300)
    trigger_observation_ids: list[str] = Field(min_length=1,max_length=8)
    next_discriminator: str = Field(min_length=1,max_length=500,
        description='What observation would distinguish this explanation from the current alternatives; not a promise of execution.')


class JudgmentReport(Strict):
    summary: str = Field(min_length=1, max_length=1200)
    findings: list[JudgmentFinding] = Field(min_length=1, max_length=10)
    next_checks: list[JudgmentCheck] = Field(default_factory=list,max_length=4)
    check_assessments: list[CheckAssessment] = Field(default_factory=list,max_length=12)
    explanation_proposals: list[ExplanationProposal] = Field(default_factory=list,max_length=3,
        description='Optional NEW competing explanations prompted by these presented sources. [] is valid. '
            'Keep triggering evidence separate from supporting evidence; do not restate existing hypotheses or require a minimum count.')
    excerpt_selections: list[ExcerptSelection] = Field(default_factory=list,
        description='Working pages only: select exact evidence-bearing excerpts from cited sources for later comparison. Preserve decisive context and contrary evidence; omitted text stays unrepresented, never disproved. Omit a source from this list to keep its entire presented excerpt.')
    source_metadata_selections: list[SourceMetadataSelection] = Field(default_factory=list,
        description='Working pages only: explicitly carry all non-excerpt fields while leaving the body in the immutable source page. Never use for claims about omitted text or material contrary content.')
    objection_assessments: list[ObjectionAssessment] = Field(default_factory=list, max_length=12,
        description='Disposition of supplied open_objections. Omission keeps them open. Intermediate source/comparison pages cannot resolve. Resolution needs re-presented contrary sources and positive evidence, never absence alone.')


class IncidentAssessment(Strict):
    verdict: Literal['undetermined','suspected','probable','confirmed','refuted']
    scope: str = Field(min_length=1,max_length=240,description='The specific unauthorized/compromise hypothesis being assessed, not a generic file or command fact.')
    rationale: str = Field(min_length=1,max_length=1000,description='Why the positive and contrary evidence supports this intrusion assessment; explain remaining legitimate alternatives. No probability or count-based score.')
    summary: str = Field(default='',max_length=500,description='Reader-friendly Korean briefing in 2–3 short sentences: what the cited evidence establishes within scope; what remains unknown; which discriminating investigation is needed next. Do not turn command records into successful execution, or narrow facts into confirmed intrusion. No IDs, jargon, percentages or unsupported causal claims.')
    supporting_evidence_ids: list[str] = Field(default_factory=list,max_length=8)
    refuting_evidence_ids: list[str] = Field(default_factory=list,max_length=8)


class SynthesisReport(JudgmentReport):
    supporting_evidence_ids: list[str] = Field(default_factory=list,max_length=8)
    refuting_evidence_ids: list[str] = Field(default_factory=list,max_length=8)
    incident_assessment: IncidentAssessment | None = Field(default=None,description='Separate intrusion judgment for this question. Null for a narrow fact-only question. A confirmed configuration, command record, hash mismatch or static capability is NOT confirmed intrusion. Refuted applies only to this scope, never certifies the whole system clean.')
    scenario_assessment: 'ScenarioAssessment | None' = None


class WorkingFinding(Strict):
    """A source-page note, not a user card or an incident-wide assessment."""
    dossier_id: str = Field(min_length=1,max_length=100)
    title: str = Field(min_length=1,max_length=240)
    judgment: Literal['확인','유력','미확인']
    reason: str = Field(min_length=1,max_length=400)
    observation_ids: list[str] = Field(default_factory=list,max_length=8)
    counterevidence_ids: list[str] = Field(default_factory=list,max_length=8)
    evidence_span_ids: list[str] = Field(default_factory=list,max_length=12)
    fact_assertions: list[FactAssertion] = Field(default_factory=list,max_length=3)
    stages: list[ClaimStage] = Field(default_factory=list,max_length=6)
    basis: Literal['positive_evidence','absence'] = 'positive_evidence'
    alternatives: list[str] = Field(default_factory=list,max_length=3)
    remaining_checks: list[str] = Field(default_factory=list,max_length=3)


class WorkingCheckAssessment(CheckAssessment):
    dossier_id: str = Field(max_length=100,description='Copy the executed check contract dossier_id, not the source or job ID. Empty only if the supplied check has no owner contract.')
    contract_id: str = Field(max_length=100,description='Copy the exact executed contract_id. Never omit a supplied contract.')
    observation_ids: list[str] = Field(default_factory=list,max_length=8,
        description='Specific returned source facts needed for this outcome, not a list of every match. For an inconclusive outcome based only on this check\'s status/coverage metadata use []; the check_id already binds those metadata. Preserve material supporting/contrary sources when the reasoning actually depends on their content.')


class WorkingReview(Strict):
    summary: str = Field(min_length=1,max_length=600)
    findings: list[WorkingFinding] = Field(min_length=1,max_length=10)
    source_selections: list[SourceChoice] = Field(
        description='At most ONE choice per cited source: excerpt with exact passages OR metadata_only with no body citation. Omit a source from this array to retain its entire presented view. Sources without excerpt bodies need no selection. Preserve sufficient context and contrary evidence; this is not a final card.')
    check_assessments: list[WorkingCheckAssessment] = Field(default_factory=list,max_length=12)
    objection_assessments: list[ObjectionAssessment] = Field(default_factory=list,max_length=12)
    next_checks: list[JudgmentCheck] = Field(default_factory=list,max_length=4)

    @model_validator(mode='after')
    def one_mode_per_source(self):
        ids=[choice.observation_id for choice in self.source_selections]
        if len(ids)!=len(set(ids)):
            raise ValueError('Choose exactly one source mode per observation: excerpt OR metadata_only, never both or duplicate choices.')
        return self


class WorkingSynthesis(WorkingReview):
    supporting_evidence_ids: list[str] = Field(default_factory=list,max_length=8)
    refuting_evidence_ids: list[str] = Field(default_factory=list,max_length=8)


class JudgmentCheckV2(JudgmentCheck):
    test_design: TestDesignV2


class CheckAssessmentV2(CheckAssessment):
    outcome: Literal['supports','refutes','inconclusive','found','no_match_in_scope','partial','unavailable']


class WorkingCheckAssessmentV2(WorkingCheckAssessment):
    outcome: CheckAssessmentV2.model_fields['outcome'].annotation


class JudgmentReportV2(JudgmentReport):
    next_checks: list[JudgmentCheckV2] = Field(default_factory=list,max_length=4)
    check_assessments: list[CheckAssessmentV2] = Field(default_factory=list,max_length=12)


class SynthesisReportV2(SynthesisReport):
    next_checks: list[JudgmentCheckV2] = Field(default_factory=list,max_length=4)
    check_assessments: list[CheckAssessmentV2] = Field(default_factory=list,max_length=12)


class WorkingReviewV2(WorkingReview):
    next_checks: list[JudgmentCheckV2] = Field(default_factory=list,max_length=4)
    check_assessments: list[WorkingCheckAssessmentV2] = Field(default_factory=list,max_length=12)


class WorkingSynthesisV2(WorkingSynthesis):
    next_checks: list[JudgmentCheckV2] = Field(default_factory=list,max_length=4)
    check_assessments: list[WorkingCheckAssessmentV2] = Field(default_factory=list,max_length=12)


def generation_schema(model, *, final_review=False):
    """Require explicit decisions, without changing legacy storage defaults.

    A decoder must not silently skip a field merely because old receipts have
    a compatibility default. Empty/unknown values remain legitimate where the
    domain contract permits them; citations and inference are validated later.
    """
    schema=model.model_json_schema()
    def explicit(node):
        if isinstance(node,dict):
            node.pop('default',None)
            if node.get('type')=='object' and 'properties' in node:
                node['required']=list(node['properties'])
            for value in node.values():explicit(value)
        elif isinstance(node,list):
            for value in node:explicit(value)
    explicit(schema)
    if final_review:
        # Selection changes the next working page, not the publishable finding.
        # Old persisted report models remain readable, but new final output must
        # cite the already validated source view rather than reselect it.
        for key in ('excerpt_selections','source_metadata_selections'):
            if key in schema.get('properties',{}):
                schema['properties'][key]['maxItems']=0
                schema['properties'][key]['description']='Return []; source reselection belongs to working pages, not final judgment.'
    return schema


def review_output_schema(role,pack):
    intermediate=pack.get('review_stream',{}).get('phase') in ('source_page','comparison_page','focus_page')
    if role=='synthesis':schema=WorkingSynthesis if intermediate else SynthesisReport
    elif role=='judgment':schema=WorkingReview if intermediate else JudgmentReport
    else:raise ValueError('Not a review role')
    if pack.get('test_contract_policy')=='purpose-outcomes-v2':
        schema={WorkingReview:WorkingReviewV2,JudgmentReport:JudgmentReportV2,
                WorkingSynthesis:WorkingSynthesisV2,SynthesisReport:SynthesisReportV2}[schema]
    count=len(pack.get('required_dossiers',[]))
    excerpts=None
    if 'observations' in pack:
        from .review_stream import resolved
        excerpts=any(isinstance(row.get('fields',{}).get('excerpt'),str) or
            bool(row.get('fields',{}).get('excerpt_spans')) for row in resolved(pack)['observations'])
    return _scoped_review_schema(schema,count,excerpts) if count or excerpts is False else schema


@lru_cache(maxsize=64)
def _scoped_review_schema(base,count,excerpts=None):
    # The request already specifies one finding per dossier. Expose that exact
    # cardinality in the generation schema instead of the generic batch ceiling.
    # Citation membership/ownership still validates identities independently.
    fields={}
    if count:
        fields['findings']=(base.model_fields['findings'].annotation,Field(min_length=count,max_length=count,
            description='Exactly one consolidated finding per required dossier. Executed check outcomes belong in check_assessments, never in duplicate findings.'))
    if excerpts is False:
        for key in ('source_selections',) if 'source_selections' in base.model_fields else ('excerpt_selections','source_metadata_selections'):
            field=base.model_fields[key]
            fields[key]=(field.annotation,Field(default=... if field.is_required() else [],max_length=0,
                description='This page has NO excerpt body. Return []. Structured fields are already fully retained: cite the observation and exact field assertions, never invent a quote from serialized JSON.'))
    return create_model(f'{base.__name__}For{count}Dossiers'+('WithoutExcerpts' if excerpts is False else ''),__base__=base,**fields)


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


class PlannedTool(InvestigationTool):
    question_id: str = Field(default='', max_length=100)
    success_condition: str = Field(default='', max_length=500)
    refutation_condition: str = Field(default='', max_length=500)
    inconclusive_condition: str = Field(default='', max_length=500)
    test_design: TestDesign = Field(default_factory=TestDesign)


class PlannedToolV2(PlannedTool):
    test_design: TestDesignV2


class ScenarioAssessment(Strict):
    comparison_question: str = Field(min_length=3, max_length=240, description='Same question for mutually competing explanations. Reuse a supplied comparison question exactly. Coexisting causal stages belong to different questions.')
    evidence_fit: Literal['limited','moderate','strong']
    ranking_reason: str = Field(min_length=3, max_length=700, description='Explain evidence fit and strongest contrary evidence, not source counts, severity or numerical probability.')
    alternative_explanation: str = Field(min_length=3, max_length=700, description='Strongest plausible alternative, including authorized operation where applicable; explain if none is supported.')
    next_check: str = Field(min_length=3, max_length=700, description='Discriminating next check, or explicitly why sources cannot distinguish explanations.')
    investigation_priority: Literal['high','normal','low']
    priority_reason: str = Field(min_length=3, max_length=700, description='Expected uncertainty reduction, impact and available test cost, independent of evidence-fit rank. No tool authority is granted.')


class ExplanationClaimRef(Strict):
    kind: Literal['claim'] = 'claim'
    id: str = Field(min_length=1, max_length=180)
    version: str = Field(pattern=r'^[a-f0-9]{64}$')


class ExplanationTargetScope(Strict):
    task_id: str = Field(min_length=1, max_length=120)
    evidence_id: str = Field(min_length=1, max_length=120)
    generation: int = Field(ge=0, strict=True)
    observation_ids: list[str] = Field(min_length=1, max_length=24)
    proposition: str = Field(min_length=1, max_length=2000)


class ExplanationDiscriminator(Strict):
    claim_ref: ExplanationClaimRef
    target_scope: ExplanationTargetScope


class ExplanationLinkProposal(ExplanationDiscriminator):
    relation: Literal['explains'] = 'explains'
    rationale: str = Field(min_length=1, max_length=700,
        description='Why this exact adopted proposition explains this hypothesis within its copied target scope. Shared source IDs alone are not an explanation relationship.')


class HypothesisAssessment(Strict):
    number: int | None = Field(default=None, ge=1, description='기본 조사 영역 번호. 자연 발생 가설은 생략하며 하네스가 번호를 부여합니다.')
    hypothesis_id: str = Field(default='', max_length=120, description='Use only an ID supplied in the input. For a new dynamic hypothesis (action=create), output an empty string; the harness assigns its identity.')
    action: Literal['create','update','reinforce','refute','hold'] = 'update'
    hypothesis_card_id: str = Field(default='', max_length=120, description='For action=create output an empty string. For update/reinforce/refute/hold copy the supplied stable card ID exactly. Never invent an ID.')
    title: str = Field(default='', max_length=240)
    card_summary: str = Field(default='', max_length=280)
    change_reason: str = Field(default='', max_length=1000)
    basis: Literal['positive_evidence','absence'] = 'positive_evidence'
    judgment: Literal['확정', '유력', '미확인']
    reasoning: str = Field(max_length=1500)
    supporting_evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    refuting_evidence_ids: list[str] = Field(default_factory=list, max_length=10)
    remaining_checks: list[str] = Field(default_factory=list, max_length=6)
    scenario_assessment: ScenarioAssessment | None = None
    explanation_links: list[ExplanationLinkProposal] = Field(default_factory=list, max_length=4,
        description='Optional explicit explanation links. Copy claim_ref and target_scope exactly from accepted_claim_refs in this input, then state how the proposition explains this hypothesis. [] is valid when no accepted claim is supplied; never infer a relationship merely from shared source IDs or invent a claim/version/scope. This grants no test execution or judgment authority.')


class InvestigationPlan(Strict):
    summary: str = Field(max_length=4000)
    tool_calls: list[PlannedTool] = Field(default_factory=list, max_length=4)
    claims: list[Candidate] = Field(default_factory=list, max_length=5)
    remaining_questions: list[str] = Field(default_factory=list, max_length=10)
    hypotheses: list[HypothesisAssessment] = Field(default_factory=list, max_length=3)
    question_updates: list['QuestionUpdate'] = Field(default_factory=list, max_length=4)


class InvestigationPlanV2(InvestigationPlan):
    tool_calls: list[PlannedToolV2] = Field(default_factory=list,max_length=4)


class QuestionUpdate(Strict):
    question_id: str = Field(min_length=1, max_length=100)
    status: Literal['open','held','scoped_answered']
    reason: str = Field(min_length=1, max_length=700)
    observation_ids: list[str] = Field(default_factory=list, max_length=8)


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
