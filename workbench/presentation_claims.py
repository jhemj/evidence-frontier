"""High-visibility facts are rendered from selected, literal-bound claims.

Free model narrative remains an explicitly marked interpretation. It cannot
silently expand a path into a product identity, an account into a person, or a
record into successful/unauthorised behaviour. Templates are artifact semantics,
not incident names or normal/malicious-program lists.
"""
from copy import deepcopy
import re
from .semantic_contract import assertion_errors, digest

VERSION='grounded-presentation-1'
LABELS={
    'linux_authentication':('인증·권한 기록','인증·권한 기록만으로 실제 작업자·명령 결과·승인 여부를 확정할 수 없습니다.'),
    'linux_session':('세션·권한 기록','권한 사용 기록만으로 실제 작업자·명령 결과·승인 여부를 확정할 수 없습니다.'),
    'linux_cron_call':('예약작업 호출 기록','호출 기록은 프로그램의 실행 성공·등록 주체·승인을 입증하지 않습니다.'),
    'linux_command':('명령 문자열 기록','명령 기록만으로 실행 성공·통신 성립·실제 작업자를 확정할 수 없습니다.'),
    'linux_persistence':('예약·지속 설정 기록','설정의 존재와 실제 실행·악성 목적은 별도로 확인해야 합니다.'),
    'linux_configuration':('설정 내용 확인','설정 내용만으로 과거 적용·실행 또는 변경 주체를 확정할 수 없습니다.'),
    'windows_task':('예약작업 설정 기록','등록된 설정은 실행 성공이나 등록 승인 여부를 입증하지 않습니다.'),
    'windows_registry':('레지스트리 값 기록','보존된 값만으로 실제 실행이나 변경 행위자를 확정할 수 없습니다.'),
    'linux_binary':('파일의 정적 내용','파일의 문자열·기능 가능성과 실제 행위는 구별해야 합니다.'),
    'linux_detection':('파일 검사 단서','탐지 일치만으로 악성·실행·침해를 확정하지 않습니다.'),
}
DEFAULT=('인용 원문에 기록된 내용','이 원문 사실과 사건 원인·행위 성공·승인 여부에 대한 해석은 별개입니다.')
DISPLAY_POINTERS=('/fields/command','/fields/path','/fields/user','/fields/target_user',
                  '/fields/event_id','/fields/network_state','/fields/excerpt')


def literal_subject(fact,source):
    value=str(fact['value'])
    # A format-bound substring of a selected literal, not a model-invented
    # entity or an extra citation. Only registered authentication records use
    # this log grammar; documentation/static strings cannot become events.
    if fact['pointer']=='/fields/excerpt' and source.get('type') in ('linux_authentication','linux_session'):
        match=re.fullmatch(r'[^\r\n]*\bsudo:\s+([^:\r\n]+)\s*:\s*PWD=[^;\r\n]*;\s*USER=([^;\r\n]+);\s*COMMAND=([^\r\n]+)',value)
        if match:
            return 'sudo 명령 기록',match[3].strip(),{'kind':'sudo_log_command_substring',
                'account':match[1].strip(),'target_account':match[2].strip(),
                'command':match[3].strip(),'scope':'Recorded tokens only; account is not a person, command is not a product identity.'}
    return None,value,{'kind':'selected_literal'}


def bind(finding,observations):
    """Use ONLY facts the model selected; never invent or borrow a citation."""
    f=deepcopy(finding)
    selected=[a for a in f.get('fact_assertions',[]) if a.get('operator','equals')=='equals'
        and a.get('pointer') in DISPLAY_POINTERS
        and isinstance(a.get('value'),(str,int)) and not isinstance(a.get('value'),bool)]
    if assertion_errors(f,observations,set(observations)):raise ValueError('presentation source binding failed')
    selected.sort(key=lambda a:DISPLAY_POINTERS.index(a['pointer']))
    f['model_narrative']={'title':f.get('title',''),'card_summary':f.get('card_summary',''),
        'scope':'Model interpretation; not independently verified entity identity or causality.'}
    fact=selected[0] if selected else None
    projection=None
    if fact:
        source=observations[fact['observation_id']]
        label,limit=LABELS.get(source.get('type'),DEFAULT)
        specific,literal,projection=literal_subject(fact,source)
        label=specific or label
        # Display clipping is explicitly labelled; the exact selected fact,
        # source ID and field remain in display_binding and the original ledger.
        subject=literal if len(literal)<=80 else literal[:77]+'…'
        f['title']=label+' · '+subject
        f['card_summary']=f['title']+'. '+limit
    else:
        f['title']='판단 근거 검토 · 원문과 해석 구분 필요'
        f['card_summary']='선택된 근거의 상세 판단을 확인하세요. 표제에 쓸 대상·행위의 원문 필드가 아직 결속되지 않았습니다.'
    f['display_binding']={'version':VERSION,'fact':fact,'source_sha256':
        observations.get((fact or {}).get('observation_id'),{}).get('fields',{}).get('source_sha256'),
        'predicate':'literal_source_record','projection':projection,'status':'bound' if fact else 'unbound',
        'claim_sha256':digest({'fact':fact,'stage':f.get('stages',[])}),
        'scope':'Literal subject and registered artifact meaning only; no product/actor/network-boundary/intent inference.'}
    return f


def representative_key(finding,observations):
    """Group display only. Preserve every occurrence, claim and source owner."""
    fact=(finding.get('display_binding') or {}).get('fact')
    if not fact:return ('unbound',finding.get('dossier_id'),finding.get('title'))
    source=observations.get(fact['observation_id'],{});fields=source.get('fields',{})
    # Equal text alone never merges objects across evidence/partitions/boots.
    return (source.get('evidence_id'),fields.get('partition_offset'),fields.get('os_instance'),
        fields.get('boot_id'),fields.get('inode'),fields.get('path'),
        fact['pointer'],str(fact['value']),source.get('type'))


def representatives(findings,observations):
    groups={}
    for f in findings:
        key=representative_key(f,observations)
        row=groups.setdefault(key,{**f,'open_objections':[], 'member_dossier_ids':[],'occurrence_claims':[]})
        row['member_dossier_ids'].append(f.get('dossier_id'))
        row['occurrence_claims'].append({'dossier_id':f.get('dossier_id'),
            'observation_ids':f.get('observation_ids',[]),'counterevidence_ids':f.get('counterevidence_ids',[]),
            'judgment':f.get('judgment'),'stages':f.get('stages',[]),'open_objections':f.get('open_objections',[])})
        if f.get('open_objections'):
            row['open_objections']=list(row.get('open_objections',[]))+f['open_objections']
        if len({m['judgment'] for m in row['occurrence_claims']})>1:
            row['judgment']='미확인'
            row['representative_limit']='구성 판단의 등급이 달라 대표 확정 판단을 보류합니다. 개별 근거·반론은 상세에 보존합니다.'
    return list(groups.values())
