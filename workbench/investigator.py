"""Proposal-only investigator boundary. No controller/store/worker is exposed.

An external runtime is deliberately NOT registered. A future adapter must return
the same structured proposals to existing Frontier validation/admission gates;
it cannot own a second scheduler, mutate the ledger or invoke a shell here.
"""
from dataclasses import dataclass
import hashlib
import json
from typing import Protocol
from .models import ProviderConfig
from .procedures import identity
from .review_context import serialize

VERSION = 'investigator-proposals-1'
TOOLS = {'linux': ('search','read_file','read_source','static_file','archive_list','correlate'),
         'windows': ('search','read_source','correlate')}
ROLES = ('investigator','judgment','synthesis','falsifier')


@dataclass(frozen=True)
class InvestigatorRequest:
    question: str
    evidence_json: str
    role: str


class Investigator(Protocol):
    def propose(self, request: InvestigatorRequest) -> tuple[dict, dict]: ...


class NativeInvestigator:
    def __init__(self, config, provider_factory, *, attempt=None, emit=None, compiled_request=None):
        self.config = ProviderConfig.model_validate(config).model_dump()
        self.provider_factory = provider_factory
        self.attempt=attempt
        self.emit=emit
        self.compiled_request=compiled_request

    def propose(self, request):
        if request.role not in ROLES:
            raise ValueError('지원하지 않는 조사자 역할입니다.')
        pack = json.loads(request.evidence_json)
        platform = pack.get('target_os','linux')
        if platform not in TOOLS:
            raise ValueError('지원하지 않는 조사 대상 OS입니다.')
        # Availability is controller-owned. Keep the exact persisted input pack
        # unchanged; enforce the platform allowlist independently of model text.
        if set(pack.get('available_tools',TOOLS[platform])) - set(TOOLS[platform]):
            raise ValueError('조사 도구 목록이 대상 OS 범위를 벗어났습니다.')
        request_hash=hashlib.sha256(serialize({'question':request.question,'pack':pack,'role':request.role}).encode()).hexdigest()
        selection=pack.get('selection_audit')
        provider=self.provider_factory(self.config)
        binder=getattr(provider,'bind_lifecycle',None)
        if self.emit is not None and callable(binder):binder(self.attempt,self.emit)
        compiled_binder=getattr(provider,'bind_compiled_request',None)
        if self.compiled_request is not None and callable(compiled_binder):compiled_binder(self.compiled_request)
        # Legacy adapters/mocks remain usable. Without an explicit callback we
        # cannot claim dispatch, response receipt or model generation occurred.
        output, receipt = provider.generate(request.question, pack, role=request.role)
        # Detailed schema/citation/assessment gates remain in Provider and the
        # owning controller phases. This transport gate never executes a proposal.
        for call in output.get('tool_calls',[]) + output.get('next_checks',[]):
            if call.get('tool') not in TOOLS[platform]:
                raise ValueError('조사 대상에서 허용하지 않는 도구 제안입니다.')
        receipt = dict(receipt)
        receipt['investigator_contract'] = {'version':VERSION, 'adapter':'native',
            'request_sha256':request_hash,
            'strategy':self.config['investigation_strategy'],
            'available_tools':list(TOOLS[platform]),
            'procedures':identity(self.config['investigation_strategy']),
            'authority':'proposals_only; Frontier executes, validates and records'}
        if selection is not None:receipt['selection_audit']=selection
        return output, receipt


def consult(config, question, pack, role, provider_factory=None, *, attempt=None, emit=None, compiled_request=None):
    if config.get('investigator','native') != 'native':
        raise ValueError('검증·등록되지 않은 외부 조사자는 실행할 수 없습니다.')
    if provider_factory is None:
        from .provider import Provider
        provider_factory = Provider
    # Serialization severs mutable references into controller-owned state.
    request = InvestigatorRequest(question, serialize(pack), role)
    return NativeInvestigator(config, provider_factory,attempt=attempt,emit=emit,compiled_request=compiled_request).propose(request)
