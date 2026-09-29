"""Add explicitly synthetic card transitions to the isolated UI QA fixture only."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.api import create_app
from workbench.hypothesis_ledger import apply
from workbench.card_evolution import assessment_revision

root=Path('artifacts/ui-qa-20260928/data')
app=create_app(root,start_worker=False);s=app.state.controller.store
cases=s.list('case')
if len(cases)!=1 or cases[0]['name']!='UI 검증용 · 합성 사건':raise SystemExit('Not the isolated synthetic UI fixture')
cid=cases[0]['id']
if any(h.get('hypothesis_kind')=='dynamic' for h in s.list('hypothesis',cid)):raise SystemExit('Transitions already seeded; leaving unchanged')
e=s.list('evidence',cid)[0];t=s.list('task',cid)[0]
obs=s.list('observation',cid)
assert e['signature']=='synthetic' and all(o['source_location'].startswith('synthetic:') for o in obs)

def transition(a,name,final=False):
    p=s.add('investigation_plan',cid,task_id=t['id'],generation=0,synthetic=True,name=name)
    result=apply(s,cid,e['id'],a,[o['id'] for o in obs],task_id=t['id'],source_plan_id=p['id'],final=final)
    assert result is not None
    return result

a={'action':'create','title':'예약 명령과 후속 호출 기록이 연결되는가?',
   'card_summary':'예약 호출 기록이 발견되었습니다. 다른 기록과 연결되는지는 검증 중입니다.',
   'judgment':'미확인','reasoning':'합성 자료의 예약 호출을 출발점으로 비교합니다.',
   'supporting_evidence_ids':[obs[1]['id']],'refuting_evidence_ids':[]}
h=transition(a,'synthetic-create')
transition({**a,'action':'reinforce','hypothesis_card_id':h['hypothesis_card_id'],
    'title':'예약 호출과 후속 명령 기록의 연결 정황 강화',
    'card_summary':'예약 호출과 후속 명령 기록이 함께 인용되었습니다. 실제 실행 완료와 악성 목적은 아직 확인되지 않았습니다.',
    'judgment':'유력','supporting_evidence_ids':[obs[1]['id'],obs[2]['id']],
    'change_reason':'후속 명령 기록을 추가해 연결 가능성을 재평가했습니다. 이 연결은 합성 UI 예시입니다.'},'synthetic-reinforce')
b={**a,'title':'문서 속 명령이 호스트 실행 흔적인가?',
   'card_summary':'명령 문자열이 발견되었습니다. 문서 예시인지 실행 기록인지 확인이 필요합니다.',
   'supporting_evidence_ids':[obs[4]['id']]}
h=transition(b,'synthetic-document-question')
transition({**b,'action':'refute','hypothesis_card_id':h['hypothesis_card_id'],
    'title':'문서의 명령 예시를 실행 흔적으로 본 가설 반박',
    'card_summary':'원문은 제품 설명서의 명령 예시입니다. 이 문자열을 호스트 실행 기록으로 해석한 가설은 반박되었으며, 다른 경로의 실행 여부는 별개입니다.',
    'supporting_evidence_ids':[],'refuting_evidence_ids':[obs[4]['id']],
    'change_reason':'새 증거를 만든 것이 아니라 같은 원문의 문서 문맥을 반영해 해석을 정정했습니다.'},'synthetic-refute',final=True)
d=s.list('dossier',cid)[1]
history=assessment_revision(d,d['finding'],'synthetic-original',published=True)
s.update(d['id'],assessment_history=history)
f={**d['finding'],'title':'예약 명령의 호출 기록 확인, 실행 완료는 미확인',
   'card_summary':'예약 명령이 호출된 기록을 확인했습니다. 호출 이후 프로그램이 정상 실행되었는지와 작업 목적은 추가 대조가 필요합니다.'}
s.update(d['id'],finding=f,assessment_history=assessment_revision(s.get(d['id']),f,'synthetic-reassessment',published=True))
print('Synthetic lifecycle and same-card revisions seeded:',cid)
