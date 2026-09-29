"""Synthetic UI-only fixture. Never run against a real investigation database."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.api import create_app

root=Path('artifacts/ui-qa-20260928/data')
app=create_app(root,start_worker=False);c=app.state.controller;s=c.store
if s.list('case'):raise SystemExit('UI QA database already seeded; leaving it unchanged')
cid=c.create('UI 검증용 · 합성 사건','','standard')['id']
e=s.add('evidence',cid,path='synthetic.ndjson',name='synthetic.ndjson',size=1000,signature='synthetic',connected=True)
cell=s.add('coverage',cid,evidence_id=e['id'],phase=1,action='ai_judgment',label='단서 검토',status='partial')
t=s.add('task',cid,evidence_id=e['id'],cell_id=cell['id'],label='단서 검토',action='ai_judgment',status='partial',retry_generation=0)
rows=[
    ('관리 계정의 원격 접속 기록','확인','핵심','2026-09-01T01:12:00+09:00','linux_authentication','인증 허용 기록이 있습니다. 작업자의 신원과 승인 여부는 아직 확인하지 못했습니다.'),
    ('예약 명령과 반복 호출의 연관성','유력','핵심','2026-09-01T01:19:00+09:00','linux_cron_call','동일한 대상 경로가 예약 설정과 호출 기록에 나타납니다. 프로그램의 실행 완료 여부는 별도 검증이 필요합니다.'),
    ('외부 연결 시도의 성공 여부','미확인','보조','2026-09-01T01:26:00+09:00','linux_command','명령에 외부 주소가 포함되어 있지만 연결 응답 기록은 없습니다.'),
    ('실행 파일의 출처 확인 필요','미확인','핵심',None,'linux_binary','파일의 기능적 특성만 확인했습니다. 패키지 출처와 실제 실행은 미확인입니다.'),
    ('제품 설명서 안의 실행 예시','확인','범위 설명','2026-09-01T00:00:00+09:00','file_metadata','문서 예시의 문자열입니다. 이 호스트에서 실행한 근거로 사용하지 않습니다.'),
]
for i,(title,judgment,role,stamp,typ,reason) in enumerate(rows):
    o=s.add('observation',cid,evidence_id=e['id'],type=typ,timestamp=stamp,source_location=f'synthetic:{i}',
        fields={'path':f'/synthetic/source-{i}','excerpt':'UI 테스트용 합성 근거 — 실제 증거 아님','time_basis':'explicit synthetic UTC offset'})
    d=s.add('dossier',cid,evidence_id=e['id'],task_id=t['id'],generation=0,title=title,baseline=False,status='reviewed',observation_ids=[o['id']])
    f={'dossier_id':d['id'],'title':title,'judgment':judgment,'reason':reason,'timeline_role':role,
        'observation_ids':[o['id']],'stages':[],'counterevidence_ids':[],
        'alternatives':['승인된 운영 작업일 가능성'],'remaining_checks':['작업 승인 이력과 원문 대조'] if role=='핵심' else []}
    s.update(d['id'],finding=f)
s.update(cid,status='paused',investigation_stage='UI 검증 · 실제 조사가 아닙니다')
print(cid)
