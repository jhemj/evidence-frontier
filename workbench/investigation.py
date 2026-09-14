"""Persistent, bounded local-AI investigator with actual forensic tool calls."""
import hashlib
import json
import os
from pathlib import Path
import httpx
from .models import InvestigationTool, InvestigationToolRequest
from .provider import Provider
from .store import now

HYPOTHESES = [
    ('최초 유입과 권한 확보', ['linux_authentication', 'linux_command', 'linux_audit'], '인증정보 오용·취약점·내부 관리·자동화·복원', '최초 보존 기록은 최초 침해 시각이 아님'),
    ('계정·관리자 세션 오용', ['linux_account', 'linux_authentication', 'linux_session', 'linux_login_record', 'linux_ssh_trust'], '승인된 관리자 접속과 업무계정 사용', '접속 승인·작업자 신원 자료 미제공'),
    ('의심 프로세스의 실제 행위', ['linux_process', 'linux_command', 'linux_audit', 'linux_system_event'], '정상 업무 프로그램·프로세스 목록·점검 산출물', '실행 존재·기능·성공·종료를 별도 판단'),
    ('원본 의심 파일 확보·복구', ['linux_binary', 'linux_tool_result'], '업무 바이너리·보안 점검 프로그램', '삭제 inode·미할당·swap·메모리 복구 미수행'),
    ('자동 재실행과 지속성', ['linux_persistence', 'linux_cron_call', 'linux_ssh_trust'], '정상 예약 작업·호출 실패·경로 오류', '등록·호출은 프로그램 시작·성공 증거가 아님'),
    ('내부·외부 통신 성공 단계', ['linux_network', 'linux_authentication', 'linux_configuration'], '설정만 존재·인증 실패·정상 관리·내부 업무 통신', '패킷/방화벽·상대 서버 자료 미제공; 목적 달성 별도'),
    ('로그·설정·시간 변경', ['linux_configuration', 'linux_system_event', 'linux_command'], '회전·보존 정책·점검·시스템 중단', '연도 없는 syslog 시각은 추정; 변경 주체 별도'),
    ('수평이동과 연계 서버', ['linux_ssh_trust', 'linux_authentication', 'linux_command'], '정상 내부 접속·우연한 파일명/포트 일치', '연계 서버 원본·동일 키/바이트 비교 미제공'),
    ('자료 접근·수집·압축·유출', ['linux_command', 'linux_audit', 'linux_network'], '정상 백업·점검 수집·업무 전송', '명령 기록은 전송 성공·외부 도달 증거가 아님'),
    ('정상 운영·점검 경쟁설명', ['linux_inspection_result', 'linux_command', 'linux_configuration', 'linux_binary'], '침해 행위가 점검 흔적으로 위장되었을 가능성', '기존 점검 결과도 독립적 무결성·범위 검토 필요'),
]


def seed(controller, case_id, evidence_id):
    existing = controller.store.list('hypothesis', case_id)
    if any(h.get('evidence_id') == evidence_id and h.get('contract') == 'linux-v1' for h in existing): return
    for index, (title, types, alternative, limit) in enumerate(HYPOTHESES, 1):
        controller.store.add('hypothesis', case_id, evidence_id=evidence_id, contract='linux-v1',
            number=index, text=title, status='open', judgment='미확인', observation_ids=[], supporting_evidence_ids=[],
            refuting_evidence_ids=[], negative_searches=[], competing_explanations=[alternative], unavailable_materials=[limit],
            expected_source_types=types, uncertainty=limit, judgment_history=[{'at': now(), 'judgment': '미확인', 'reason': '상세 내용 조사 전 초기 등록'}])


def ranked(observations, question=''):
    terms = [t.casefold() for t in question.split() if len(t) > 2]
    def score(o):
        f = o['fields']; value = 0
        if o['type'].startswith('linux_'): value += 10
        if o['type']=='linux_detection':value+=100
        if o['type'] in ('linux_tool_result', 'linux_environment', 'linux_binary'): value += 30
        if o['type'] in ('linux_authentication', 'linux_persistence', 'linux_inspection_result'): value += 15
        if f.get('signals') and not f.get('inspection_context'): value += 20
        text = json.dumps(f, ensure_ascii=False).casefold()
        value += 12 * sum(t in text for t in terms)
        return value
    # Round-robin sources/types prevent a large metadata or single log from filling context.
    buckets = {}
    for o in sorted(observations, key=score, reverse=True): buckets.setdefault(o['type'], []).append(o)
    result = []
    for i in range(100):
        for bucket in buckets.values():
            if i < len(bucket): result.append(bucket[i])
    return result


def compact_observation(original):
    o={k:original[k] for k in ('id','type','timestamp','source_location','fields')}
    f=dict(o['fields']);o['fields']=f
    for key,value in list(f.items()):
        if isinstance(value,str) and len(value)>6000:f[key]=value[:6000];f[key+'_truncated']=True
        elif isinstance(value,list) and len(value)>10:f[key]=value[:10];f[key+'_truncated']=True
    if len(json.dumps(o,ensure_ascii=False))>10000:
        o['fields']={k:v for k,v in f.items() if k in ('path','rule_id','title','severity','excerpt','command','stage','source_sha256','source_complete','artifact_path','byte_offset','image_file_byte_offset','line','facts','interpretation_limit','capability_symbols','selected_strings')}
        o['fields']['context_compacted']=True
    from .retrieval import source_origin
    o['source_origin'] = source_origin(original)
    original_excerpt = original['fields'].get('excerpt', '')
    shown = o['fields'].get('excerpt', '')
    if original['fields'].get('path', '').startswith('/'):
        offset = original['fields'].get('image_file_byte_offset', original['fields'].get('source_offset', 0)) or 0
        o['context_request'] = {'tool':'read_file','path':original['fields']['path'],
            'byte_offset':max(0, offset-2048), 'byte_length':8192}
        if 'decompressed' in original['fields'].get('locator_basis',''):
            artifact=original['fields'].get('artifact_path','')
            o['context_request']={'tool':'read_source','path':artifact,
                'byte_offset':original['fields'].get('byte_offset',0),'byte_length':8192}
        if len(original_excerpt)>len(shown):
            o['fields']['excerpt_truncated']=True
            o['context_request']['byte_length']=16384
            # Replacement decoding and redaction make text length unsuitable as
            # a byte locator. Re-read from a known original offset with overlap.
        for selector in ('partition_offset','inode'):
            if original['fields'].get(selector) is not None:o['context_request'][selector]=original['fields'][selector]
        o['context_limit']='Byte location is recorded where available; inspect the returned range before interpreting it.'
    return o


def evidence_pack(controller, case_id, question='', preferred=()):
    obs = controller.active_observations(case_id); selected = []; used = set(); length = 0
    by_id = {o['id']: o for o in obs}
    ordered = [by_id[oid] for oid in preferred if oid in by_id] + ranked(obs, question)
    for original in ordered:
        if original['id'] in used: continue
        o=compact_observation(original)
        f=o['fields']
        # Preserve provenance; content excerpts can be expanded via actual read tools.
        for key in ('elf_headers',):
            if key in f and len(f[key]) > 1600: f[key] = f[key][:1600]; f[key + '_truncated'] = True
        encoded = json.dumps(o, ensure_ascii=False)
        if length + len(encoded) > 32000: continue
        selected.append(o); used.add(o['id']); length += len(encoded)
        if len(selected) >= 60: break
    return {'observations': selected, 'total_observations': len(obs), 'included_observations': len(selected),
            'selection_is_partial': len(selected) < len(obs),
            'hypotheses': [{'number': h.get('number'), 'question': h['text'], 'alternatives': h.get('competing_explanations'),
                            'unavailable': h.get('unavailable_materials')} for h in controller.store.list('hypothesis', case_id) if h.get('contract') == 'linux-v1']}


def store_tool_result(controller, case_id, evidence, task, result, request):
    receipt = controller.store.add('receipt', case_id, task_id=task['id'], evidence_id=evidence['id'], receipt_type='investigation_tool',
                                   request=request, result={k: v for k, v in result.items() if k != 'observations'}, output_count=len(result.get('observations', [])))
    ids = []
    known={o['digest']:o for o in controller.store.list('observation',case_id) if o.get('digest')}
    for event in result.get('observations', []):
        digest = hashlib.sha256(json.dumps([evidence['id'], event], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        ob = known.get(digest)
        if not ob:
            ob = controller.store.add('observation', case_id, evidence_id=evidence['id'], receipt_id=receipt['id'], cell_id=task['cell_id'], digest=digest, **event)
            known[digest]=ob
        controller.store.add('lineage', case_id, observation_id=ob['id'], receipt_id=receipt['id'], cell_id=task['cell_id']); ids.append(ob['id'])
    return ids


def run(controller, case_id, evidence, task):
    from .investigation_graph import tick
    return tick(controller, case_id, evidence, task)
