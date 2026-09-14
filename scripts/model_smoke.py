"""Opt-in local model integration check using an existing synthetic smoke case only."""
import argparse
import json
import urllib.request

p=argparse.ArgumentParser();p.add_argument('--model',required=True);p.add_argument('--url',default='http://127.0.0.1:8765');args=p.parse_args()
def request(path,method='GET',body=None):
    req=urllib.request.Request(args.url+'/api'+path,data=json.dumps(body).encode() if body is not None else (b'' if method=='POST' else None),method=method,headers={'Content-Type':'application/json','X-Requested-With':'frontier'})
    with urllib.request.urlopen(req,timeout=120) as response:return json.load(response)
cases=request('/cases');case=next((c for c in cases if c['name']=='Smoke · synthetic activity'),None)
if not case:raise SystemExit('Run scripts/smoke.py first to create a synthetic case.')
request('/settings','PUT',{'protocol':'ollama','base_url':'http://host.docker.internal:11434','model':args.model,'falsifier_model':'','trusted_lan':False})
answer=request('/cases/'+case['id']+'/messages','POST',{'message':'이 합성 예제에서 확인된 실행 기록과 정상 관리 작업의 가능성을 근거 ID에 연결해 2개 이내의 후보 주장으로 정리해줘. 조사 완료를 주장하지 마.'})
snapshot=request('/cases/'+case['id'])
print(json.dumps({'model':args.model,'message_mode':answer['mode'],'candidate_count':len(snapshot['claim'])}))
if snapshot['claim']:
    review=request('/claims/'+snapshot['claim'][-1]['id']+'/falsify','POST')
    print(json.dumps({'falsification':'passed','missing_checks':len(review['falsification']['missing_checks'])}))
