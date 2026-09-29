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


def waiting(store,cid):
    state=store.get(cid).get('model_wait') or {}
    if not state:return False
    return state.get('circuit_open',False) or datetime.now(timezone.utc)<datetime.fromisoformat(state['retry_at'])


def defer(store,cid,task,error,**context):
    with transaction(store):
        case=store.get(cid)
        previous=case.get('model_wait') or {}
        metadata=getattr(error,'metadata',{})
        identity=metadata.get('transport_identity')
        if previous.get('transport_identity')!=identity:previous={}
        failures=previous.get('consecutive_failures',0)+1
        circuit_open=failures>len(RETRY_DELAYS)
        retry_at=None if circuit_open else (datetime.now(timezone.utc)+timedelta(seconds=RETRY_DELAYS[failures-1])).isoformat()
        receipt=store.add('receipt',cid,task_id=task['id'],evidence_id=task.get('evidence_id'),
            receipt_type='model_service_unavailable',failure_category='model_service_unavailable',
            error=str(error),model_metadata=getattr(error,'metadata',{}),
            consecutive_failures=failures,retry_at=retry_at,circuit_open=circuit_open,**context)
        state={'task_id':task['id'],'consecutive_failures':failures,'retry_at':retry_at,
               'circuit_open':circuit_open,'receipt_id':receipt['id'],
               'transport_identity':identity,'model_slot':metadata.get('model_slot'),
               'role':metadata.get('role')}
        updates={'model_wait':state,'investigation_stage':
            'AI 연결 장애 반복 · 조사 일시정지 · 기존 판단 보존' if circuit_open else 'AI 연결 복구 대기 · 동일 작업 재시도 예정'}
        if circuit_open:updates['status']='paused'
        store.update(cid,**updates)
        return receipt


def recovered(store,cid):
    if store.get(cid).get('model_wait'):
        with transaction(store):
            store.audit(cid,'model_service_recovered')
            store.update(cid,model_wait=None)
