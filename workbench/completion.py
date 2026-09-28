"""Separate execution termination, collection coverage, and analyst judgments."""


def presentations(store,cid,tasks):
    current=lambda r:r.get('task_id') in tasks and r.get('generation',0)==tasks[r['task_id']].get('retry_generation',0)
    rows=[r for r in store.list('review_input',cid) if current(r)]
    rows += [{'included_ids':r.get('valid_ids',[])} for r in store.list('investigation_plan',cid) if current(r)]
    rows += [{'included_ids':[o['id'] for o in r['pack']['observations']]} for r in store.list('synthesis_input',cid) if current(r)]
    return rows


def project(document, review_inputs):
    observations=document['observations'];ids={o['id'] for o in observations}
    units=document['dossiers'];presented=set()
    for r in review_inputs:presented.update(set(r.get('included_ids',[]))&ids)
    judged={oid for d in units if d['status']=='reviewed' for oid in (d.get('finding') or {}).get('observation_ids',[]) if oid in ids}
    sources=[];windows_sources=[]
    for o in observations:
        if o['type']=='windows_environment':
            windows_sources.extend({'evidence_id':o['evidence_id'],**row} for row in o['fields'].get('source_coverage',[]))
        if o['type']!='linux_environment':continue
        for family,counts in o['fields'].get('coverage_map',{}).get('families',{}).items():
            sources.append({'evidence_id':o['evidence_id'],'family':family,**counts})
    statuses=[t.get('status') for t in document['coverage'] if t.get('action')!='investigation_report']
    terminated=bool(statuses) and not any(s in ('queued','running') for s in statuses)
    unreviewed=sum(d['status']!='reviewed' for d in units)
    ledger=document['check_ledger']
    gaps=any(s not in ('covered','covered_zero') for s in statuses) or unreviewed or ledger['not_executed'] or ledger['unassessed_contracts']
    gaps=gaps or any(s['status']!='reviewed' or s.get('selection_is_partial') for s in document.get('case_synthesis',[]))
    gaps=gaps or any(not s.get('complete',False) for s in windows_sources)
    frontier=document.get('investigation_frontier',{})
    gaps=gaps or frontier.get('open_leads',0) or frontier.get('deferred_discovery',0)
    return {'execution_terminated':terminated,'analysis_complete_in_supported_scope':terminated and not gaps,
        'label':'실행 종료 · 분석 공백 있음' if terminated and gaps else '지원 범위 처리 완료' if terminated else '진행 중 스냅샷',
        'collection_denominators':sources,'windows_source_coverage':windows_sources,'indexed_observations':len(ids),'ai_presented_observations':len(presented),
        'judgment_cited_observations':len(judged),'review_units':len(units),'unreviewed_units':unreviewed,
        'open_leads':frontier.get('open_leads',0),'deferred_discovery':frontier.get('deferred_discovery',0),
        'reused_review_units':sum(bool(d.get('reused_from')) for d in units),
        'source_records_indexed':sum(o['fields'].get('events',0) for o in observations if o['type'] in ('linux_environment','windows_environment')),
        'scope':'Linux 수집 분모는 파일 수, Windows 수집 범위는 소스별 레코드 수·파싱 실패·보존 기간을 별도 표시. AI 제시·판단 인용은 고유 관측 ID 수. 표본/압축 문맥은 원문 전수검토가 아니며, 무결성 성공은 분석 완료가 아님.'}


def materials(document):
    rows=[]
    for check in document['check_ledger']['checks']:
        if check['job_ids'] and all(c['evaluation_status']=='assessed' for c in check['contracts']):continue
        rows.append({'category':'retained' if check['job_ids'] else 'image_extractable',
            'material':check['request'].get('path') or check['request'].get('query') or check['request']['tool'],
            'blocked_conclusion':check['request'].get('reason','가설 판별조건 미평가'),
            'action':'보존된 검사 결과를 가설별 재평가' if check['job_ids'] else '같은 이미지에서 명시된 범위 추가 추출',
            'reference':check['scope_key']})
    # Acquisition needs, not interpretive caveats mislabeled as missing files.
    requirements={
        1:[('external_required','최초 노출 이전의 인증·웹·VPN 로그와 보존 정책','최초 유입 경로·시각')],
        2:[('external_required','작업 승인·계정 소유자·MFA/VPN/인증 서버 기록','계정 오용과 정당한 관리 접속 구별')],
        3:[('unavailable_or_unsupported','사건 시점 메모리·프로세스 실행/종료 원본 기록','프로그램 실행 결과·행위 귀속')],
        4:[('image_extractable','관측된 의심 바이너리의 전체 바이트·소유권·권한·해시','파일 기능과 경로별 동일성'),
           ('unavailable_or_unsupported','삭제 inode·미할당·swap 복구 산출물','삭제된 원본 파일 확보')],
        5:[('image_extractable','예약 설정의 대상 원본 파일과 같은 시점 cron/audit 로그','등록·호출과 실제 실행/종료 구별')],
        6:[('external_required','방화벽·NetFlow/패킷·상대 서버 연결 기록','연결 성립·외부 도달·통신 목적 달성')],
        7:[('external_required','시간 동기화·설정 변경 승인·외부 로그 보존 기록','시간 오차·로그 공백·변경 행위자')],
        8:[('external_required','연계 서버 원본 인증 로그·키 지문·파일 해시','수평이동과 정상 내부 접속 구별')],
        9:[('external_required','수신 서버 전송 로그·수신 바이트 수·파일 해시','특정 전송의 성공과 자료 유출 영향 범위')],
        10:[('external_required','점검/배포 작업표·서명된 원본 도구·승인된 실행 기록','정상 운영·점검 경쟁 설명 검증')],
    }
    for h in document['hypotheses']:
        for category,material,conclusion in requirements.get(h.get('number'),[]):
            rows.append({'category':category,'material':material,'blocked_conclusion':conclusion,
                'action':'별도 자료·접근 승인 필요' if category=='external_required' else
                    '보존 여부부터 확인 후 부족한 범위만 이미지에서 추출' if category=='image_extractable' else
                    '현재 수집·파서 범위 밖; 확보 가능성과 별도 분석 필요',
                'reference':h['id']})
    return rows
