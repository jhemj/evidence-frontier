"""Analyst-controlled registration of an existing OpenRelik template; never called by an agent."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import httpx

p=argparse.ArgumentParser()
p.add_argument('capability');p.add_argument('template_id',type=int);args=p.parse_args()
base=os.environ['OPENRELIK_URL'].rstrip('/')
with httpx.Client(timeout=30,trust_env=False) as c:
    r=c.get(f'{base}/workflows/templates/{args.template_id}',headers={'x-openrelik-access-token':os.environ['OPENRELIK_TOKEN']})
    r.raise_for_status();template=r.json()
spec=json.loads(template['spec_json'])
print(json.dumps(spec,ensure_ascii=False,indent=2))
if input('Review the workflow above. Type APPROVE to allow this exact template: ')!='APPROVE':raise SystemExit('Not registered.')
path=Path(__file__).resolve().parents[1]/'config/openrelik-templates.json'
registered=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
registered[args.capability]={'id':args.template_id,'sha256':hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
path.write_text(json.dumps(registered,indent=2),encoding='utf-8')
print('Registered the reviewed template. No workflow was executed.')
