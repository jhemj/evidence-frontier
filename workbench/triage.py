"""Read-only reader projection; never a scheduler, verdict, or IOC classifier.

Relevance is a disclosed presentation index of agent-assessed incident role and
source-linked follow-up work. It is not calibrated probability. Exact evidence
times order cards, independent of score. Unknown clocks stay outside that axis.
"""
from __future__ import annotations
from .temporal import TimeContext
from .card_evolution import lifecycle
from .hypothesis_ledger import current_scope

VERSION = 'triage-projection-6'
LOW_RELEVANCE = 30


def _event(by_id, refs):
    return TimeContext(by_id.values()).event(refs)


def project(document):
    evidence=document.get('evidence',[])
    active={e['id'] for e in evidence if e.get('connected',True)}
    scoped=lambda row:not evidence or not row.get('evidence_id') or row['evidence_id'] in active
    observations=document.get('observations',document.get('observation',[]))
    by_id={o['id']:o for o in observations if scoped(o)}
    time_context=TimeContext(by_id.values())
    tasks={t['id']:t for t in document.get('task',[]) if scoped(t) and not t.get('superseded')}
    findings={f['dossier_id']:f for j in document.get('judgments',[]) for f in j.get('findings',[]) if f.get('dossier_id')}
    leads={r['dossier_id']:r for r in document.get('investigation_frontier',{}).get('leads',[]) if r.get('dossier_id')}
    checks={}
    for c in document.get('check_ledger',{}).get('checks',[]):
        for x in c.get('contracts',[]):checks.setdefault(x.get('dossier_id'),[]).append((c,x))
    raw_hypotheses=document.get('hypotheses',document.get('hypothesis',[]))
    hypotheses=[h for h in raw_hypotheses if scoped(h) and
        (h.get('hypothesis_kind')!='dynamic' or current_scope(h,tasks,active))]
    syntheses={s['hypothesis_id']:s for s in document.get('case_synthesis',[]) if s.get('hypothesis_id') and scoped(s)}
    board=[]
    for h in hypotheses:
        if h.get('hypothesis_kind')!='dynamic':continue
        refs=h.get('observation_ids',[])
        if not set(refs).issubset(by_id):continue
        history=h.get('revision_history',[]);latest=history[-1] if history else {}
        board.append({**h,'lifecycle':h.get('lifecycle','investigating'),
            'change_reason':latest.get('change_reason') or h.get('reasoning',''),
            'change_history':history,'change_type':latest.get('change_type','first_assessment')})
    cards=[];claimed=set()

    def append_card(kind,ident,title,finding,refs,state,baseline=False,lead=None,history=None):
        f=finding or {};lead=lead or {}
        # A pending unit's old finding is not a current verdict.
        reviewed=state=='reviewed';semantic=f.get('judgment') if reviewed else None
        refs=list(dict.fromkeys(oid for oid in refs if oid in by_id))
        pending=not reviewed
        role=f.get('timeline_role') if reviewed else None
        relevance=f.get('incident_relevance') or {}
        level=relevance.get('level','undetermined') if reviewed else 'undetermined'
        qualified=set(relevance.get('observation_ids',[]))<=set(by_id)
        if level in ('direct','indirect') and not (qualified and relevance.get('observation_ids') and relevance.get('reason')):
            level='undetermined'
        # Never turn the number of tests, detections or hypotheses into case
        # relevance. Legacy core-role labels are not a relevance assessment.
        score={'direct':85,'indirect':55,'context':10}.get(level)
        if score is None and role in ('참고','반증됨','coverage-only','범위 설명'):
            score=10
        drivers=[(score,relevance.get('reason') or '사건 관련성의 별도 평가 없음')]
        priority_drivers=[]
        if pending:priority_drivers.append((35,'미검토 질문'))
        linked=[]
        for h in hypotheses:
            hf=h.get('finding') or h
            hrefs=set(hf.get('observation_ids',[])+h.get('supporting_evidence_ids',[])+h.get('refuting_evidence_ids',[]))
            if hrefs.intersection(refs):
                linked.append({'id':h.get('id'),'number':h.get('number'),
                    'title':h.get('title') or h.get('question') or h.get('text') or hf.get('title','가설'),
                    'lifecycle':h.get('lifecycle','investigating'),
                    'card_summary':h.get('card_summary',''),'revision':h.get('revision',0)})
        related=checks.get(ident,[])
        open_checks=[(c,x) for c,x in related if not c.get('job_ids') or x.get('evaluation_status')!='assessed']
        if open_checks or lead.get('next_tests'):priority_drivers.append((30,'판별 검사 진행·대기'))
        assessed_refs={oid for _,x in related for oid in (x.get('assessment') or {}).get('observation_ids',[]) if oid in by_id}
        if f.get('open_objections'):priority_drivers.append((40,'미해결 반론'))
        priority=min(100,(score or 0)+sum(p for p,_ in priority_drivers))
        must_surface=pending or bool(open_checks or lead.get('next_tests') or f.get('open_objections'))
        # A refuted/context-only lead only resurfaces when new linked work merits
        # it; confidence alone never increases incident relevance.
        time_refs=list(dict.fromkeys(refs+[oid for stage in f.get('stages',[]) for oid in stage.get('observation_ids',[]) if oid in by_id]))
        cards.append({'id':ident,'kind':kind,'title':f.get('title') or title,'label':semantic or '추정',
            'semantic_judgment':semantic,'state':state,'timeline_role':role,'baseline':baseline,
            'relevance_score':score,'relevance_level':level,'metric_kind':'incident_relevance_not_probability',
            'investigation_priority':priority,'priority_reasons':[why for _,why in priority_drivers],
            'why':[{'driver':why,'points':points} for points,why in drivers],
            'display':{'collapsed_suggestion':score is not None and score<LOW_RELEVANCE and not must_surface,'must_surface':must_surface},
            'unreviewed':pending,'newly_linked':False,'event_time':time_context.event(time_refs),
            'observation_ids':refs,'counterevidence_ids':list(dict.fromkeys(oid for oid in
                f.get('counterevidence_ids',[])+[i for o in f.get('open_objections',[]) for i in o['observation_ids']] if oid in by_id)),
            'open_objections':f.get('open_objections',[]),'publication_status':f.get('publication_status'),
            'publication_limit':f.get('publication_limit',''),
            'reason':f.get('reason') or lead.get('reason') or ('검토 대기' if pending else '판단 이유 미등록'),
            'card_summary':f.get('card_summary',''),
            'alternatives':f.get('alternatives',[]),'remaining_checks':f.get('remaining_checks',[]),
            'hypothesis_links':linked,'change_history':history or [],
            'revision':len(history or []),'change_type':(history or [{}])[-1].get('change_type'),
            'change_reason':(history or [{}])[-1].get('change_reason',''),
            'lifecycle':lifecycle(f,final=reviewed)})

    for d in document.get('dossiers',document.get('dossier',[])):
        task=tasks.get(d.get('task_id'))
        if not scoped(d) or not task or d.get('generation',0)!=task.get('retry_generation',0):continue
        history=d.get('assessment_history',[])
        f=d.get('finding') or findings.get(d['id']) or ((history[-1].get('finding') or {}) if history else {})
        refs=f.get('observation_ids') if d.get('status')=='reviewed' else d.get('observation_ids',f.get('observation_ids',[]))
        if history and d.get('status')!='reviewed':refs=list(dict.fromkeys((refs or [])+f.get('observation_ids',[])))
        if refs and not set(refs).issubset(by_id):continue
        append_card('dossier',d['id'],d.get('title','단서'),f,refs or [],d.get('status','pending'),d.get('baseline',False),leads.get(d['id']),history)
        if d.get('source_claim_id'):claimed.add(d['source_claim_id'])
    # During agent exploration the review queue may not exist yet. Surface its
    # source-backed hypotheses as provisional, never as final confirmed facts.
    for claim in document.get('claim',[]):
        if claim['id'] in claimed or not scoped(claim) or claim.get('task_id') not in tasks or not claim.get('automatic'):continue
        refs=claim.get('observation_ids',[])
        if not refs or not set(refs).issubset(by_id):continue
        f={'reason':claim.get('uncertainty','에이전트 해석 · 정식 단서 검토 전'),
            'alternatives':claim.get('alternatives',[])}
        append_card('claim',claim['id'],claim.get('text','조사 단서'),f,refs,'pending')
    existing={c['id'] for c in cards}
    for did,lead in leads.items():
        if did in existing:continue
        # Only true frontier-only records (not stale/disconnected dossiers).
        if any(d.get('id')==did for d in document.get('dossiers',document.get('dossier',[]))):continue
        append_card('lead',did,lead.get('title','후속 단서'),{},lead.get('observation_ids',[]),
                    'reviewed' if lead.get('state')=='disposed' else 'pending',lead=lead)
    # Interpretations have their own stable card. They are not additional raw
    # evidence and cannot overwrite a different claim sharing a source record.
    for h in board:
        f={'title':h['title'],'card_summary':h.get('card_summary',''),'reason':h.get('reasoning',''),
            'judgment':h.get('judgment','미확인'),'counterevidence_ids':h.get('refuting_evidence_ids',[]),
            'remaining_checks':h.get('remaining_checks',[]),'timeline_role':'반증됨' if h['lifecycle']=='refuted' else '보조'}
        append_card('hypothesis',h['hypothesis_card_id'],h['title'],f,h['observation_ids'],
            'reviewed' if h.get('status')=='reviewed' else 'pending')
        cards[-1].update(lifecycle=h['lifecycle'],revision=h.get('revision',0),change_history=h['change_history'],
            change_type=h['change_type'],change_reason=h['change_reason'],hypothesis_links=[])
        # Rejected hypotheses remain visible as resolved interpretations until
        # explicitly collapsed, rather than disappearing at the moment of refutation.
        cards[-1]['display']={'collapsed_suggestion':False,'must_surface':True}
    cards.sort(key=lambda c:({'occurred':0,'estimated':1,'file_metadata':2,'unknown':3}[c['event_time']['time_kind']],
        int(c['event_time']['epoch_nanoseconds'] or 0),c['id']))
    hidden=sum(c['display']['collapsed_suggestion'] for c in cards)
    return {'version':VERSION,'cards':cards,'hypothesis_board':board,
        'coverage_domains':{'total':sum(h.get('hypothesis_kind')!='dynamic' for h in hypotheses),
            'assessed':sum(bool(h.get('hypothesis_kind')!='dynamic' and (h.get('reasoning') or h['id'] in syntheses)) for h in hypotheses)},
        'visible_count':len(cards)-hidden,'hidden_count':hidden,
        'thresholds':{'low_relevance':LOW_RELEVANCE,'rationale':'표시만 접으며 조사·근거를 삭제하지 않습니다. 미검토·새 검사 연결은 항상 표시합니다.'},
        'scope':'행위 기록·추정·파일 참고 시각을 구별하고 각 범위 안에서 정렬. 관련성은 조사 연결도이며 침해 확률이 아닙니다.'}
