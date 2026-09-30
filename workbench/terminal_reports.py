"""Model-free, durable partial-report finalization after execution stops.

Only explicit new-runtime requests are processed. Historical terminated cases
are never auto-resumed or retroactively rewritten. A scope change blocks
publication rather than mixing late findings into an old closing snapshot.
"""
from pathlib import Path
import os
from .model_availability import transaction
from .store import now

TERMINAL={'complete','quiescent','resource_limit','paused','failed'}


def request(controller,cid,token,reason):
    store=controller.store
    with transaction(store):
        prior=next((r for r in store.list('report_finalization',cid) if r['token']==token),None)
        if prior:return prior
        case=store.get(cid)
        return store.add('report_finalization',cid,token=token,status='pending',
            reason=reason,execution_status=case['status'],epoch_id=case.get('epoch_id'),
            source_revision=store.report_revision(cid),source_binding=case.get('runtime_binding'))


def process(controller,cid):
    """At most one attempt per request; restart can recover an interrupted build."""
    store=controller.store
    with controller.report_finalization_lock:
        with store.tx():
            case=store.get(cid)
            marker=case.get('report_snapshot_request')
            # Pause/circuit requests are captured only after active work settles.
            if marker and case['status']=='paused' and not any(t.get('status')=='running' for t in store.list('task',cid)):
                store.update(cid,report_snapshot_request=None)
                request(controller,cid,marker['token'],marker['reason'])
            pending=next((r for r in store.list('report_finalization',cid) if r['status'] in ('pending','building')),None)
            if not pending:return False
            # Report adoption may precede the finalizer acknowledgement on crash.
            existing=next((r for r in store.list('report',cid) if r.get('finalization_id')==pending['id']),None)
            if existing:
                store.update(pending['id'],status='generated',report_record_id=existing['id'],ended_at=now())
                return True
            if case['status'] not in TERMINAL or case.get('epoch_id')!=pending.get('epoch_id') or store.report_revision(cid)!=pending['source_revision']:
                store.update(pending['id'],status='superseded',error='마감 이후 실행 범위·근거가 변경되어 과거 스냅샷의 보고서를 생성하지 않습니다.',ended_at=now())
                return True
            binding=pending.get('source_binding')
            if binding and binding['fingerprint']!=controller.runtime_binding()['fingerprint']:
                store.update(pending['id'],status='failed',error='동결 실행 소스와 보고서 생성 소스가 다릅니다.',ended_at=now())
                return True
            store.update(pending['id'],status='building',started_at=now())
        try:
            from .controller import ROOT
            from .reporting import build_report
            root=Path(os.getenv('DATA_ROOT',str(ROOT/'data')))/'reports'
            report=build_report(controller,cid,root,expected_revision=pending['source_revision'],finalization_id=pending['id'])
        except Exception as error:
            with store.tx():
                store.update(pending['id'],status='failed',error=str(error),ended_at=now())
                store.audit(cid,'terminal_report_failed',finalization_id=pending['id'],error=str(error))
            return True
        with store.tx():
            store.update(pending['id'],status='generated',report_record_id=report['id'],ended_at=now())
            store.audit(cid,'terminal_report_generated',finalization_id=pending['id'],report_record_id=report['id'])
        return True
