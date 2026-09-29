"""Paired Native contract probes, NOT a forensic-accuracy benchmark.

Same model digest/settings/fixtures and call cap for both arms. Interleave arm
order to reduce warm-cache order effects. No real evidence or worker execution.
Hermes is not registered; a future adapter must meet the same boundary first.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import httpx

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from workbench.provider import validate_url
from workbench.models import ProviderConfig
from workbench.runtime_contract import code_identity
from workbench.procedures import identity


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model-url',required=True);p.add_argument('--model',required=True)
    p.add_argument('--trusted-lan',action='store_true')
    p.add_argument('--think',choices=['off','on','low','medium','high','xhigh'],default='off',help='Explicit mode; auto is not a reproducible comparison setting')
    p.add_argument('--num-ctx',type=int,default=32768);p.add_argument('--num-predict',type=int,default=4000)
    p.add_argument('--repeats',type=int,choices=range(1,4),default=1)
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--out',type=Path,default=ROOT/'artifacts/investigator-comparison')
    a=p.parse_args()
    config=ProviderConfig(base_url=a.model_url,model=a.model,trusted_lan=a.trusted_lan,
        think=a.think,num_ctx=a.num_ctx,num_predict=a.num_predict).model_dump()
    config.pop('investigation_strategy')
    plan={'version':'native-contract-comparison-1','source_sha256':code_identity(),'procedures':identity(),
        'shared_configuration':config,'max_generate_calls':8*a.repeats,'repeats':a.repeats,
        'arms':['baseline','guided'],'fixtures':['linux','windows'],'hermes':'not_installed_or_registered',
        'scope':'Synthetic instructed schema/source-scope probes only; not detection accuracy, a real investigation, or a framework superiority test.',
        'status':'planned' if a.dry_run else 'running','runs':[]}
    a.out.mkdir(parents=True,exist_ok=True)
    summary=a.out/'comparison.json'
    if summary.exists():raise SystemExit('Output already exists; select a new directory to preserve prior runs.')
    def save():summary.write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    save()
    if a.dry_run:
        print(json.dumps({'status':'planned','output':str(summary),'max_generate_calls':plan['max_generate_calls']}));return
    base=validate_url(a.model_url,a.trusted_lan)
    def digest():
        with httpx.Client(timeout=15,trust_env=False,follow_redirects=False) as client:
            response=client.get(base+'/api/tags');response.raise_for_status()
        value=next((m.get('digest') for m in response.json().get('models',[]) if m.get('name')==a.model),None)
        if not value:raise ValueError('Selected model digest unavailable')
        return value
    try:
        fixed=digest();plan['model_digest']=fixed;save()
        if os.getenv('FRONTIER_MODEL_DIGEST','unverified') not in ('unverified',fixed):
            raise ValueError('Configured and observed model digests differ')
        for repeat in range(a.repeats):
            for os_index,target in enumerate(('linux','windows')):
                arms=('baseline','guided') if (repeat+os_index)%2==0 else ('guided','baseline')
                for arm in arms:
                    destination=a.out/f'{target}-{arm}-{repeat+1}.json'
                    command=[sys.executable,str(ROOT/'scripts/model_contract_smoke.py'),'--model-url',base,'--model',a.model,
                        '--target-os',target,'--strategy',arm,'--think',a.think,'--num-ctx',str(a.num_ctx),
                        '--num-predict',str(a.num_predict),'--out',str(destination)]
                    if a.trusted_lan:command.append('--trusted-lan')
                    env={**os.environ,'FRONTIER_MODEL_DIGEST':fixed}
                    start=time.monotonic()
                    run=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,timeout=1250)
                    row={'target_os':target,'arm':arm,'repeat':repeat+1,'status':'passed' if run.returncode==0 else 'failed',
                        'wall_seconds':round(time.monotonic()-start,3),'returncode':run.returncode}
                    if run.returncode==0:
                        result=json.loads(destination.read_text(encoding='utf-8'))
                        row['receipts']=[{k:v for k,v in r['receipt'].items() if k in ('role','usage','elapsed_seconds','generation_settings','prompt_characters','output_characters')} for r in result['results']]
                    else:row['error']=(run.stderr or run.stdout)[-2500:]
                    plan['runs'].append(row);save()
                    print(json.dumps({k:row[k] for k in ('target_os','arm','repeat','status')},ensure_ascii=False),flush=True)
        plan['model_digest_unchanged']=digest()==fixed
        plan['status']='completed' if plan['model_digest_unchanged'] else 'invalid_model_changed'
        plan['all_contract_probes_passed']=all(r['status']=='passed' for r in plan['runs'])
        save()
    except (ValueError,httpx.HTTPError,subprocess.TimeoutExpired) as ex:
        plan.update(status='blocked_or_incomplete',error=str(ex)[:2000]);save();raise SystemExit(str(ex))
    print(json.dumps({'status':plan['status'],'all_contract_probes_passed':plan['all_contract_probes_passed'],'output':str(summary)}))
    if plan['status']!='completed' or not plan['all_contract_probes_passed']:raise SystemExit(1)


if __name__=='__main__':main()
