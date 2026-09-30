"""Bound infrastructure retries at case scope, never by evidence dossier.

Input reservations remain in the lifetime budget and audit trail, even when
delivery is unknown. Only the per-content validation counter is restored.
"""
from datetime import datetime, timedelta, timezone
from contextlib import contextmanager, nullcontext
import httpx
from .provider import ModelServiceError

RETRY_DELAYS=(30,120)


@contextmanager
def transaction(store):
    # Callers can atomically restore their own checkpoint in the same tx.
    with store.lock:
        with (nullcontext() if store.db.in_transaction else store.tx()):
            yield


def unavailable(error):
    return isinstance(error,(ModelServiceError,httpx.TransportError))


def states(case):
    result=dict(case.get('model_wait_by_transport') or {})
    legacy=case.get('model_wait')
    if legacy:result[legacy.get('transport_identity') or 'unidentified']=legacy
    return result


def waiting(store,cid):
    return any(state.get('circuit_open',False) or datetime.now(timezone.utc)<datetime.fromisoformat(state['retry_at'])
               for state in states(store.get(cid)).values())


def defer(store,cid,task,error,**context):
    with transaction(store):
        case=store.get(cid)
        metadata=getattr(error,'metadata',{})
        identity=metadata.get('transport_identity')
        by_transport=states(case)
        key=identity or 'unidentified'
        previous=by_transport.get(key,{})
        failures=previous.get('consecutive_failures',0)+1
        retryable=metadata.get('retryable',True)
        circuit_open=not retryable or failures>len(RETRY_DELAYS)
        retry_at=None if circuit_open else (datetime.now(timezone.utc)+timedelta(seconds=RETRY_DELAYS[failures-1])).isoformat()
        receipt=store.add('receipt',cid,task_id=task['id'],evidence_id=task.get('evidence_id'),
            receipt_type='model_service_unavailable',failure_category=getattr(error,'category','model_service_unavailable'),
            error=str(error),model_metadata=getattr(error,'metadata',{}),
            consecutive_failures=failures,retry_at=retry_at,circuit_open=circuit_open,**context)
        state={'task_id':task['id'],'consecutive_failures':failures,'retry_at':retry_at,
               'circuit_open':circuit_open,'receipt_id':receipt['id'],
               'transport_identity':identity,'model_slot':metadata.get('model_slot'),
               'role':metadata.get('role'),'retryable':retryable,
               'failure_category':getattr(error,'category','model_service_unavailable'),
               'delivery_state':metadata.get('delivery_state','unknown')}
        by_transport[key]=state
        updates={'model_wait':state,'model_wait_by_transport':by_transport,'investigation_stage':
            'AI 인증·설정·모델 식별 확인 필요 · 조사 일시정지 · 기존 판단 보존' if not retryable else
            'AI 연결 장애 반복 · 조사 일시정지 · 기존 판단 보존' if circuit_open else 'AI 연결 복구 대기 · 동일 작업 재시도 예정'}
        if circuit_open:
            updates['status']='paused'
            updates['end_reason']='model_configuration' if not retryable else 'model_service_circuit'
            updates['report_snapshot_request']={'token':receipt['id'],
                'reason':updates['end_reason']}
        store.update(cid,**updates)
        return receipt


def recovered(store,cid,transport_identity=None):
    with transaction(store):
        by_transport=states(store.get(cid))
        key=transport_identity or 'unidentified'
        state=by_transport.get(key)
        # Another healthy endpoint/parallel role must not erase this outage.
        if state and state.get('transport_identity')==transport_identity:
            store.audit(cid,'model_service_recovered',transport_identity=transport_identity)
            del by_transport[key]
            remaining=list(by_transport.values())
            store.update(cid,model_wait=remaining[-1] if remaining else None,model_wait_by_transport=by_transport)
