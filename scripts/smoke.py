"""Cross-platform HTTP integration test against an already started workbench."""
import json
import os
import time
import urllib.request
import zipfile
import io

base=os.getenv('SMOKE_URL','http://127.0.0.1:8765')
def request(path,method='GET',body=None):
    headers={'Content-Type':'application/json','X-Requested-With':'frontier'}
    if os.getenv('WORKBENCH_TOKEN'):headers['X-Workbench-Token']=os.environ['WORKBENCH_TOKEN']
    r=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else (b'' if method=='POST' else None),headers=headers,method=method)
    with urllib.request.urlopen(r,timeout=20) as response:return response.read()

assert json.loads(request('/health'))['status']=='ok'
case=json.loads(request('/api/cases','POST',{'name':'Smoke · synthetic activity','profile':'standard'}))
id=case['id']
request(f'/api/cases/{id}/evidence','POST',{'path':'activity.ndjson'})
request(f'/api/cases/{id}/start','POST')
deadline=time.monotonic()+60
while time.monotonic()<deadline:
    state=json.loads(request('/api/cases/'+id))
    if state['case']['status']!='running':break
    time.sleep(.5)
assert state['case']['status']=='quiescent',state['case']
assert state['summary']=={'total':6,'covered':5,'gaps':1},state['summary']
assert state['observation_count']==6,state['observation_count']
report=json.loads(request(f'/api/cases/{id}/reports','POST'))
with zipfile.ZipFile(io.BytesIO(request('/api/reports/'+report['id']+'/download'))) as z:
    assert {'report.html','report.json','manifest.json','SHA256SUMS'}==set(z.namelist())
print(json.dumps({'status':'passed','case_id':id,'coverage':state['summary'],'observations':state['observation_count'],'report':'valid ZIP'},ensure_ascii=False))
