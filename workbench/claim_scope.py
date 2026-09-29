"""Propagate source lineage/time uncertainty without reinterpreting a verdict."""
from copy import deepcopy
from .temporal import TimeContext
from .retrieval import source_origin


def qualify(finding,observations):
    result=deepcopy(finding)
    ids=list(dict.fromkeys(finding.get('observation_ids',[])+finding.get('counterevidence_ids',[])))
    rows=[observations[i] for i in ids if i in observations]
    origins=sorted({source_origin(o) for o in rows})
    result['source_scope']={'observation_count':len(rows),'origin_count':len(origins),
        'origin_ids':origins,'independence_established':False,
        'limitation':'동일 원문 재추출·파싱·AI 해석은 독립 증거가 아닙니다. 다른 원문도 독립성은 별도 확인이 필요합니다.'}
    result['temporal_scope']=TimeContext(observations.values()).event(ids)
    for stage in result.get('stages',[]):
        stage['temporal_scope']=TimeContext(observations.values()).event(stage.get('observation_ids',[]))
    return result
