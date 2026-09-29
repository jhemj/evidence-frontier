"""Synthetic-only cockpit QA. No worker or model is started."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.api import create_app
from workbench.case_synthesis import source_revision
import uvicorn

p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--port',type=int,default=8820)
a=p.parse_args()
if a.root.exists():raise SystemExit('Refusing to replace an existing preview.')
app=create_app(a.root,start_worker=False);c=app.state.controller;s=c.store
cid=c.create('UI 검증 · 합성 침해 판단판','실제 사건이 아닌 화면 검증용 합성 자료','standard')['id']
e=s.add('evidence',cid,path='synthetic.ndjson',name='합성 증거',size=1000,signature='synthetic',connected=True)
t=s.add('task',cid,evidence_id=e['id'],label='합성 검토',action='ai_judgment',status='partial',retry_generation=0)
for n,(verdict,title) in enumerate([('probable','권한 없는 외부 접속 가능성'),('refuted','압축 파일이 전송되었다는 가설')]):
    o=s.add('observation',cid,evidence_id=e['id'],type='synthetic',timestamp='2026-09-29T01:00:00Z',
        source_location=f'synthetic:{n}',fields={'path':f'/synthetic/{n}','excerpt':'UI 테스트용 합성 근거 — 실제 증거 아님'})
    d=s.add('dossier',cid,evidence_id=e['id'],task_id=t['id'],generation=0,title=title,status='reviewed',observation_ids=[o['id']])
    f={'dossier_id':d['id'],'title':title,'card_summary':'합성 UI 시나리오입니다. 실제 침해 판단이 아닙니다.',
       'judgment':'유력' if verdict=='probable' else '미확인','reason':'화면 검증용 합성 판단','observation_ids':[o['id']],
       'incident_relevance':{'level':'direct','reason':'합성 조사 질문과 연결','observation_ids':[o['id']]}}
    s.update(d['id'],finding=f)
    support=[o['id']] if verdict=='probable' else [];refute=[o['id']] if verdict=='refuted' else []
    s.add('case_synthesis',cid,task_id=t['id'],evidence_id=e['id'],generation=0,hypothesis_id=f'h{n}',
        question=title,number=n+1,status='reviewed',finding=f,supporting_evidence_ids=support,refuting_evidence_ids=refute,
        scope={'presented_observations':1,'relevant_observations':1},selection_is_partial=False,
        source_ids=[o['id']],source_revision=source_revision([o]),incident_assessment={
            'verdict':verdict,'scope':title,'rationale':'합성 인증 기록과 승인 내역을 대조한 시나리오입니다.',
            'summary':'인증 기록에서 외부 주소의 계정 접속을 확인했습니다. 승인된 운영 작업인지 아직 확인되지 않아 침해가 유력한 상태입니다. 접속 후 실행 기록과 작업 승인 이력을 추가로 조사해야 합니다.',
            'supporting_evidence_ids':support,'refuting_evidence_ids':refute})
from workbench.hypothesis_ledger import apply
plan=s.add('investigation_plan',cid,task_id=t['id'],generation=0)
ob=s.list('observation',cid)
for i,title in enumerate(['탈취한 계정으로 외부에서 접속했을 가능성','승인된 유지보수 작업으로 접속했을 가능성',
                          '예약 작업으로 실행이 반복되었을 가능성','운영 자동화가 파일을 변경했을 가능성',
                          '외부 전송이 완료되었을 가능성','접속 기록의 시간이 잘못 해석되었을 가능성']):
    apply(s,cid,e['id'],{'action':'create','title':title,'card_summary':'화면 검증용 합성 시나리오입니다. 원문과 운영 승인 여부를 대조해야 합니다.',
        'judgment':'유력','reasoning':'합성 자료의 상반된 설명 비교','supporting_evidence_ids':[ob[0]['id']],
        'scenario_assessment':{'comparison_question':'원격 접속은 승인된 작업인가?' if i<2 else '예약 실행의 목적은 무엇인가?' if i==2 else '후속 행위는 무엇을 의미하는가?',
          'evidence_fit':'moderate' if i!=1 else 'limited','ranking_reason':'합성 접속 기록은 확인되지만 운영 승인은 미확인입니다.',
          'alternative_explanation':'승인된 유지보수일 수 있습니다.','next_check':'승인 이력과 접속 주체를 대조합니다.',
          'investigation_priority':'high' if i==1 else 'normal','priority_reason':'승인 이력 하나로 설명을 구분할 수 있습니다.'}},
        {o['id'] for o in ob},task_id=t['id'],generation=0,source_plan_id=plan['id'],proposal_index=i)
s.update(cid,status='paused',investigation_stage='UI 검증 · 실제 조사가 아닙니다')
uvicorn.run(app,host='127.0.0.1',port=a.port,log_level='warning')
