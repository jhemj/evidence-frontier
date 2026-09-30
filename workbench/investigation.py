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
    from .platforms import target_os, WINDOWS_HYPOTHESES
    platform=target_os(controller.store.get(case_id))
    contract=platform+'-v1'
    hypotheses=WINDOWS_HYPOTHESES if platform=='windows' else HYPOTHESES
    existing = controller.store.list('hypothesis', case_id)
    if any(h.get('evidence_id') == evidence_id and h.get('contract') == contract for h in existing): return
    for index, (title, types, alternative, limit) in enumerate(hypotheses, 1):
        controller.store.add('hypothesis', case_id, evidence_id=evidence_id, contract=contract,
            number=index, hypothesis_kind='coverage_domain', text=title, status='open', judgment='미확인', observation_ids=[], supporting_evidence_ids=[],
            refuting_evidence_ids=[], negative_searches=[], competing_explanations=[alternative], unavailable_materials=[limit],
            expected_source_types=types, uncertainty=limit, judgment_history=[{'at': now(), 'judgment': '미확인', 'reason': '상세 내용 조사 전 초기 등록'}])


def ranked(observations, question=''):
    terms = [t.casefold() for t in question.split() if len(t) > 2]
    def score(o):
        f = o['fields']; value = 0
        if o['type'].startswith(('linux_','windows_')): value += 10
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
    for i in range(max((len(bucket) for bucket in buckets.values()),default=0)):
        for bucket in buckets.values():
            if i < len(bucket): result.append(bucket[i])
    return result


def serialized_text_prefix(value, maximum=6000):
    """Bound the transmitted JSON text, including escaped control characters.

    This is presentation-only; callers retain the exact source/locator and mark
    any prefix as truncated. Character count alone can undercount by sixfold.
    """
    if len(json.dumps(value,ensure_ascii=False))<=maximum:return value
    low,high=0,min(len(value),maximum)
    while low<high:
        mid=(low+high+1)//2
        if len(json.dumps(value[:mid],ensure_ascii=False))<=maximum:low=mid
        else:high=mid-1
    return value[:low]


def compact_observation(original, *, preserve_content=False):
    """Planner preview by default; lossless source view for adjudication.

    Judgment pages must not inherit the planner's display prefix or list cap.
    The paging layer owns the bounded envelope; an indivisible source is a
    visible input gap, never permission to discard its tail.
    """
    o={k:original[k] for k in ('id','type','timestamp','source_location','fields')}
    f=dict(o['fields']);o['fields']=f
    for key,value in ([] if preserve_content else list(f.items())):
        if isinstance(value,str) and key not in ('path','artifact_path','source_sha256','os_instance','source_member','json_pointer','locator_basis'):
            prefix=serialized_text_prefix(value)
            if prefix!=value:f[key]=prefix;f[key+'_truncated']=True;f['context_compacted']=True
        elif isinstance(value,list) and len(value)>10:f[key]=value[:10];f[key+'_truncated']=True
    if not preserve_content and len(json.dumps(o,ensure_ascii=False))>10000:
        from .temporal import ALIASES
        time_keys={'file_context','time_basis','time_kind','time_type','partition_offset','inode','volume_id','snapshot_id',
            'source_offset','source_range_start','byte_length','original_source_sha256'}|{k for keys in ALIASES.values() for k in keys}
        o['fields']={k:v for k,v in f.items() if k in time_keys or k in ('path','rule_id','title','severity','excerpt','command','stage','source_sha256','source_complete','artifact_path','byte_offset','image_file_byte_offset','line','facts','interpretation_limit','capability_symbols','selected_strings','network_state','time_record','os_instance','imported_normalized','raw_source_reverified','source_member','json_pointer','source_row','locator_basis','event_id','event_record_id','process_id','channel','activity_context','parser_degraded','unmapped_fields')}
        o['fields']['context_compacted']=True
    from .retrieval import source_origin
    o['source_origin'] = source_origin(original)
    from .evidence_semantics import observation_time
    o['time_semantics']=observation_time(original)
    original_excerpt = original['fields'].get('excerpt', '')
    shown = o['fields'].get('excerpt', '')
    if original['type'].startswith('windows_') and original['type'] not in ('windows_environment','windows_correlation','windows_counterevidence') and original['fields'].get('artifact_path'):
        o['context_request']={'tool':'read_source','path':original['fields']['artifact_path'],'byte_offset':0,'byte_length':8192}
    if not original['type'].startswith('windows_') and original['fields'].get('path', '').startswith('/'):
        offset = original['fields'].get('image_file_byte_offset', original['fields'].get('source_offset', 0)) or 0
        o['context_request'] = {'tool':'read_file','path':original['fields']['path'],
            'byte_offset':max(0, offset-2048), 'byte_length':8192}
        if 'decompressed' in original['fields'].get('locator_basis',''):
            artifact=original['fields'].get('artifact_path','')
            o['context_request']={'tool':'read_source','path':artifact,
                'byte_offset':original['fields'].get('byte_offset',0),'byte_length':8192}
            if original['fields'].get('image_file_byte_offset') is not None:
                o['context_request']['source_offset']=max(0,original['fields']['image_file_byte_offset']-original['fields'].get('byte_offset',0))
        if len(original_excerpt)>len(shown):
            o['fields']['excerpt_truncated']=True
            o['context_request']['byte_length']=16384
            # Replacement decoding and redaction make text length unsuitable as
            # a byte locator. Re-read from a known original offset with overlap.
        for selector in ('partition_offset','inode'):
            if original['fields'].get(selector) is not None:o['context_request'][selector]=original['fields'][selector]
        o['context_limit']='Byte location is recorded where available; inspect the returned range before interpreting it.'
    return o


def evidence_pack(controller, case_id, question='', preferred=(), evidence_id=None, focus=(), presented=(), strategy=None, task_id=None, generation=None):
    obs = [o for o in controller.active_observations(case_id) if evidence_id is None or o['evidence_id']==evidence_id]
    selected = []; used = set(); length = 0
    by_id = {o['id']: o for o in obs}
    all_hypotheses=[h for h in controller.store.list('hypothesis',case_id)
        if (h.get('contract') in ('linux-v1','windows-v1') or h.get('hypothesis_kind')=='dynamic') and (evidence_id is None or h.get('evidence_id')==evidence_id)]
    dynamic=[h for h in all_hypotheses if h.get('hypothesis_kind')=='dynamic']
    if task_id is not None:
        dynamic=[h for h in dynamic if h.get('task_id')==task_id and h.get('generation',0)==generation]
    latest={}
    for h in dynamic:
        key=h.get('hypothesis_card_id') or h['id']; old=latest.get(key)
        if old is None or h.get('revision',0)>old.get('revision',0): latest[key]=h
    dynamic=list(latest.values())
    dynamic_total=len(dynamic)
    # Rotate bounded memory rather than permanently hiding the 13th hypothesis.
    offset=len(presented or [])%max(1,dynamic_total)
    dynamic=(dynamic[offset:]+dynamic[:offset])[:12]
    hypotheses=[h for h in all_hypotheses if h.get('hypothesis_kind','coverage_domain')=='coverage_domain']
    focused=[h for h in hypotheses if not focus or h.get('number') in focus]
    if strategy is None:
        configs=controller.store.list('config')
        strategy=(configs[-1]['provider'] if configs else {}).get('investigation_strategy','guided')
    from .evidence_selection import order, audit
    if strategy=='guided':ordered,reasons=order(obs,focused,preferred,presented,question)
    else:
        ordered=[by_id[oid] for oid in preferred if oid in by_id]+ranked(obs,question);reasons={}
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
    return {'target_os':controller.store.get(case_id).get('target_os','linux'),
            'available_tools':['search','read_source','correlate'] if controller.store.get(case_id).get('target_os')=='windows' else ['search','read_file','read_source','static_file','archive_list','correlate'],
            'observations': selected, 'total_observations': len(obs), 'included_observations': len(selected),
            'selection_is_partial': len(selected) < len(obs),
            'selection_audit':audit(obs,selected,reasons,focused,presented,strategy),
            'hypotheses': [{'number': h.get('number'), 'hypothesis_id': h.get('id'), 'kind': h.get('hypothesis_kind','coverage_domain'), 'question': h['text'], 'alternatives': h.get('competing_explanations'),
                            'remaining_checks':h.get('remaining_checks',[])[:6],
                            'status':h.get('status','open'),'unavailable': h.get('unavailable_materials')} for h in focused],
            'dynamic_hypotheses': [{'hypothesis_id': h.get('hypothesis_card_id', h.get('id')), 'record_id': h.get('id'),
                                    'number': h.get('number'), 'kind': 'dynamic', 'title': h.get('title', h.get('text')),
                                    'card_summary': h.get('card_summary',''), 'question': h.get('text'), 'revision': h.get('revision', 0),
                                    'hypothesis_card_id':h.get('hypothesis_card_id'),
                                    'scenario_assessment':h.get('scenario_assessment'),
                                    'lifecycle': h.get('lifecycle','investigating'), 'observation_ids': h.get('observation_ids', []), 'judgment': h.get('judgment')}
                                   for h in dynamic],
            'dynamic_hypotheses_total': dynamic_total, 'dynamic_hypotheses_omitted': max(0, dynamic_total-12)}


def store_tool_result(controller, case_id, evidence, task, result, request, *, bounded_lookup=False):
    receipt = controller.store.add('receipt', case_id, task_id=task['id'], evidence_id=evidence['id'], receipt_type='investigation_tool',
                                   request=request, result={k: v for k, v in result.items() if k != 'observations'}, output_count=len(result.get('observations', [])))
    ids = []
    if bounded_lookup:
        # Collector invokes this inside its writer-fenced transaction. Only
        # materialize matching canonical observations; never parse the entire
        # case ledger in Python while holding the writer transaction.
        digests=list(dict.fromkeys(hashlib.sha256(json.dumps([evidence['id'],event],
            sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            for event in result.get('observations',[])))
        if len(digests)>128:raise ValueError('bounded result ingest limit exceeded')
        rows=controller.store.db.execute("SELECT body FROM records WHERE kind='observation' AND case_id=? "
            "AND json_extract(body,'$.digest') IN ("+','.join('?' for _ in digests)+")",
            [case_id,*digests]).fetchall() if digests else []
        known={o['digest']:o for o in (json.loads(row[0]) for row in rows)}
    else:
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
