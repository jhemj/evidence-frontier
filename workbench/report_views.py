"""One deterministic, redacted block model for HTML and editable Word.

No provider or worker calls here. Reader projections are derived once from the
same immutable ReportDocument. Findings and their IDs are shared across views.
"""
from datetime import datetime, timezone
from .report_redaction import redact

VERSION='ko-dual-report-2'
GLOSSARY={
    'linux_authentication':'인증 기록은 접속 허용·실패를 보여 줍니다. 계정 소유자 본인의 행위나 접속 후 실행을 단독으로 증명하지는 않습니다.',
    'linux_command':'명령 문자열은 기록된 입력·호출의 흔적입니다. 실행 완료, 연결 성립, 자료 전송 성공은 별도 기록으로 확인해야 합니다.',
    'linux_persistence':'예약·시작 설정은 지속 실행 경로가 등록되어 있음을 뜻합니다. 실제 호출·실행 여부와 악성 의도는 별도 판단입니다.',
    'linux_binary':'파일 정적 분석은 파일의 구조와 기능 가능성을 보여 줍니다. 이 시스템에서 실행되었다는 증거와는 다릅니다.',
    'linux_detection':'탐지 규칙에 해당한 원문 단서입니다. 탐지 이름 자체가 침해 확정이나 행위자 귀속을 뜻하지는 않습니다.',
    'linux_network':'통신 관련 기록은 관측된 단계에 한정합니다. 명령 인자에 주소가 있다는 사실과 실제 통신 성공은 구별합니다.',
}
LABELS={'path':'원본 경로','user':'계정','target_user':'목적 계정','command':'기록된 명령','event':'이벤트',
    'rule':'탐지 규칙','excerpt':'관측 발췌','message':'기록 내용','stage':'기록된 단계','interpretation_limit':'해석 한계'}
MISSING='자료에 기록되지 않음'
STAGES={'configuration':'설정','invocation':'호출','execution':'실행','connection':'연결','objective':'목적 달성','intent':'의도'}
STATUS={'covered':'지정 범위 처리','covered_zero':'지정 범위 내 0건','partial':'일부 처리','queued':'대기','running':'진행 중',
    'failed':'실패','blocked':'확인 필요','unsupported':'미지원','reviewed':'검토됨','model_failed':'모델 검증 실패',
    'input_projection_blocked':'입력 구성 한계로 미검토',
    'deferred':'보류','unavailable':'자료 미확보','source_unavailable':'자료 미확보','budget_exhausted':'검토 예산 소진',
    'external_required':'외부 자료 필요','image_extractable':'이미지 추가 추출','retained':'현재 보존 자료','unavailable_or_unsupported':'미확보·미지원'}


def text(value):
    if value is None or value=='':return MISSING
    if isinstance(value,list):return ' / '.join(text(v) for v in value) or '등록 없음'
    if isinstance(value,dict):return '; '.join(f'{k}: {text(v)}' for k,v in value.items()) or '등록 없음'
    return str(value)


def observation_fact(o):
    f=o.get('fields',{})
    parts=[f'{label}: {text(f[k])}' for k,label in LABELS.items() if f.get(k) not in (None,'',[])]
    return '\n'.join(parts) if parts else f"{o['type']} 관측. 상세 해석 자료 없음."


def event_time(o):
    f=o.get('fields',{})
    from .evidence_semantics import observation_time
    record=observation_time(o)
    kinds={'occurred':'행위 기록','file_metadata':'파일 메타데이터','collected':'수집','observed':'관측','ingested':'적재','derived':'파생','unknown':'미상'}
    from .temporal import INFERRED
    saved=f.get('time_record') or {}
    assumed=(isinstance(saved,dict) and saved.get('timezone_assumed') is True) or INFERRED.search(str(record['timezone_basis']))
    qualifier=' · 연도/시간대 추정' if assumed else ' · 시간대 미확인' if record['raw'] and record['epoch_nanoseconds'] is None else ''
    return text(record['raw'])+'\n시간 종류: '+kinds[record['time_kind']]+qualifier+' · 시간 근거: '+text(record['timezone_basis'])


def brief(value,limit=180):
    return value if len(value)<=limit else value[:limit]+'… (전문은 분석가용 상세 참조)'


def time_key(o):
    from .evidence_semantics import exact_utc_ns
    exact=exact_utc_ns(o.get('timestamp'))
    if exact is not None:return (0,exact,o['id'])
    try:
        stamp=datetime.fromisoformat(o.get('timestamp') or '')
        if stamp.tzinfo:return (1,stamp.astimezone(timezone.utc).isoformat(),o['id'])
    except ValueError:pass
    return (2,o.get('timestamp') or '',o['id'])


def project(document):
    from .semantic_contract import assertion_errors, report_gate
    raw={o['id']:o for o in document['observations']}
    for judgment in document.get('judgments',[]):
        for finding in judgment['findings']:
            if assertion_errors(finding,raw,set(raw)):
                raise ValueError('보고서의 원문 필드값 검증에 실패했습니다.')
    for synthesis in document.get('case_synthesis',[]):
        if assertion_errors(synthesis['finding'],raw,set(raw)):
            raise ValueError('최종 종합의 원문 필드값 검증에 실패했습니다.')
    d=redact(document)
    observations={o['id']:o for o in d['observations']}
    dossiers={x['id']:x for x in d.get('dossiers',[])}
    findings=[];history=[];seen=set()
    for judgment in d.get('judgments',[]):
        for f in judgment['findings']:
            did=f.get('dossier_id');owner=dossiers.get(did)
            identity=did or (judgment['id'],f['title'],tuple(f.get('observation_ids',[])))
            if identity in seen:continue
            seen.add(identity)
            if (f.get('timeline_role')=='반증됨' or not f.get('observation_ids')
                or owner and owner['status']!='reviewed'
                or f.get('timeline_role') in ('coverage-only','범위 설명')):
                history.append({'finding':f,'status':owner['status'] if owner else '현재 집계 제외'});continue
            refs=list(dict.fromkeys(f.get('observation_ids',[])+f.get('counterevidence_ids',[])+[i for s in f.get('stages',[]) for i in s.get('observation_ids',[])]))
            if not set(refs).issubset(observations):raise ValueError('배포 보고서 근거 연결이 끊어졌습니다.')
            findings.append({**f,'dossier_id':did or MISSING,'refs':refs,'receipt_id':(owner or judgment).get('receipt_id',MISSING)})
    findings.sort(key=lambda f:(('확인','유력','미확인').index(f['judgment']),str(f['dossier_id']),f['title']))
    gate=report_gate(d,findings)
    for i,f in enumerate(findings,1):f['label']=f'F-{i:03d}'
    counts={level:sum(f['judgment']==level for f in findings) for level in ('확인','유력','미확인')}
    completion=d.get('completion',{})
    status=completion.get('label','조사 상태 미등록')
    if not completion.get('execution_terminated'):status='조사 진행 중 — 기준시각 현재'
    scope=(f"검토 단위 {completion.get('review_units',0)}개 중 미검토 {completion.get('unreviewed_units',0)}개. "
        f"미실행 검사 {d.get('check_ledger',{}).get('not_executed',0)}개, 결과 미평가 계약 {d.get('check_ledger',{}).get('unassessed_contracts',0)}개. "
        '최초 유입·실제 실행·수평이동·유출은 각 항목의 근거 범위까지만 판단하며, 기록 부재로 정상 또는 행위 부재를 확정하지 않습니다.')
    integrity_notes=[e.get('integrity_scope',{}).get('scope_note') for e in d['evidence']]
    integrity_notes=list(dict.fromkeys(note for note in integrity_notes if note))
    if integrity_notes:
        scope+=' 입력 해시 계산과 취득 당시 원본 해시 대조는 별개이며, 대조 범위는 분석가용 상세에 기재했습니다.'
    counts_text='판단 항목 수: '+' · '.join(f'{level} {n}개' for level,n in counts.items())+' (침해 사건·서버·파일 수가 아님)'
    highlights=sorted(findings,key=lambda f:f.get('timeline_role')!='핵심')
    summary='\n'.join(f"{f['label']} [{f['judgment']}] {f['title']}" for f in highlights[:3]) or '현재 근거 검토를 마친 판단이 없습니다.'
    if len(findings)>3:summary+=f'\n나머지 {len(findings)-3}개 판단은 분석가용 상세에 수록했습니다.'
    # File metadata and collection times must not become the first intrusion time.
    cited={oid for f in findings for oid in f['refs']}
    from .temporal import TimeContext, LIMIT as FILE_TIME_LIMIT
    times=TimeContext(observations.values())
    cited_times={oid:times.event([oid]) for oid in cited}
    key_events=sorted([o for oid,o in observations.items() if oid in cited and cited_times[oid]['time_kind']=='occurred'],key=time_key)
    estimated_events=sorted([o for oid,o in observations.items() if oid in cited and cited_times[oid]['time_kind']=='estimated'],key=time_key)
    metadata_anchors={}
    for entry in cited_times.values():
        for anchor in entry['anchors']:
            if anchor.get('relation')=='same_file_context':
                metadata_anchors.setdefault((anchor['observation_id'],anchor['pointer']),anchor)
    # A reader-layout limit, never an investigation or evidence retention limit.
    executive_events=key_events[:4]
    first=event_time(key_events[0]) if key_events else '관련 원문 이벤트 시각 미확인'
    common={'version':VERSION,'report_id':d['id'],'case_id':d['case']['id'],'generated_at':d['generated_at'],
        'title':d['case']['name']+' 침해조사 결과 보고','target_os':d['case'].get('target_os','linux'),'status':status,'counts':counts,'counts_text':counts_text,
        'redaction_notice':'배포용: 탐지된 비밀번호·토큰·인증값을 삭제했습니다. 원문 근거 패키지는 별도 제한 배포 대상입니다.',
        'snapshot':d.get('snapshot',{}),'synthesis_gate':gate,'finding_map':[{k:f[k] for k in ('label','title','judgment','dossier_id','refs','receipt_id')} for f in findings]}
    def p(value):return {'kind':'paragraph','text':text(value)}
    def h(value,level=1):return {'kind':'heading','text':value,'level':level}
    def table(headers,rows,widths=None):return {'kind':'table','headers':headers,'rows':[[text(c) for c in r] for r in rows], 'widths':widths}
    def refs(ids):return {'kind':'citations','ids':ids}
    def target_rows():
        return [[e.get('name',e.get('path')),e['id'],e.get('role','용도 미등록'),'연결된 조사 대상 · 침해 여부는 개별 판단 참조'] for e in d['evidence']]
    executive=[h('□ 요약'),table(['구분','주요 내용'],[
        ['핵심 판단',summary],['발생 시점',first+'\n관련 기록 중 처음 제시한 시각이며 최초 침입 시각을 뜻하지 않습니다.'],
        ['대상 및 영향',f"연결 증거 {len(d['evidence'])}개. 확인된 영향 범위는 각 판단 항목에 한정합니다."],
        ['대응 사항','실제 대응 수행 여부 미등록. 아래 권고와 수행 사실을 구별합니다.'],['조사 한계',scope]], [24,146]),p(counts_text),
        h('□ 조사 대상 및 주요 결과'),table(['대상','근거 ID','용도','확인 범위'],target_rows(),[38,42,27,63])]
    executive += [table(['판단 ID','등급','주요 확인 사실'],[[f['label'],f['judgment'],f['title']] for f in highlights[:6]],[20,22,128])]
    # Four is a reader-layout limit, never a cap on generated hypotheses.
    synthesis=sorted(d.get('case_synthesis',[]),key=lambda s:(
        {'핵심':0,'보조':1,'참고':2,'반증됨':3}.get(s.get('finding',{}).get('timeline_role'),2),
        not bool(s.get('supporting_evidence_ids')),s.get('number',0)))[:4]
    if synthesis:
        executive += [p('최종 종합 주요 답변 · 아래 가설은 단서 판단 항목 수에 중복 합산하지 않습니다.'),
            table(['사건 질문','종합 판단'],[[s['question'],'['+s['finding']['judgment']+'] '+brief(s['finding']['reason'],140)] for s in synthesis],[50,120])]
    executive += [{'kind':'page_break'},h('□ 주요 경과'),p('원문 이벤트 시각을 표시합니다. 시간대가 없는 값은 임의로 한국 시각으로 변환하지 않습니다. 시간순 배열은 인과관계를 증명하지 않습니다.'),
        table(['원문 시각 / 근거','대상','기록된 사실'],[[event_time(o),o['evidence_id'],brief(observation_fact(o),90)] for o in executive_events],[48,40,82]) if executive_events else p('판단과 연결된 시각 기록이 없어 주요 경과를 구성하지 않았습니다.'),
        p(f'판단 근거 시각 기록 {len(key_events)}개 중 요약 {len(executive_events)}개. 전체 판단 근거 시각은 분석가용 상세에 수록했습니다.'),
        h('□ 대응 필요 사항 및 한계'),p('수행된 대응: 수행 여부 미등록. 보고서 작성 또는 분석 도구 실행을 시스템 차단·복구 조치로 집계하지 않습니다.'),
        p('권고: 관련 원문과 로그를 보존하고, 아래 자료 확보 및 경쟁 설명 검증을 진행하십시오. 차단·계정 변경 등 외부 시스템 조치는 별도 승인과 영향 검토가 필요합니다.')]
    needs=d.get('required_materials',[])
    executive += [table(['필요 자료','판단에 미치는 영향'],[[m['material'],m['blocked_conclusion']] for m in needs[:4]],[85,85]) if needs else p('구체적인 추가 자료 요청이 등록되지 않았습니다. 자료의 충분성을 뜻하지 않습니다.'),
        p(scope),p('분석가용 상세의 동일한 F-번호에서 관측·검사·반대 설명·원문 위치를 확인할 수 있습니다. 후속 갱신은 이 스냅샷에 포함되지 않습니다.')]
    analyst=[h('□ 결과 요약'),p(summary),p(counts_text),p(scope),h('□ 조사 대상과 신뢰 범위'),
        table(['대상','근거 ID','용도','확인 범위'],target_rows(),[38,42,27,63]),
        *[p(note) for note in integrity_notes],p(completion.get('scope',MISSING)),
        p(f"색인 관측 {completion.get('indexed_observations',0)} / AI 제시 {completion.get('ai_presented_observations',0)} / 단서 판단 인용 {completion.get('judgment_cited_observations',0)}. 고유 관측 ID 수이며 원문 전수검토율이 아닙니다."),
        table(['자료군','발견','읽기','보존','파싱','미처리'],[[x.get('family'),x.get('discovered'),x.get('read'),x.get('retained'),x.get('text_parsed'),x.get('unprocessed')] for x in completion.get('collection_denominators',[])],[55,23,23,23,23,23]),
        table(['조사 영역','상태','범위 제한'],[[c.get('label',c.get('action')),STATUS.get(c.get('status'),c.get('status')),c.get('error') or '별도 오류 미등록'] for c in d.get('coverage',[])],[47,32,91]),
        ]
    if completion.get('windows_source_coverage'):
        analyst += [h('□ Windows 소스별 보존·파싱 범위'),
            p('정규화 안전본은 원본 디스크 재검증과 다릅니다. 기록 수, 엔진 시작 수, 시간대별 묶음 수를 구별하며 결과 로그의 보존기간 밖 요청은 성공·실패 미상입니다.'),
            table(['소스 / 상태','레코드 분모','시각 / 제한'],[[x.get('source','미등록')+'\n'+text(x.get('status')),
                text({k:x[k] for k in ('source_records_reported','records_seen','parsed_records','failed_records','degraded_records','unread_records') if k in x}),
                text({k:x[k] for k in ('first_observed','last_observed','retention_first_reported','retention_last_reported','retention_continuity','error','errors','absence_is_refutation') if k in x})]
                for x in completion['windows_source_coverage']],[55,45,70])]
    analyst += [h('□ 원문 기반 사건 가설과 행위 흐름'),p('동일 모델의 반복 검토는 독립 증거가 아닙니다. 설정 → 호출 → 실행 → 연결 → 목적 달성 → 의도는 각각 별도 주장입니다.')]
    for s in d.get('case_synthesis',[]):
        f=s['finding'];analyst += [h(f"가설 {s['number']} · {f['title']}",2),p(f"[{f['judgment']}] {f['reason']}"),p(f"종합 상태: {s['status']} · 입력 {s['scope']['presented_observations']} / 관련 관측 {s['scope']['relevant_observations']}"),refs(f.get('observation_ids',[])),
            p('지지 근거'),refs(s.get('supporting_evidence_ids',[])),p('반대 근거 (자료 부재와 구별)'),refs(list(dict.fromkeys(s.get('refuting_evidence_ids',[])+f.get('counterevidence_ids',[])))),p('경쟁 설명: '+text(f.get('alternatives',[]))),p('남은 검사: '+text(f.get('remaining_checks',[])))]
        for stage in f.get('stages',[]):
            analyst += [p('종합 단계 · '+STAGES.get(stage['stage'],stage['stage'])+' ['+stage['judgment']+'] '+stage['statement']),refs(stage['observation_ids'])]
        for fact in f.get('fact_assertions',[]):
            analyst += [p('원문 필드 대조 · '+fact['pointer']+' · '+fact.get('operator','equals')+' · '+text(fact['value'])),refs([fact['observation_id']])]
    if not d.get('case_synthesis'):analyst.append(p('전체 가설 최종 종합이 아직 없습니다. 중간 단서 판단만으로 전체 조사가 끝났다고 해석하지 마십시오.'))
    for link in d.get('session_links',[]):
        linked=list(dict.fromkeys(oid for oid in link.get('observation_ids',[]) if oid in cited))
        if not linked:continue
        analyst += [h('세션 연결 후보 · '+text(link.get('key')),2),
            p(link.get('limitation')),
            p('경쟁 설명: '+text(link.get('alternative'))),
            p(f"그룹 원장 관측 {len(link.get('observation_ids',[]))}개 · 현재 판단 근거와 연결된 관측 {len(linked)}개. 본문에는 교차한 관측만 인용했습니다."),
            refs(linked)]
    analyst.append(h('□ 현재 판단 전체'))
    for f in findings:
        analyst += [h(f"{f['label']} · [{f['judgment']}] {f['title']}",2),p('결론: '+f['reason'])]
        for fact in f.get('fact_assertions',[]):
            analyst += [p('원문 필드 대조 · '+fact['pointer']+' · '+fact.get('operator','equals')+' · '+text(fact['value'])),refs([fact['observation_id']])]
        for oid in f['refs']:
            o=observations[oid];analyst += [p('실제 관측 · '+oid+'\n'+observation_fact(o))]
        meanings=list(dict.fromkeys(GLOSSARY.get(observations[oid]['type'],'상세 해석 자료 없음. 이 관측은 해당 원문의 기록 범위에 한정하여 해석합니다.') for oid in f['refs']))
        analyst += [p('자료의 의미: '+text(meanings)),table(['행위 단계','판단','해석 / 근거'],[[STAGES.get(s['stage'],s['stage']),s['judgment'],s['statement']+'\n'+text(s.get('observation_ids',[]))] for s in f.get('stages',[])],[27,22,121]),
            p('반대 근거 (자료 부재와 구별)'),refs(f.get('counterevidence_ids',[])),p('경쟁 설명·상충: '+text(f.get('alternatives',[]))),p('미확인·남은 검사: '+text(f.get('remaining_checks',[])))]
        matched=[(c,x) for c in d.get('check_ledger',{}).get('checks',[]) for x in c['contracts'] if x['dossier_id']==f['dossier_id']]
        analyst += [p('수행 검사: '+ ('아래 검사 원장에 같은 검토 단위의 결과를 연결했습니다.' if matched else '이 판단에 연결된 추가 검사 평가가 등록되지 않았습니다.'))]
        for c,x in matched:
            a=x.get('assessment') or {};analyst += [p(text(c['request'])+'\n실행: '+c['execution_status']+' · 평가: '+x['evaluation_status']+' · 결과: '+text(a.get('outcome'))+'\n'+text(a.get('reason'))),refs(a.get('observation_ids',[]))]
        analyst += [p('검토 단위: '+text(f['dossier_id'])+'\n모델 검토 기록: '+text(f['receipt_id'])),refs(f['refs'])]
    analyst += [h('□ 추가 검사와 대안 검증 원장'),p(d.get('check_ledger',{}).get('scope',MISSING))]
    frontier=d.get('investigation_frontier',{})
    if frontier:
        analyst += [h('□ 새 단서와 후속 조사 상태'),p(frontier.get('scope')),
            p(f"미완료 단서 {frontier.get('open_leads',0)}개 · 재검색 보류 {frontier.get('deferred_discovery',0)}개 · 변경 없는 검토 재사용 {completion.get('reused_review_units',0)}개. 재사용은 새로운 독립 검토가 아닙니다.")]
        for lead in frontier.get('leads',[]):
            if lead['disposition']=='excluded':continue  # Already retained in the history section.
            analyst += [p(lead['title']+' · '+lead['state']+' · '+lead['disposition']+'\n'+text(lead.get('reason'))),
                        p('남은 검사: '+text(lead.get('next_tests',[])))]
        for candidate in frontier.get('discovery_inventory',[]):
            analyst += [p('재검색 단서: '+candidate['query']+' · '+candidate['state']+'\n'+candidate['reason']),refs(candidate['observation_ids'])]
    analyst += [p('최종 판단 포함 검증: 현재 검토 완료 판단의 포함/제외 이유와 필수 근거 연결을 검사했습니다. 원문 전수검토 또는 모든 문장의 의미적 정확성을 뜻하지 않습니다.')]
    for c in d.get('check_ledger',{}).get('checks',[]):
        analyst += [h('검사 · '+c['scope_key'][:16],2),p(text(c['request'])),p('실행 상태: '+c['execution_status']+'\n실제 작업 ID: '+text(c['job_ids']))]
        for x in c['contracts']:
            a=x.get('assessment') or {};analyst += [p('검토 단위: '+x['dossier_id']+'\n성공 조건: '+text(x.get('success_condition'))+'\n반증 조건: '+text(x.get('refutation_condition'))+'\n평가 상태: '+x['evaluation_status']+' · 결과: '+text(a.get('outcome'))+'\n평가: '+text(a.get('reason'))),refs(a.get('observation_ids',[]))]
    analyst += [h('□ 판단 근거 시각 기록'),p('원문 시간 의미·시간대가 확인되는 기록만 비교 가능합니다. 파일 시각·수집 시각과 침해 시작 시각을 혼용하지 않습니다.'),
        table(['시각 / 시간 근거','관측 ID','기록된 사실'],[[event_time(o),o['id'],observation_fact(o)] for o in key_events],[48,40,82]) if key_events else p('판단에 연결된 시각 기록이 없습니다.'),
        h('연도·시간대 추정 기록',2),
        table(['추정 시각 / 가정','관측 ID','기록된 사실'],[[event_time(o),o['id'],observation_fact(o)] for o in estimated_events],[48,40,82]) if estimated_events else p('판단에 인용된 추정 행위 시각이 없습니다.'),
        h('파일 시각 참고 · 행위 시각 아님',2),p(FILE_TIME_LIMIT),
        table(['파일 시각 / 의미','관측 ID / 필드','대상 / 근거'],[[a['raw']+'\n'+a['label'],a['observation_id']+'\n'+a['pointer'],a['path']+'\n'+a['basis']]
            for a in sorted(metadata_anchors.values(),key=lambda a:(int(a['epoch_nanoseconds']),a['observation_id']))],[48,40,82]) if metadata_anchors else p('인용 근거와 같은 파일로 연결되는 시각 메타데이터가 없습니다.'),
        refs(list(dict.fromkeys([o['id'] for o in estimated_events]+[a['observation_id'] for a in metadata_anchors.values()]))),
        p('파일 시각 참고는 단서별 최대 8개 시간 근거의 표시용 투영입니다. 전체 시각·필드는 원문 원장에 보존되며 미표시를 부재로 해석하지 않습니다.'),
        p('시각 미상 기록은 전체 색인 목록을 본문에 나열하지 않습니다. 판단에 실제 인용된 원문의 위치·해시는 아래 근거 부록과 별도 원장에 보존합니다.'),
        h('□ 미확인 사항과 후속 확보 자료'),table(['구분 / 참조','필요 자료','막힌 결론 / 후속 행동'],[[STATUS.get(m['category'],m['category'])+'\n'+m['reference'],m['material'],m['blocked_conclusion']+'\n'+m['action']] for m in needs],[40,65,65]),
        h('□ 판단 이력 및 현재 집계 제외 항목')]
    excluded=history+[x for x in d.get('dossiers',[]) if x['status']!='reviewed']+d.get('dossier_history',[])
    for x in excluded:
        f=x.get('finding') or {};analyst += [p(text(x.get('id'))+' · '+text(x.get('status'))+' · '+text(f.get('title',x.get('title')))+'\n'+text(f.get('reason',x.get('error')))),refs(f.get('observation_ids',[]))]
    for c in d.get('automatic_findings',[]):analyst += [p('중간 해석 (최종 판단 집계 제외): '+text(c.get('text'))),refs(c.get('observation_ids',[]))]
    for r in d.get('review_failures',[]):analyst.append(p('검증 실패 · '+text(r.get('failure_category'))+' · '+r['id']+'\n'+text(r.get('error'))))
    if not excluded and not d.get('automatic_findings') and not d.get('review_failures'):analyst.append(p('등록된 제외 항목·과거 판단·검증 실패 이력이 없습니다.'))
    analyst += [h('□ 부록 · 판단과 원문 근거 매핑'),table(['판단 ID','검토 단위','관측 ID'],[[f['label'],f['dossier_id'],text(f['refs'])] for f in findings],[20,55,95])]
    # Only observations that are actually cited by a judgment, stage, check,
    # history item, synthesis, or displayed discovery receive reader anchors.
    anchor_ids={oid for block in analyst if block['kind']=='citations' for oid in block['ids']}
    anchor_ids.update(o['id'] for o in key_events)
    if not anchor_ids.issubset(observations):raise ValueError('분석가 보고서에 원문 연결이 없는 인용이 있습니다.')
    common['source_projection']={'indexed_observations':len(observations),'reader_sources':len(anchor_ids),
        'uncited_observations':len(observations)-len(anchor_ids),'basis':'actual_reader_citations'}
    analyst.append(p(f'관측 원장 {len(observations)}개 · 본문 근거 {len(anchor_ids)}개 · 본문 미인용 {len(observations)-len(anchor_ids)}개. '
        '전체 관측은 제한 배포 패키지의 report.json에 보존합니다. REPORT_EVIDENCE_MAP.csv는 추출 근거 위치 매핑이며, '
        'TIMELINE.csv는 보존된 시간 기록입니다. 각 원장의 범위는 전체 디스크 전수검토를 뜻하지 않습니다.'))
    for o in observations.values():
        if o['id'] not in anchor_ids:continue
        fields=o.get('fields',{})
        analyst += [{'kind':'anchor','id':o['id']},h(o['id']+' · '+o['type'],2),p('증거: '+o['evidence_id']+'\n위치: '+text(o.get('source_location'))),
            p('원본 경로: '+text(fields.get('path'))+'\n파티션 오프셋: '+text(fields.get('partition_offset'))+' · inode: '+text(fields.get('inode'))),
            p('좌표 기준: '+text(fields.get('locator_basis'))+'\n행: '+text(fields.get('line'))+' · 바이트 위치: '+text(fields.get('byte_offset'))+' · 길이: '+text(fields.get('byte_length'))),
            p('보존 산출물 SHA-256: '+text(fields.get('source_sha256'))+'\n도구 영수증: '+text(o.get('receipt_id'))),p(event_time(o))]
        if o['type'].startswith('windows_'):
            analyst += [p('Windows 출처·해석 범위: '+text({k:fields[k] for k in ('os_instance','source_kind','imported_normalized','raw_source_reverified','source_member','json_pointer','original_source_sha256','hash_scope','activity_context','path_resolution','interpretation_limit') if k in fields}))]
    analyst += [h('□ 부록 · 관측 지표 후보'),p('주소·해시·도메인은 관측 지표 후보이며 단독으로 악성 지표를 확정하지 않습니다.')]
    cited_indicators=0
    for o in observations.values():
        if o['id'] not in anchor_ids:continue
        for indicator in o.get('fields',{}).get('indicators',[]):analyst += [p(text(indicator)),refs([o['id']])]
        cited_indicators+=len(o.get('fields',{}).get('indicators',[]))
    total_indicators=sum(len(o.get('fields',{}).get('indicators',[])) for o in observations.values())
    if not cited_indicators:analyst.append(p('판단에 연결된 정형 지표 후보가 등록되지 않았습니다.'))
    analyst.append(p(f'정형 지표 후보 원장 {total_indicators}개 중 본문 인용 {cited_indicators}개. 나머지는 report.json 및 별도 원장에 보존합니다.'))
    analyst += [h('□ 부록 · 재현 정보와 용어'),p('배포 보고서에는 원문 파일·원시 JSON을 자동 첨부하지 않습니다. 권한 있는 분석가는 별도 제한 배포 패키지의 manifest.json, SHA256SUMS와 원문 위치를 대조할 수 있습니다.'),
        p('렌더러: '+VERSION+'\n스냅샷 범위 해시: '+text(d.get('snapshot',{}).get('scope_sha256')))]
    for e in d['evidence']:
        for segment in e.get('segment_manifest',[]):analyst.append(p('증거 세그먼트: '+text(segment.get('name'))+'\nSHA-256: '+text(segment.get('sha256'))))
    for r in d.get('tool_receipts',[]):
        analyst.append(p('영수증 '+r['id']+' · '+text(r.get('receipt_type',r.get('tool')))+'\n모델/도구: '+text(r.get('model',r.get('version')))+' · 기록 시각: '+text(r.get('created_at'))))
    analyst += [p('확인: 명시한 사실의 근거가 확인됨. 유력: 가장 타당한 해석이나 대안이 남음. 미확인: 자료·검사로 결론을 내리기 어려움. 이 등급은 침해 사건 수나 악성 여부 자체가 아닙니다.')]
    analyst += [p(v) for v in GLOSSARY.values()]
    if d.get('preview_omitted_observations'):
        for blocks in (executive,analyst):blocks.insert(0,p(f"저장 전 미리보기: 인용되지 않은 관측 {d['preview_omitted_observations']}개는 본문에서 생략했습니다. 전체 관측은 report.json 원장에, 추출 근거 위치는 해당 별도 매핑 원장에 보존합니다."))
    used={oid for b in analyst if b['kind']=='citations' for oid in b['ids']}
    if not used.issubset(anchor_ids):raise ValueError('분석가 보고서의 인용 근거 부록이 누락되었습니다.')
    return {reader:{**common,'reader':reader,'reader_name':label,'blocks':blocks} for reader,label,blocks in
        [('executive','임원용 요약',executive),('analyst','분석가용 상세',analyst)]}
