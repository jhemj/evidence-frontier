"""Isolated synthetic temporal UI preview; no model or worker execution."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.api import create_app
import uvicorn

parser=argparse.ArgumentParser()
parser.add_argument('--reuse',action='store_true')
args=parser.parse_args()
root=Path('artifacts/ui-temporal-qa-20260928/data')
if root.exists() and not args.reuse:raise SystemExit('Preview exists; --reuse preserves its fixture.')
if args.reuse and not root.exists():raise SystemExit('Preview does not exist.')
app=create_app(root,start_worker=False);c=app.state.controller;s=c.store
if not args.reuse:
    if s.list('case'):raise SystemExit('Refusing to alter an existing case.')
    cid=c.create('시각 검증 · 합성 사건','','standard')['id']
    e=s.add('evidence',cid,path='synthetic.ndjson',name='synthetic.ndjson',size=1000,signature='synthetic',connected=True)
    t=s.add('task',cid,evidence_id=e['id'],label='단서 검토',action='ai_judgment',status='partial',retry_generation=0)
    rows=[
        ('관리 계정 원격 접속 기록','linux_authentication','2026-06-23T01:12:00+09:00',{},'인증 기록의 명시된 시각입니다. 작업 승인 여부는 미확인입니다.'),
        ('예약 작업 호출 시각 — 연도 대조 필요','linux_cron_call','2026-06-22T01:19:00+09:00',{'time_basis':'year inferred from rotation filename'},'호출 기록에 연도가 없어 회전 파일명에서 연도를 추정했습니다. 실행 완료는 미확인입니다.'),
        ('라이브러리 변경 시각 — 실제 실행은 미확인','filesystem_entry',None,{'ctime_ns':1782104669000000001,'mtime_epoch':1747132834,'crtime_epoch':1782104668},'메타데이터 변경은 2026년, 내용 수정은 2025년으로 기록돼 있습니다. 복사·설치 이력을 대조해야 합니다.'),
        ('설정 파일의 등록 시점 추가 확인','linux_configuration',None,{},'설정 내용만 확보했습니다. 파일 시각과 호출 로그를 추가 확인해야 합니다.'),
    ]
    for i,(title,typ,stamp,extra,summary) in enumerate(rows):
        o=s.add('observation',cid,evidence_id=e['id'],type=typ,timestamp=stamp,source_location=f'synthetic:{i}',
                fields={'path':f'/synthetic/source-{i}','partition_offset':0,'inode':100+i,
                        'excerpt':'UI 테스트용 합성 근거 — 실제 증거 아님','time_basis':'explicit synthetic UTC offset',**extra})
        d=s.add('dossier',cid,evidence_id=e['id'],task_id=t['id'],generation=0,title=title,baseline=False,status='reviewed',observation_ids=[o['id']])
        s.update(d['id'],finding={'dossier_id':d['id'],'title':title,'card_summary':summary,'judgment':'미확인',
            'reason':summary,'timeline_role':'핵심','observation_ids':[o['id']],'remaining_checks':['합성 자료 — 원본 조사 아님']})
    s.update(cid,status='paused',investigation_stage='UI 검증 · 실제 조사가 아닙니다')
uvicorn.run(app,host='127.0.0.1',port=8782,log_level='warning')
