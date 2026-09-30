import ipaddress
import hashlib
import json
import os
import socket
import time
from urllib.parse import urlsplit
import httpx
from .models import ProviderConfig
from .review_context import serialize


class ModelOutputError(ValueError):
    def __init__(self, message, raw_output, category, metadata=None):
        super().__init__(message)
        self.raw_output=raw_output[:100000] if isinstance(raw_output,str) else None
        self.category=category
        self.metadata=dict(metadata or {})


class ModelServiceError(ValueError):
    """Infrastructure failure, not an invalid evidence interpretation."""
    category='model_service_unavailable'

    def __init__(self, message, *, transport, operation, request_attempted=False,
                 category='model_service_unavailable', phase=None, retryable=True,
                 delivery_state=None):
        super().__init__(message)
        self.category=category
        self.metadata={'transport':transport,'operation':operation,
                       'request_attempted':request_attempted,'failure_category':category,
                       'phase':phase or ('generation' if request_attempted else 'preflight'),
                       'retryable':retryable,
                       'delivery_state':delivery_state or ('unknown' if request_attempted else 'not_sent')}


def validate_url(url, trusted_lan=False):
    u=urlsplit(url)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError('인증 정보 없는 http(s) 모델 URL을 입력하세요.')
    # Docker's well-known host bridge is a local transport; never permit public providers by default.
    if u.hostname=='host.docker.internal':
        return url.rstrip('/')
    addresses={r[4][0] for r in socket.getaddrinfo(u.hostname,u.port or 80,type=socket.SOCK_STREAM)}
    for address in addresses:
        ip=ipaddress.ip_address(address)
        if ip.is_loopback:
            continue
        if trusted_lan and ip.is_private and not ip.is_link_local and not ip.is_unspecified and not ip.is_multicast:
            continue
        raise ValueError('로컬 또는 명시적으로 신뢰한 사설망 모델만 연결할 수 있습니다.')
    return url.rstrip('/')


class Provider:
    def __init__(self,config):
        config=ProviderConfig.model_validate(config).model_dump()
        self.pool_config=config
        self.slot='primary'
        self._connect(config)

    def _connect(self,config):
        self.config=config
        self.base=validate_url(config['base_url'],config.get('trusted_lan',False))
        relay=os.getenv('MODEL_RELAY_URL') if self.slot=='primary' else None
        if relay:
            if self.base!=os.getenv('MODEL_UPSTREAM_URL','http://host.docker.internal:11434').rstrip('/'):
                raise ValueError('모델 주소가 배포 시 고정한 로컬 모델 주소와 다릅니다.')
            self.base=relay.rstrip('/')
        timeout=max(30,min(600,int(os.getenv('MODEL_TIMEOUT','300'))))
        key=os.getenv('MODEL_API_KEY' if self.slot=='primary' else 'MODEL_SECONDARY_API_KEY')
        self.client=httpx.Client(timeout=httpx.Timeout(timeout,connect=10),follow_redirects=False,trust_env=False,headers={'Authorization':'Bearer '+key} if key else {})

    def select_role(self,role):
        slot=self.pool_config['role_routes'].get(role,'primary')
        if slot==self.slot:return
        self.client.close()
        self.slot=slot
        selected=dict(self.pool_config)
        if slot=='secondary':selected.update(self.pool_config['secondary'])
        self._connect(selected)

    def transport_identity(self):
        # Endpoint identity is metadata, not an incident-dependent routing rule.
        return hashlib.sha256(json.dumps([self.config['protocol'],self.base,self.config['model']],sort_keys=True).encode()).hexdigest()

    def bind_lifecycle(self, attempt, emit):
        """Attach caller-owned telemetry; no Store or execution authority."""
        self._lifecycle_attempt=attempt
        self._lifecycle_emit=emit
        return self

    def bind_compiled_request(self,compiled):
        self._compiled_request=compiled
        return self

    def _emit(self, phase, **metadata):
        callback=getattr(self,'_lifecycle_emit',None)
        if callback is not None:
            from .request_lifecycle import safe_metadata
            controls={'delivery_state':metadata['delivery_state']} if 'delivery_state' in metadata else {}
            callback(phase,**controls,**safe_metadata(metadata))

    def response(self,path,payload):
        self._operation=path
        generating=path in ('/api/chat','/chat/completions')
        compiled=getattr(self,'_active_compiled',None) if generating else None
        if compiled is not None and (path!=compiled.path or serialize(payload)!=compiled.payload_json):
            # A local envelope mismatch is not a dispatch or delivery attempt.
            raise ValueError('Actual transport envelope differs from the compiled budget envelope')
        def dispatched():
            if generating:
                self._request_attempted=True
                self._emit('dispatch_attempted',delivery_state='attempted',request_attempted=True)
                # Nonstreaming HTTP entry is not observation of GPU generation.
                self._emit('response_waiting',delivery_state='attempted')
        broker_path=os.getenv('FRONTIER_RESOURCE_BROKER')
        if generating and broker_path and self.config['protocol']=='ollama':
            from .decision_runtime import ResourceBroker
            import uuid
            broker=ResourceBroker(broker_path,os.environ['FRONTIER_RESOURCE_GROUP'])
            try:
                with broker.lease(uuid.uuid4().hex) as lease:
                    dispatched();lease.dispatched=True
                    if compiled is not None:
                        r=self.client.post(self.base+path,content=compiled.payload_json.encode(),headers={'Content-Type':'application/json'})
                    else:r=self.client.post(self.base+path,json=payload)
                    lease.received({'http_status':r.status_code,'response_sha256':hashlib.sha256(r.content).hexdigest(),
                        'delivery_state':'response_received'})
            except ValueError as ex:
                from .decision_runtime import ResourceUnavailable
                if not isinstance(ex,ResourceUnavailable):raise
                raise ModelServiceError('공유 모델 자원 대기 또는 미확인 실행 확인 필요',
                    transport='ollama',operation=path,request_attempted=False,
                    category='model_resource_busy',phase='resource_wait',retryable=True,
                    delivery_state='not_sent') from ex
        elif compiled is not None:
            dispatched()
            r=self.client.post(self.base+path,content=compiled.payload_json.encode(),headers={'Content-Type':'application/json'})
        else:
            dispatched()
            r=self.client.post(self.base+path,json=payload)
        if generating:
            self._emit('response_received',delivery_state='response_received',
                http_status=r.status_code,response_bytes=len(r.content),
                response_sha256=hashlib.sha256(r.content).hexdigest())
        self.check_http_status(r,path)
        data=self.response_json(r,path)
        if generating:
            usage=data.get('usage',{}) if self.config['protocol']!='ollama' else {
                k:data.get(k) for k in ('prompt_eval_count','eval_count','total_duration',
                    'load_duration','prompt_eval_duration','eval_duration')}
            self._emit('response_received',delivery_state='response_received',usage=usage)
        return data

    def response_json(self,response,operation):
        try:
            value=response.json()
            if not isinstance(value,dict):raise ValueError('response envelope must be an object')
            return value
        except ValueError as error:
            raise ModelServiceError('모델 서버의 응답 프로토콜이 올바르지 않습니다.',
                transport=self.config['protocol'],operation=operation,
                request_attempted=getattr(self,'_request_attempted',False),
                category='model_transport_protocol',retryable=False,
                delivery_state='response_received') from error

    def check_http_status(self,response,operation):
        """A server rejection is not a model's evidence interpretation.

        Do not persist response bodies: authentication/configuration diagnostics
        can contain credentials or deployment details. No automatic fallback.
        """
        if 200<=response.status_code<300:return
        status=response.status_code
        transient=status==429 or status>=500
        category=('model_authentication' if status in (401,403) else
                  'model_service_unavailable' if transient else 'model_request_configuration')
        failure=ModelServiceError(f'모델 서버 응답 오류 ({status})',
            transport=self.config['protocol'],operation=operation,
            request_attempted=getattr(self,'_request_attempted',False),
            category=category,phase='preflight' if operation=='/api/tags' else 'generation',
            retryable=transient,delivery_state='response_received')
        failure.metadata['http_status']=status
        raise failure

    def models(self):
        try:
            r=self.client.get(self.base+('/api/tags' if self.config['protocol']=='ollama' else '/models'))
            r.raise_for_status();data=r.json()
            return [x['name'] for x in data.get('models',[])] if self.config['protocol']=='ollama' else [x['id'] for x in data.get('data',[])]
        finally:
            self.client.close()

    def verify_model_identity(self,model):
        """Check the identity of the transport that will actually generate."""
        expected=self.config.get('model_digest') or (os.getenv('FRONTIER_MODEL_DIGEST','unverified') if self.slot=='primary' else 'unverified')
        if expected!='unverified' and model!=self.config['model']:
            raise ModelServiceError('digest를 고정한 검증 배포에서는 반증 모델도 기본 모델과 같아야 합니다.',
                transport=self.config['protocol'],operation='model-identity',category='model_identity',retryable=False)
        if expected!='unverified' and self.config['protocol']=='ollama':
            self._operation='/api/tags'
            response=self.client.get(self.base+'/api/tags')
            self.check_http_status(response,'/api/tags')
            actual=next((m.get('digest') for m in self.response_json(response,'/api/tags').get('models',[]) if m.get('name')==model),None)
            if actual!=expected:
                raise ModelServiceError('로컬 모델 digest가 조사 시작 시 고정한 값과 다릅니다. 새 사건에서 버전을 확인하세요.',
                    transport=self.config['protocol'],operation='model-identity',category='model_identity',retryable=False)
        return {'transport':self.config['protocol'],'model':model,'expected_digest':expected}

    def generate(self,question,pack,role='analyst',*,attempt=None,emit=None,compiled_request=None):
        if emit is not None:self.bind_lifecycle(attempt,emit)
        if compiled_request is not None:self.bind_compiled_request(compiled_request)
        self._operation='prepare'
        self._request_attempted=False
        try:
            self.select_role(role)
            self._emit('input_preparing',role=role,model_slot=self.slot,
                transport_identity=self.transport_identity())
            from .model_concurrency import lease
            self._emit('resource_queued',transport_identity=self.transport_identity())
            with lease(self.transport_identity()):
                self._emit('resource_acquired',transport_identity=self.transport_identity())
                return self._generate(question,pack,role)
        except httpx.TransportError as ex:
            failure=ModelServiceError(str(ex),transport=self.config['protocol'],
                operation=self._operation,request_attempted=self._request_attempted)
            failure.metadata.update(model_slot=self.slot,transport_identity=self.transport_identity(),role=role)
            if failure.metadata['delivery_state']=='unknown':
                self._emit('delivery_unknown',delivery_state='unknown')
            self._emit('failed',failure_category=failure.category,retryable=failure.metadata['retryable'])
            raise failure from ex
        except ModelServiceError as ex:
            ex.metadata.update(model_slot=self.slot,transport_identity=self.transport_identity(),role=role)
            self._emit('failed',failure_category=ex.category,retryable=ex.metadata['retryable'])
            raise
        except ValueError as ex:
            self._emit('failed',failure_category=getattr(ex,'category','input_or_output_contract'))
            raise
        finally:
            # Preflight errors must close their client, too.
            self.client.close()
            self._lifecycle_emit=None
            self._compiled_request=None
            self._active_compiled=None

    def _generate(self,question,pack,role='analyst'):
        from .request_compiler import compile_request
        compiled=getattr(self,'_compiled_request',None) or compile_request(self.config,question,pack,role)
        if not compiled.matches(self.config,question,pack,role):
            raise ValueError('Prepared request does not match its model, role or exact input scope')
        compiled.assert_fits()
        self._active_compiled=compiled
        schema,model=compiled.schema,compiled.model
        references=compiled.references
        model_pack=compiled.model_pack
        messages=compiled.messages
        text_projection=compiled.text_projection
        from .model_tables import MANIFEST
        self._emit('availability_check',model_sha256=hashlib.sha256(model.encode()).hexdigest())
        identity_check=self.verify_model_identity(model)
        self._emit('availability_verified',identity_verified=bool(
            identity_check.get('expected_digest') and identity_check['expected_digest']!='unverified'))
        start=time.monotonic()
        from .prompt_budget import measured
        prompt_budget=compiled.budget
        # These are exact input-observation versions, not a claim of source
        # independence or complete retained-file coverage. Decode only the
        # existing lossless transport before making this metadata manifest.
        if getattr(self,'_lifecycle_emit',None) is not None:
            from .review_stream import resolved
            presented=resolved(pack)
            input_manifest={'source_refs':[{'kind':'observation','id':o['id'],
                'version':hashlib.sha256(json.dumps(o,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()}
                for o in presented.get('observations',[]) if o.get('id')],
                'owner_ids':[d['id'] for d in presented.get('required_dossiers',[]) if d.get('id')],
                'test_ids':[c['contract_id'] for c in presented.get('executed_checks',[]) if c.get('contract_id')]}
            self._emit('input_ready',request_sha256=compiled.identity['request_body_sha256'],
                message_count=len(messages),prompt_characters=sum(len(m['content']) for m in messages),
                observation_count=len(presented.get('observations',[])),
                dossier_count=len(presented.get('required_dossiers',[])),
                check_count=len(presented.get('executed_checks',[])),prompt_budget=prompt_budget,
                input_manifest=input_manifest)
        settings=compiled.settings
        try:
            if self.config['protocol']=='ollama':
                # Constrain structure at generation as well as validation.
                # Legacy bridges may explicitly select JSON compatibility;
                # never silently downgrade after a failed schema request.
                payload=compiled.payload
                mode=self.config['think']
                if mode!='auto':
                    value={'on':True,'off':False}.get(mode,mode)
                    if mode!='off':
                        metadata=self.response('/api/show',{'model':model})
                        thinking=metadata.get('thinking')
                        advertised=thinking.get('values',[]) if isinstance(thinking,dict) else []
                        if not isinstance(advertised,list):advertised=[]
                        supported=any(type(item) is type(value) and item==value for item in advertised)
                        # Older Ollama advertises only a thinking capability.
                        if not advertised and mode=='on':supported='thinking' in (metadata.get('capabilities') or [])
                        if not supported:raise ValueError('선택한 추론 모드를 모델 서버가 지원한다고 확인하지 못했습니다. 끄기 또는 모델 기본값을 선택하세요.')
                        settings['advertised_thinking_values']=advertised
                    payload['think']=value
                data=self.response('/api/chat',payload)
                content=data.get('message',{}).get('content')
                usage={k:data.get(k) for k in ('prompt_eval_count','eval_count','total_duration','load_duration','prompt_eval_duration','eval_duration')}
                prompt_budget=measured(prompt_budget,usage)
                metadata={'usage':usage,'generation_settings':settings,
                          'prompt_characters':sum(len(m['content']) for m in messages),
                          'elapsed_seconds':round(time.monotonic()-start,3),'prompt_budget':prompt_budget,
                          'compiled_request':compiled.identity,
                          'reference_projection':references.receipt(),
                          'table_projection':model_pack.get(MANIFEST),'text_projection':text_projection}
                if prompt_budget['actual_headroom'] is not None and prompt_budget['actual_headroom']<0:
                    raise ModelOutputError('실제 입력 토큰이 출력 예약을 침범했습니다. 질문/원문 범위를 분할해야 합니다.',content,'input_context_pressure',metadata)
                if data.get('done_reason')=='length': raise ModelOutputError('모델 출력이 잘렸습니다.',content,'output_budget',metadata)
                content=data['message']['content']
            else:
                payload=compiled.payload
                data=self.response('/chat/completions',payload)
                choice=data['choices'][0]
                content=choice.get('message',{}).get('content')
                usage=data.get('usage',{})
                prompt_budget=measured(prompt_budget,usage)
                metadata={'usage':usage,'generation_settings':settings,
                          'prompt_characters':sum(len(m['content']) for m in messages),
                          'elapsed_seconds':round(time.monotonic()-start,3),'prompt_budget':prompt_budget,
                          'compiled_request':compiled.identity,
                          'reference_projection':references.receipt(),
                          'table_projection':model_pack.get(MANIFEST),'text_projection':text_projection}
                if choice.get('finish_reason')!='stop': raise ModelOutputError('모델이 정상적으로 응답하지 않았습니다.',content,'output_budget',metadata)
                content=choice['message']['content']
            self._emit('validating',validation_stage='structure_and_references')
            try:parsed=schema.model_validate_json(content)
            except ValueError as ex:raise ModelOutputError(str(ex),content,'output_schema',metadata) from ex
            try:output=references.decode(parsed.model_dump())
            except ValueError as ex:raise ModelOutputError(str(ex),content,'output_reference',metadata) from ex
            lifecycle={'request_attempt_id':self._lifecycle_attempt} if getattr(self,'_lifecycle_emit',None) is not None else {}
            return output,{'model':model,'role':role,'usage':usage,'elapsed_seconds':round(time.monotonic()-start,3),
                **lifecycle,
                'model_slot':self.slot,'transport_identity':self.transport_identity(),
                'identity_check':identity_check,
                'generation_settings':settings,'prompt_characters':sum(len(m['content']) for m in messages),
                'output_characters':len(content),'prompt_budget':prompt_budget,'compiled_request':compiled.identity,'procedures':compiled.procedures,
                'reference_projection':references.receipt(),'table_projection':model_pack.get(MANIFEST),'text_projection':text_projection,
                'model_reference_output':parsed.model_dump(),
                'output':output}
        finally:
            self.client.close()
