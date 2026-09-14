"""Verify a completed real-service run of make_investigation_fixture.py.

The model does not define the expected facts. Pass BASE_URL CASE_ID.
"""
import hashlib
import io
import json
import os
import sys
import urllib.request
import zipfile

base,cid=sys.argv[1:3]
headers={'X-Requested-With':'frontier'}
if os.getenv('WORKBENCH_TOKEN'):headers['X-Workbench-Token']=os.environ['WORKBENCH_TOKEN']
def request(path):
    with urllib.request.urlopen(urllib.request.Request(base+path,headers=headers),timeout=120) as response:return response.read()
state=json.loads(request('/api/cases/'+cid))
if state['case']['status'] in ('running','paused','pause_requested'):
    print(json.dumps({'status':'pending','stage':state['case'].get('investigation_stage')}));sys.exit(2)
assert not [t for t in state['task'] if t['status'] in ('failed','blocked')], state['task']
assert state['report'],'automatic report missing'
with zipfile.ZipFile(io.BytesIO(request('/api/reports/'+state['report'][-1]['id']+'/download'))) as archive:
    manifest=json.loads(archive.read('manifest.json'))
    for path,expected in manifest['files'].items():assert hashlib.sha256(archive.read(path)).hexdigest()==expected,path
    document=json.loads(archive.read('report.json'));observations=document['observations'];ids={o['id'] for o in observations}
    assert any(o['type']=='linux_authentication' and o['fields']['user']=='analyst' and o['fields']['outcome']=='accepted' for o in observations)
    links=[o['fields'] for o in observations if o['type']=='linux_persistence_link']
    agent=next(f for f in links if f['target_path']=='/opt/agent.sh')
    dormant=next(f for f in links if f['target_path']=='/opt/never-ran.sh')
    assert agent['facts']['cron_invocation_records']==1
    assert dormant['facts']['cron_invocation_records']==0 and not dormant['facts']['program_execution_confirmed']
    audit=next(o for o in observations if o['type']=='linux_audit_group')
    assert len(audit['fields']['records'])==3
    assert all(not c.get('automatic') or c['status']=='candidate' for c in document['automatic_findings'])
    assert all(set(c['observation_ids']).issubset(ids) for c in document['automatic_findings'])
    assert any('scp' in o['fields'].get('command','') for o in observations)
    assert not any(o['fields'].get('facts',{}).get('objective_success_confirmed') for o in observations)
    receipts=document['tool_receipts']
    assert any(r.get('receipt_type')=='investigator_model' and r.get('usage',{}).get('eval_count',0)>0 for r in receipts)
    assert any(r.get('receipt_type')=='investigation_tool' and r.get('request',{}).get('tool')=='read_file' for r in receipts)
    assert any(r.get('receipt_type')=='automatic_falsifier' for r in receipts)
    assert json.loads(archive.read('RESULT_REVISION.json'))['result_revision']==document['case']['result_revision']
    output={'status':'passed','case_id':cid,'observations':len(observations),'files_verified':len(manifest['files']),
            'local_model_receipts':sum(r.get('receipt_type')=='investigator_model' for r in receipts),
            'tool_receipts':sum(r.get('receipt_type')=='investigation_tool' for r in receipts),
            'candidate_claims':len(document['automatic_findings']),'result_revision':document['case']['result_revision']}
print(json.dumps(output,ensure_ascii=False))
