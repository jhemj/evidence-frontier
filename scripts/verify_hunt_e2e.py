"""Validate actual Docker/local-model hunting output against inert E01 controls."""
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import zipfile

base,cid=sys.argv[1:3]
headers={'X-Requested-With':'frontier'}
if os.getenv('WORKBENCH_TOKEN'):headers['X-Workbench-Token']=os.environ['WORKBENCH_TOKEN']
with urllib.request.urlopen(urllib.request.Request(base+'/api/cases/'+cid,headers=headers),timeout=120) as response:state=json.load(response)
if state['case']['status'] in ('running','paused','pause_requested'):
    print(json.dumps({'status':'pending','stage':state['case'].get('investigation_stage')}));sys.exit(2)
root=Path(__file__).resolve().parents[1];archive_path=root/'artifacts/hunt-validation'/f'{cid}.zip'
subprocess.run([sys.executable,str(root/'scripts/verify_report_package.py'),base,cid,str(archive_path)],check=True)
with zipfile.ZipFile(archive_path) as archive:
    doc=json.loads(archive.read('report.json'))
    detections=[o for o in doc['observations'] if o['type']=='linux_detection']
    expected={'extra_uid_zero','writable_persistence','download_execute','webshell_code','ELF_socket_filter_and_process_disguise'}
    actual={o['fields']['rule_id'] for o in detections}
    assert expected<=actual,expected-actual
    assert not any(o['fields']['path']=='/opt/maintenance/check.sh' for o in detections)
    elf=next(o for o in detections if o['fields']['rule_id']=='ELF_socket_filter_and_process_disguise')
    assert elf['fields'].get('machine') and not elf['fields'].get('elf_parser_error')
    auth=[o for o in doc['observations'] if o['type']=='linux_authentication' and o['fields'].get('path')=='/var/log/zz-secure.1.gz']
    assert auth and auth[0]['fields']['locator_basis']=='gzip decompressed bytes'
    manifest_name=next(n for n in archive.namelist() if n.startswith('evidence/RUN-') and n.endswith('/manifest.json'))
    manifest=json.loads(archive.read(manifest_name))
    assert manifest['bytes_extracted']==512*1024*1024
    assert any(s['path']=='/var/log/zz-messages' and s['status']=='excluded' for s in manifest['sources'])
    assert any(s['path']=='/var/log/zz-messages' and s['status']=='hunt_excerpt' for s in manifest['sources'])
    dossiers=doc['dossiers'];assert len(dossiers)>=8
    assert all(d['status']=='reviewed' for d in dossiers),[(d['title'],d['status']) for d in dossiers]
    receipts=doc['tool_receipts']
    assert any(r.get('receipt_type')=='dossier_model' and r.get('usage',{}).get('eval_count',0)>0 for r in receipts)
    assert any(r.get('receipt_type')=='investigation_tool' for r in receipts)
    # A second assessment must follow actual admitted work, not just model prose.
    assert any(b['round']>0 and b['job_ids'] for b in state['dossier_batch'])
    assert len([r for r in receipts if r.get('receipt_type')=='dossier_model'])>len(state['dossier_batch'])
    print(json.dumps({'status':'passed','case_id':cid,'expected_patterns':len(expected),'detected_patterns':len(expected&actual),
        'normal_control_detections':0,'dossiers_reviewed':len(dossiers),
        'semantic_intrusion_verdict':'Requires review of scoped AI findings; inert fixture success is not a real-case detection rate.'},ensure_ascii=False))
