"""Fictional report contract example. No private reference-image values are used."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.report_views import project


def example():
    observations=[]
    for i,(typ,path,stamp,fields) in enumerate([
        ('linux_authentication','/var/log/auth.log','2026-09-01T08:30:00+09:00',{'user':'example-operator','event':'인증 성공','excerpt':'Accepted publickey for example-operator from 192.0.2.10'}),
        ('linux_persistence','/etc/cron.d/example',None,{'command':'*/10 * * * * root /opt/example/check.sh','excerpt':'검증용 예약 작업 설정. 실행 기록은 미확보.'}),
        ('linux_command','/var/log/audit/audit.log','2026-09-01T08:35:00+09:00',{'user':'example-operator','command':'/usr/bin/id','event':'프로세스 실행 호출'})],1):
        observations.append({'id':f'OBSERVATION-example{i:02d}','evidence_id':'EVIDENCE-example01','type':typ,'timestamp':stamp,
            'source_location':f'example.E01:partition@1048576:{path}:line={i}','receipt_id':'RECEIPT-example01',
            'fields':{'path':path,'partition_offset':1048576,'inode':100+i,'line':i,'byte_offset':i*128,
                'locator_basis':'retained_source_bytes','time_basis':'원문 명시 +09:00' if stamp else '설정 파일: 이벤트 시각 없음',
                'source_sha256':hashlib.sha256(f'fictional-source-{i}'.encode()).hexdigest(),**fields}})
    findings=[{'dossier_id':'DOSSIER-example01','title':'관리 계정의 인증 성공 기록 확인','judgment':'확인',
        'reason':'인증 로그에 관리 계정의 공개키 인증 성공 기록이 있습니다. 계정 소유자 본인의 정당한 접속인지, 접속 이후 어떤 활동을 했는지는 별도 확인이 필요합니다.',
        'observation_ids':[observations[0]['id']],'stages':[{'stage':'인증','judgment':'확인','statement':'공개키 인증 성공 메시지가 원문에 기록됨.','observation_ids':[observations[0]['id']]}],
        'alternatives':['승인된 유지보수 담당자의 정상 접속'],'remaining_checks':['작업 승인 기록과 해당 공개키 소유자 확인'],'timeline_role':'핵심'},
        {'dossier_id':'DOSSIER-example02','title':'예약 작업의 악성 목적 및 실제 실행 여부 미확인','judgment':'미확인',
        'reason':'예약 설정은 확인되지만 대상 스크립트와 동일 시점의 실행 결과가 확보되지 않았습니다. 등록 사실만으로 지속적 악성 실행을 확정하지 않습니다.',
        'observation_ids':[observations[1]['id']],'stages':[{'stage':'설정','judgment':'확인','statement':'10분 간격의 스크립트 호출이 설정되어 있음.','observation_ids':[observations[1]['id']]}],
        'alternatives':['정상 상태 점검을 위한 예약 작업'],'remaining_checks':['대상 스크립트와 cron 실행 로그 확보'],'timeline_role':'핵심'}]
    return {'schema_version':'1.2','id':'REPORT-example-contract','generated_at':'2026-09-22T10:00:00+09:00',
        'case':{'id':'CASE-example','name':'합성 검증 예제 (실사건 아님)','status':'quiescent'},
        'evidence':[{'id':'EVIDENCE-example01','name':'예제 서버 이미지','path':'example.E01','connected':True}],
        'observations':observations,'judgments':[{'id':'JUDGMENT-example','findings':findings,'receipt_id':'RECEIPT-example01'}],
        'dossiers':[{'id':f['dossier_id'],'status':'reviewed','finding':f,'receipt_id':'RECEIPT-example01'} for f in findings],
        'dossier_history':[],'case_synthesis':[],'session_links':[],'automatic_findings':[],
        'coverage':[{'label':'인증·예약 설정 검토','status':'partial','error':'원본 대상 스크립트와 실행 종료 기록 미확보'}],
        'completion':{'label':'실행 종료 · 분석 공백 있음','execution_terminated':True,'review_units':2,'unreviewed_units':0,
            'indexed_observations':3,'ai_presented_observations':3,'judgment_cited_observations':2,
            'scope':'레이아웃·내용 연결을 확인하는 가상 사례이며 AI 분석 성능 검증 결과가 아닙니다.','collection_denominators':[]},
        'check_ledger':{'not_executed':1,'unassessed_contracts':1,'checks':[],'scope':'가상 예제의 검사 공백'},
        'required_materials':[{'category':'external_required','reference':'DOSSIER-example01','material':'작업 승인 내역과 공개키 소유자 정보','blocked_conclusion':'정상 관리 접속과 계정 오용 구분','action':'권한 있는 담당자에게 확인'},
            {'category':'image_extractable','reference':'DOSSIER-example02','material':'/opt/example/check.sh 원본과 cron 로그','blocked_conclusion':'실제 실행 및 목적 판단','action':'보존 여부 확인 후 추가 추출'}],
        'review_failures':[],'tool_receipts':[{'id':'RECEIPT-example01','receipt_type':'synthetic_fixture','model':'호출 없음 · 양식 검증용','created_at':'2026-09-22T10:00:00+09:00'}],
        'snapshot':{'scope_sha256':hashlib.sha256(b'fictional-snapshot').hexdigest()}}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=Path('artifacts/report-contract-sample'))
    parser.add_argument('--format',choices=['docx','html'],required=True)
    parser.add_argument('--follow-through',action='store_true',help='Include synthetic follow-through and literal-check examples')
    parser.add_argument('--grounded',action='store_true',help='Exercise fact-bound presentation on fictional selected facts')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    doc=example()
    if args.grounded:
        from workbench.presentation_claims import bind
        obs={o['id']:o for o in doc['observations']}
        for d,pointer,value in zip(doc['dossiers'],['/fields/user','/fields/command'],
                ['example-operator','*/10 * * * * root /opt/example/check.sh']):
            f=d['finding']
            f['fact_assertions']=[{'observation_id':f['observation_ids'][0],'pointer':pointer,'operator':'equals','value':value}]
            d['finding']=bind(f,obs)
        doc['judgments'][0]['findings']=[d['finding'] for d in doc['dossiers']]
    if args.follow_through:
        finding=doc['judgments'][0]['findings'][0]
        finding['fact_assertions']=[{'observation_id':doc['observations'][0]['id'],'pointer':'/fields/user','operator':'equals','value':'example-operator'}]
        doc['investigation_frontier']={'open_leads':1,'deferred_discovery':1,'scope':'단서의 후속 검사와 보류 범위를 구별합니다.',
            'leads':[{'title':'예약 대상의 실행 기록 대조','state':'investigating','disposition':'pending','reason':'검사 결과가 아직 평가되지 않았습니다.',
                      'next_tests':[{'request':{'tool':'search','query':'/opt/example/check.sh'},'evaluation_status':'unassessed'}]}],
            'discovery_inventory':[{'query':'/opt/example/check.sh','state':'deferred','reason':'현재 실행 예산 밖. 미검토 범위로 보존합니다.','observation_ids':[doc['observations'][1]['id']]}]}
    if args.format=='html':
        from jinja2 import Environment,FileSystemLoader,select_autoescape
        env=Environment(loader=FileSystemLoader(Path(__file__).resolve().parents[1]/'templates'),autoescape=select_autoescape(['html']))
    else:
        from workbench.report_docx import render
    for name,view in project(doc).items():
        if args.format=='docx':(args.out/(name+'.docx')).write_bytes(render(view))
        else:(args.out/(name+'.html')).write_text(env.get_template('report_readers.html').render(view=view),encoding='utf-8')
    (args.out/'report.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2),encoding='utf-8')
    print(args.out.resolve())
