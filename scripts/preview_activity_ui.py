"""Isolated synthetic UI preview. No model, worker, or real case is executed."""
import sys
import sqlite3
import argparse
from contextlib import asynccontextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.api import create_app
import uvicorn

parser=argparse.ArgumentParser()
parser.add_argument('--reuse',action='store_true',help='Reopen this isolated synthetic preview only')
args=parser.parse_args()
root=Path('artifacts/ui-activity-qa-20260928/data')
if root.exists() and not args.reuse:raise SystemExit('Preview exists; use --reuse to preserve its fixture.')
if not root.exists():
    if args.reuse:raise SystemExit('Preview does not exist.')
    root.mkdir(parents=True)
    source=Path('artifacts/ui-qa-20260928/data/case.sqlite3')
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as src, sqlite3.connect(root/'case.sqlite3') as dst:
        src.backup(dst)
app=create_app(root,start_worker=False);s=app.state.controller.store
cid=s.list('case')[0]['id'];e=s.list('evidence',cid)[0]
if not args.reuse:
    s.update(cid,name='UI 미리보기 · 합성 작업 이력',investigation_stage='다음 조사 계획 · UI 검증용')
    t=s.add('task',cid,evidence_id=e['id'],action='linux_investigate',label='AI 추가 탐색',status='queued',attempts=1,
            started_at=(datetime.now(timezone.utc)-timedelta(minutes=15)).isoformat())
    base=datetime.now(timezone.utc)-timedelta(minutes=12)
    for n in range(58):
        status='failed' if n==56 else 'partial' if n==57 else 'covered'
        s.add('investigation_job',cid,task_id=t['id'],status='ingested',result_status=status,
              request={'tool':'read_file' if n%2 else 'search','path':f'/synthetic/log/source-{n}.log',
                       'query':'인증 기록' if n%2==0 else ''},
              dispatched_at=(base+timedelta(seconds=n*10)).isoformat(),ended_at=(base+timedelta(seconds=n*10+8)).isoformat(),
              error='합성 오류: 원문 일부 구간을 읽지 못했습니다.' if status=='failed' else None)
    s.add('model_reservation',cid,task_id=t['id'],purpose='plan',status='reserved',target='새 단서와 후속 검사 우선순위')
original_lifespan=app.router.lifespan_context
@asynccontextmanager
async def fixture_lifespan(app):
    async with original_lifespan(app):
        s.update(cid,status='running')
        yield
app.router.lifespan_context=fixture_lifespan
uvicorn.run(app,host='127.0.0.1',port=8780,log_level='warning')
