"""Cross-platform Docker + actual local-model investigation acceptance test."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request

p=argparse.ArgumentParser();p.add_argument('--model',required=True,help='Already installed local Ollama model name')
p.add_argument('--fixture',choices=['baseline','hunt'],default='baseline')
p.add_argument('--model-url',default='http://host.docker.internal:11434');args=p.parse_args()
root=Path(__file__).resolve().parents[1];evidence=root/'artifacts/e2e-evidence';evidence.mkdir(parents=True,exist_ok=True)
if not (root/'.env').exists():raise SystemExit('Run scripts/setup.ps1 -NoStart or scripts/setup.sh --no-start first.')
user=f'{os.getuid()}:{os.getgid()}' if hasattr(os,'getuid') else '10001:10001'
fixture=evidence/('hunt-controls.E01' if args.fixture=='hunt' else 'investigation-fixture.E01')
if not fixture.exists():
    subprocess.run(['docker','run','--rm','-i','--network','none','--user',user,'--mount',f'type=bind,source={evidence},target=/out',
        'evidence-frontier-worker:0.1.0','python','-','/out'],input=(root/'scripts'/('make_hunt_fixture.py' if args.fixture=='hunt' else 'make_investigation_fixture.py')).read_bytes(),check=True,cwd=root)
compose=['docker','compose','-p','frontier-investigation-e2e','-f','compose.yaml','-f','compose.test.yaml']
subprocess.run(compose+['up','-d','--no-build','--wait'],check=True,cwd=root)
base='http://127.0.0.1:8767'
headers={'Content-Type':'application/json','X-Requested-With':'frontier'}
if os.getenv('WORKBENCH_TOKEN'):headers['X-Workbench-Token']=os.environ['WORKBENCH_TOKEN']
def request(path,method='GET',body=None):
    req=urllib.request.Request(base+path,method=method,headers=headers,data=json.dumps(body).encode() if body is not None else None)
    with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)
request('/api/settings','PUT',{'protocol':'ollama','base_url':args.model_url,'model':args.model,'falsifier_model':'','trusted_lan':False})
case=request('/api/cases','POST',{'name':'E2E · '+args.fixture+' controls','profile':'standard','question':'사건 키워드 없이 침해 흔적과 정상 대안을 조사하세요.'})
cid=case['id'];request('/api/cases/'+cid+'/evidence','POST',{'path':fixture.name});request('/api/cases/'+cid+'/start','POST',{})
print(json.dumps({'case_id':cid,'url':base}),flush=True)
deadline=time.monotonic()+7200;last=None
while time.monotonic()<deadline:
    state=request('/api/cases/'+cid);stage=(state['case']['status'],state['case'].get('investigation_stage'))
    if stage!=last:print(json.dumps({'status':stage[0],'stage':stage[1]},ensure_ascii=False),flush=True);last=stage
    if stage[0] not in ('running','pause_requested'):break
    time.sleep(3)
else:raise SystemExit('E2E deadline reached; inspect the preserved Docker case.')
subprocess.run([sys.executable,str(root/'scripts'/('verify_hunt_e2e.py' if args.fixture=='hunt' else 'verify_investigation_e2e.py')),base,cid],check=True,cwd=root)
