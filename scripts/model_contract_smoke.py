"""Two real-model contract probes, synthetic data only; no tool execution."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.investigator import consult
from workbench.models import ProviderConfig
from workbench.review_validation import errors
from workbench.runtime_contract import code_identity
from workbench.semantic_contract import source_facts

p=argparse.ArgumentParser();p.add_argument('--model-url',required=True);p.add_argument('--model',required=True)
p.add_argument('--target-os',choices=['linux','windows'],default='linux')
p.add_argument('--strategy',choices=['guided','baseline'],default='guided')
p.add_argument('--think',choices=['off','on','auto','low','medium','high','xhigh'],default='off')
p.add_argument('--num-ctx',type=int,default=32768);p.add_argument('--num-predict',type=int,default=4000)
p.add_argument('--trusted-lan',action='store_true');p.add_argument('--out',type=Path,default=Path('artifacts/model-contract-smoke.json'))
p.add_argument('--luna-test',action='store_true',help='Explicit test-only Luna transport for every model role; synthetic inputs only')
p.add_argument('--include-scenarios',action='store_true',help='Also verify source-bound comparison and separate test priority using synthetic alternatives')
a=p.parse_args();config=ProviderConfig(protocol='ollama',base_url=a.model_url,model=a.model,trusted_lan=a.trusted_lan,
    investigation_strategy=a.strategy,think=a.think,num_ctx=a.num_ctx,num_predict=a.num_predict).model_dump()
if a.luna_test:
    from scripts.run_assisted_e2e import install_assisted_provider, MODEL_ROLES
    install_assisted_provider(a.out.parent/'synthetic-transport',luna_roles=MODEL_ROLES)
source='RUN-'+'a'*32+'/sources/example.txt'
o={'id':'OBSERVATION-smoke01','evidence_id':'EVIDENCE-smoke01','type':'linux_persistence','timestamp':None,
   'source_location':'example.E01:/etc/cron.d/example','fields':{'path':'/etc/cron.d/example','artifact_path':source,
   'excerpt':'*/10 * * * * root /opt/example/check.sh','locator_basis':'retained_source_bytes'}}
if a.target_os=='windows':
    o.update(type='windows_task',source_location='example.E01:Windows/System32/Tasks/Fixture',
        fields={'path':r'C:\Windows\System32\Tasks\Fixture','artifact_path':source,'task_uri':r'\Fixture',
                'actions':[{'Command':'fixture.exe'}],'locator_basis':'retained normalized JSON; configuration only'})
pack={'target_os':a.target_os,'observations':[o],'selection_is_partial':False,
      'output_validation_feedback':{'error':'tool_calls.0.artifact_path: Extra inputs are not permitted',
        'instruction':'Rejected output is not evidence. read_source accepts path, not artifact_path.'}}
result={'source_sha256':code_identity(),'model':a.model,'target_os':a.target_os,'synthetic_only':True,'configuration':config,'results':[]}
output,receipt=consult(config,'합성 계약 검증입니다. 제공된 보존 파일의 첫 256바이트를 read_source로 읽는 도구 요청 하나만 계획하세요. 실제 도구를 실행했다거나 내용이 안전하다고 결론내리지 마세요. claims와 hypotheses는 빈 배열로 두세요.',pack,role='investigator')
calls=output['tool_calls'];assert len(calls)==1 and calls[0]['tool']=='read_source' and calls[0]['path']==source
assert 'artifact_path' not in calls[0]
result['results'].append({'role':'investigator','status':'passed','output':output,'receipt':receipt})
print('investigator schema/path: passed',flush=True)
hid='HYPOTHESIS-smoke01'
pack={'target_os':a.target_os,'observations':[o],'required_dossiers':[{'id':hid,'title':'예약 작업이 실제 실행되었는가?','observation_ids':[o['id']]}],
    'allowed_observation_ids':[o['id']],'allowed_observation_ids_by_dossier':{hid:[o['id']]},
    'final_pass':True,'selection_is_partial':False,'scope':{'presented_observations':1,'relevant_observations':1}}
pack['literal_fact_candidates']=source_facts([o])
output,receipt=consult(config,'합성 계약 검증입니다. 예약 등록과 실제 실행 여부를 분리하여 이 가설에 답하세요. 실행·연결·전송 성공을 확인할 로그는 제공되지 않았습니다.',pack,role='synthesis')
assert not errors(output,[hid],[o['id']],{hid:[o['id']]},{o['id']:o},require_literals=True)
assert not output['next_checks'] and len(output['findings'])==1
assert not any(s['judgment']=='확인' and s['stage'] in ('execution','connection','objective') for f in output['findings'] for s in f['stages'])
assert set(output['supporting_evidence_ids']+output['refuting_evidence_ids']).issubset({o['id']})
assert not set(output['supporting_evidence_ids'])&set(output['refuting_evidence_ids'])
from workbench.incident_status import assessment_errors
assert not assessment_errors(output)
assert not output.get('incident_assessment') or output['incident_assessment']['verdict']=='undetermined'
result['results'].append({'role':'synthesis','status':'passed','output':output,'receipt':receipt})
if a.include_scenarios:
    scenario_pack={'target_os':a.target_os,'observations':[o],'selection_is_partial':False,'dynamic_hypotheses':[]}
    output,receipt=consult(config,'합성 계약 검증입니다. 이 예약 설정의 목적은 승인된 운영 점검인지 비인가 작업인지 미확인입니다. comparison_question="예약 작업은 승인된 운영인가?"인 두 대안 가설을 action=create로 제시하고 각 scenario_assessment를 채우세요. 설정만 제시되었으므로 evidence_fit=limited, 실제 실행/악성/승인은 미확인입니다. 도구와 claims는 빈 배열로 두세요. 두 개 지정은 이 합성 검증에만 해당합니다.',scenario_pack,role='investigator')
    from workbench.scenarios import accepted
    assert len(output['hypotheses'])==2 and not output['tool_calls']
    for h in output['hypotheses']:
        assert h['action']=='create' and not h['hypothesis_id'] and not h['hypothesis_card_id'] and h['number'] is None
        assert set(h['supporting_evidence_ids']+h['refuting_evidence_ids']).issubset({o['id']})
        assessment=accepted(h.get('scenario_assessment'),h['supporting_evidence_ids'],h['refuting_evidence_ids'],h.get('basis'))
        assert assessment and assessment['comparison_question']=='예약 작업은 승인된 운영인가?' and assessment['evidence_fit']=='limited'
    result['results'].append({'role':'scenario-investigator','status':'passed','output':output,'receipt':receipt})
    print('scenario alternatives/source scope/separate priority: passed',flush=True)
a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('final synthesis schema/source scope: passed',flush=True)
