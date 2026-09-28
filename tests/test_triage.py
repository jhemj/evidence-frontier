from workbench.triage import project


def base(status="running"):
    return {"case": {"status": status}, "task": [], "dossier": [], "dossier_batch": [], "investigation_frontier": {"leads": []}}


def test_unreviewed_and_new_leads_are_never_collapsed():
    data = base()
    data["task"] = [{"id": "t", "action": "ai_judgment", "retry_generation": 0, "status": "running"}]
    data["dossier"] = [{"id": "d", "task_id": "t", "generation": 0, "status": "pending", "title": "검토 단서"}]
    data["investigation_frontier"]["leads"] = [{"dossier_id": "new", "title": "새 연결 단서", "state": "open"}]
    result = project(data)
    assert all(c["display"]["must_surface"] and not c["display"]["collapsed_suggestion"] for c in result["cards"])
    assert all(c["metric_kind"] == "investigation_priority_not_intrusion_probability" for c in result["cards"])


def test_stale_generation_is_not_presented_as_current_judgment():
    data = base()
    data["task"] = [{"id": "t", "action": "ai_judgment", "retry_generation": 2, "status": "done"}]
    data["dossier"] = [{"id": "old", "task_id": "t", "generation": 1, "status": "reviewed", "finding": {"judgment": "확인"}}]
    assert project(data)["cards"] == []


def test_cards_are_chronological_and_file_metadata_is_unknown():
    data = base()
    data["task"] = [{"id": "t", "action": "ai_judgment", "retry_generation": 0, "status": "done"}]
    data["observation"] = [
        {"id": "o2", "type": "linux_command", "timestamp": "2026-01-02T00:00:00+00:00", "fields": {}},
        {"id": "o1", "type": "file_metadata", "timestamp": "2025-01-01T00:00:00+00:00", "fields": {}},
    ]
    data["dossier"] = [
        {"id": "d2", "task_id": "t", "generation": 0, "status": "reviewed", "finding": {"observation_ids": ["o2"], "judgment": "유력"}},
        {"id": "d1", "task_id": "t", "generation": 0, "status": "reviewed", "finding": {"observation_ids": ["o1"], "judgment": "확인"}},
    ]
    cards = project(data)["cards"]
    assert [c["id"] for c in cards] == ["d2", "d1"]
    assert cards[0]["event_time"]["time_kind"] == "occurred"
    assert cards[1]["event_time"]["time_kind"] == "unknown"
    assert "observation_ids" in cards[0] and "remaining_checks" in cards[0]


def test_refuted_and_coverage_only_findings_are_not_primary_cards():
    data = base()
    data['task'] = [{'id': 't', 'retry_generation': 0, 'status': 'done'}]
    data['dossier'] = [
        {'id': 'refuted', 'task_id': 't', 'generation': 0, 'status': 'reviewed', 'finding': {'judgment': '확인', 'timeline_role': '반증됨'}},
        {'id': 'coverage', 'task_id': 't', 'generation': 0, 'status': 'reviewed', 'finding': {'judgment': '미확인', 'timeline_role': 'coverage-only'}},
    ]
    cards=project(data)['cards']
    assert len(cards)==2  # retained, not silently discarded
    assert all(c['display']['collapsed_suggestion'] for c in cards)


def test_disconnected_evidence_is_excluded_but_failed_review_is_surfaceable():
    data = base()
    data['task'] = [{'id': 't', 'retry_generation': 0, 'status': 'done'}]
    data['evidence'] = [{'id': 'gone', 'connected': False}, {'id': 'live', 'connected': True}]
    data['observation'] = [{'id': 'og', 'evidence_id': 'gone', 'type': 'linux_command', 'timestamp': '2026-01-01T00:00:00+00:00', 'fields': {}},
                           {'id': 'ol', 'evidence_id': 'live', 'type': 'linux_command', 'timestamp': '2026-01-01T00:00:00+00:00', 'fields': {}}]
    data['dossier'] = [
        {'id': 'gone-d', 'task_id': 't', 'generation': 0, 'status': 'failed', 'finding': {'observation_ids': ['og']}},
        {'id': 'live-d', 'task_id': 't', 'generation': 0, 'status': 'failed', 'finding': {'observation_ids': ['ol']}},
    ]
    cards = project(data)['cards']
    assert [c['id'] for c in cards] == ['live-d']
    assert cards[0]['display']['must_surface']


def test_nanosecond_order_is_preserved_for_same_normalized_microsecond():
    data = base()
    data['task'] = [{'id': 't', 'retry_generation': 0, 'status': 'done'}]
    data['observation'] = [
        {'id': 'a-late', 'type': 'linux_command', 'timestamp': '2026-01-01T00:00:00.000000009+00:00', 'fields': {}},
        {'id': 'z-early', 'type': 'linux_command', 'timestamp': '2026-01-01T00:00:00.000000001+00:00', 'fields': {}},
    ]
    data['dossier'] = [
        {'id': 'a-late-d', 'task_id': 't', 'generation': 0, 'status': 'reviewed', 'finding': {'observation_ids': ['a-late']}},
        {'id': 'z-early-d', 'task_id': 't', 'generation': 0, 'status': 'reviewed', 'finding': {'observation_ids': ['z-early']}},
    ]
    assert [c['id'] for c in project(data)['cards']] == ['z-early-d', 'a-late-d']


def test_low_relevance_resurfaces_when_new_check_links_without_mutating_ledger():
    from copy import deepcopy
    data=base();data['task']=[{'id':'t','retry_generation':0}]
    data['observation']=[{'id':'o','type':'linux_command','timestamp':None,'fields':{}}]
    data['dossier']=[{'id':'d','task_id':'t','status':'reviewed','finding':{
        'title':'문서 속 문자열','judgment':'확인','timeline_role':'참고',
        'card_summary':'문서에 명령 예시가 있습니다. 호스트에서 실행했다는 근거가 아닙니다.',
        'reason':'원문 문맥 대조','observation_ids':['o']}}]
    before=deepcopy(data)
    old=project(data)['cards'][0]
    assert old['display']['collapsed_suggestion'] and data==before
    assert old['card_summary']==data['dossier'][0]['finding']['card_summary']
    data['check_ledger']={'checks':[{'job_ids':['j'],'contracts':[{'dossier_id':'d','evaluation_status':'unassessed'}]}]}
    new=project(data)['cards'][0]
    assert new['display']['must_surface'] and not new['display']['collapsed_suggestion']
    assert new['relevance_score']>old['relevance_score']
    assert new['id']==old['id'] and new['semantic_judgment']=='확인'


def test_uncertain_clock_cannot_enter_event_axis_and_pending_keeps_sources():
    data=base();data['task']=[{'id':'t'}]
    data['observation']=[{'id':'o','type':'linux_command','timestamp':'2026-01-01T10:00:00+09:00','fields':{'time_basis':'inferred year and timezone'}}]
    data['dossier']=[{'id':'d','task_id':'t','status':'pending','observation_ids':['o'],
        'finding':{'judgment':'확인','timeline_role':'핵심','reason':'old assessment'}}]
    card=project(data)['cards'][0]
    assert card['event_time']['time_kind']=='estimated'
    assert card['event_time']['needs_time_followup']
    assert card['observation_ids']==['o'] and card['semantic_judgment'] is None
