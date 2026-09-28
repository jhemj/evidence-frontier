"""Source-scoped time anchors. Never substitute file times for event times."""
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation
import re
from .evidence_semantics import observation_time, exact_utc_ns, windows_path

INFERRED=re.compile(r'assum|infer|추정',re.I)
FILE_TYPES={'filesystem_entry','filesystem_time','file_metadata','windows_file','linux_tool_result','linux_configuration','linux_persistence'}
LABELS={'ctime':'메타데이터 변경','mtime':'내용 수정','crtime':'파일 생성','atime':'접근'}
ALIASES={'ctime':('ctime_ns','ctime_epoch','ctime','change_time','last_change_time','ChangeTime'),
         'mtime':('mtime_ns','mtime_epoch','mtime','last_write_time','modified_time','LastWriteTime'),
         'crtime':('crtime_ns','birthtime_ns','crtime_epoch','birthtime_epoch','crtime','birthtime','creation_time','created_time','CreationTime'),
         'atime':('atime_ns','atime_epoch','atime','last_access_time','LastAccessTime')}
LIMIT='파일 시각은 시점 탐색의 단서입니다. 설정 등록·실행·침해 시각으로 단정할 수 없으며 복사·복원·시간 변경 가능성을 대조해야 합니다.'


def _stamp(value,key):
    if value in (None,'',0,'0') or isinstance(value,bool):return None
    try:
        if isinstance(value,str) and re.search(r'\d{4}-\d\d-\d\d[T ]',value):
            ns=exact_utc_ns(value)
            return (value,ns) if ns is not None else None
        number=Decimal(str(value))*(1 if key.endswith('_ns') else 1000000000)
        if not number.is_finite() or number!=number.to_integral_value() or number<=0:return None
        ns=int(number);sec,fraction=divmod(ns,1000000000)
        base=datetime(1970,1,1,tzinfo=timezone.utc)+timedelta(seconds=sec)
        raw=base.strftime('%Y-%m-%dT%H:%M:%S')+(('.'+f'{fraction:09d}'.rstrip('0')) if fraction else '')+'Z'
        return raw,ns
    except (ValueError,OverflowError,InvalidOperation):return None


def file_anchors(o):
    f=o.get('fields',{});containers=[('fields',f)]
    if isinstance(f.get('file_context'),dict):containers.insert(0,('fields/file_context',f['file_context']))
    if o.get('type') not in FILE_TYPES and len(containers)==1:return []
    result=[]
    for kind,keys in ALIASES.items():
        value=None;pointer=None
        for prefix,fields in containers:
            for key in keys:
                stamp=_stamp(fields.get(key),key)
                if stamp:value=stamp;pointer='/'+prefix+'/'+key;break
            if value:break
        if not value and f.get('time_type') in keys:
            value=_stamp(o.get('timestamp'),'timestamp');pointer='/timestamp'
        if value:
            result.append({'observation_id':o['id'],'raw':value[0],'epoch_nanoseconds':str(value[1]),
                'time_type':kind,'label':LABELS[kind],'pointer':pointer,'path':f.get('path',''),
                'basis':'증거 내부 파일시스템 메타데이터','relation':'same_file_context'})
    return result


def _identity(o):
    f=o.get('fields',{});path=f.get('path');eid=o.get('evidence_id')
    if not eid or not path:return None
    # No cross-artifact path joining when the partition/OS identity is missing.
    if f.get('partition_offset') is None and not (f.get('os_instance') and f.get('volume_id')):return None
    path=windows_path(path) if o.get('type','').startswith('windows_') or re.match(r'^[A-Za-z]:[\\/]',path) else path
    return (eid,str(f.get('partition_offset')),f.get('os_instance'),f.get('volume_id'),f.get('snapshot_id'),path)


class TimeContext:
    def __init__(self,observations):
        self.by_id={o['id']:o for o in observations}
        self.by_file=defaultdict(list)
        for o in self.by_id.values():
            anchors=file_anchors(o);key=_identity(o)
            if anchors and key:self.by_file[key].append((o,anchors))

    def event(self,refs):
        exact=[];inferred=[];metadata=[];missing=[]
        for oid in refs:
            o=self.by_id.get(oid)
            if not o:continue
            t=observation_time(o)
            if t['time_kind']=='occurred' and t['epoch_nanoseconds'] is not None:
                candidate={'observation_id':oid,'raw':t['raw'],'epoch_nanoseconds':t['epoch_nanoseconds'],
                    'basis':t['timezone_basis'],'relation':'cited_event','label':'행위 기록'}
                saved=o.get('fields',{}).get('time_record') or {}
                assumed=isinstance(saved,dict) and saved.get('timezone_assumed') is True
                (inferred if assumed or INFERRED.search(str(t.get('timezone_basis',''))) else exact).append(candidate)
            own=file_anchors(o);metadata.extend(own)
            key=_identity(o)
            if key and not own:
                for other,anchors in self.by_file.get(key,[]):
                    a=o.get('fields',{}).get('inode');b=other.get('fields',{}).get('inode')
                    if a is not None and b is not None and str(a)!=str(b):continue
                    metadata.extend({**anchor,'linked_from':oid,
                        'basis':anchor['basis']+(' · 동일 inode 대조' if a is not None and b is not None else ' · 동일 경로 대조, inode 일치 미확인')} for anchor in anchors)
            if t['epoch_nanoseconds'] is None:missing.append(oid)
        # Copies/expanded metadata rows aren't independent corroboration.
        unique={}
        for a in metadata:
            o=self.by_id[a['observation_id']]
            unique.setdefault((_identity(o) or o['id'],str(o.get('fields',{}).get('inode')),a['time_type'],a['epoch_nanoseconds']),a)
        metadata=list(unique.values())
        priority={'ctime':0,'mtime':1,'crtime':2,'atime':3}
        metadata.sort(key=lambda a:(priority[a['time_type']],int(a['epoch_nanoseconds']),a['observation_id']))
        ordered=lambda values:sorted(values,key=lambda a:(int(a['epoch_nanoseconds']),a['observation_id']))
        anchors=ordered(exact)+ordered(inferred)+metadata
        selected=(ordered(exact) or ordered(inferred) or metadata or [None])[0]
        kind='occurred' if exact else 'estimated' if inferred else 'file_metadata' if metadata else 'unknown'
        title={'occurred':'인용한 행위 기록 중 최초 시각','estimated':'로그 시각 추정 · 연도/시간대 근거 확인',
               'file_metadata':'파일 '+(selected['label'] if selected else '')+' 시각 참고 · 행위 시각 미확인',
               'unknown':'행위 시각 미확인'}[kind]
        refs_without_time=bool(missing)
        return {'raw':selected['raw'] if selected else None,'epoch_nanoseconds':selected['epoch_nanoseconds'] if selected else None,
            'time_sort_key':selected['raw'] if selected else None,'time_kind':kind,'time_label':title,
            'observation_id':selected['observation_id'] if selected else None,
            'time_basis':selected['basis'] if selected else None,
            'last_epoch_nanoseconds':ordered(exact)[-1]['epoch_nanoseconds'] if exact else None,
            'confidence':'source_record' if exact else 'provisional' if inferred else 'context_only' if metadata else 'unknown',
            'anchors':anchors[:8],'anchor_count':len(anchors),'anchors_omitted':max(0,len(anchors)-8),
            'limitation':LIMIT if metadata else '추정 연도·시간대와 시계 오차를 검증하기 전 확정 시각으로 사용하지 않습니다.' if inferred else '',
            'needs_time_followup':kind!='occurred' or refs_without_time,
            'next_checks':(['같은 증거·파티션·파일의 시각 메타데이터와 전후 원문 로그를 대조',
                'ctime은 생성/실행 시각이 아님; mtime·생성 시각·로그 시계·복사/복원/시간 변경 가능성을 검증'] if kind!='occurred' or refs_without_time else [])}
