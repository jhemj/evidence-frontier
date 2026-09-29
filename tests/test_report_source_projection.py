from copy import deepcopy
from test_dual_reports import fixture
from workbench.reporting import report_document
from workbench.report_views import project


def test_projection_anchors_only_cited_sources_not_every_observation(tmp_path):
    c, cid, *_ = fixture(tmp_path)
    doc = report_document(c, cid)
    for i in range(500):
        doc['observations'].append({'id': f'noise-{i}', 'evidence_id': doc['evidence'][0]['id'],
            'type': 'linux_system_event', 'timestamp': None, 'source_location': f'noise:{i}', 'fields': {}})

    view = project(doc)['analyst']
    anchors = [b for b in view['blocks'] if b.get('kind') == 'anchor']
    assert len(anchors) == 1
    assert 'noise-0' not in '\n'.join(b.get('text', '') for b in view['blocks'] if b.get('kind') in ('paragraph', 'heading'))


def test_session_link_projection_reports_group_vs_cited_counts(tmp_path):
    c, cid, *_ = fixture(tmp_path)
    doc = report_document(c, cid)
    cited = doc['judgments'][0]['findings'][0]['observation_ids'][0]
    doc['session_links'] = [{'key': 'same-account', 'observation_ids': [cited, 'not-cited-1', 'not-cited-2'],
        'limitation': '근접성은 동일 세션·인과를 확정하지 않음', 'alternative': '정상 운영'}]

    view = project(doc)['analyst']
    text = '\n'.join(b.get('text', '') for b in view['blocks'] if b.get('kind') in ('paragraph', 'heading'))
    assert '그룹 원장 관측 3개 · 현재 판단 근거와 연결된 관측 1개' in text
    assert 'not-cited-1' not in text


def test_every_rendered_citation_has_anchor_and_raw_ledger_unchanged(tmp_path):
    c, cid, *_ = fixture(tmp_path)
    doc = report_document(c, cid)
    prototype = doc['observations'][0]
    for oid in ('automatic', 'history', 'supporting', 'refuting', 'counter'):
        doc['observations'].append({**deepcopy(prototype), 'id': oid})
    doc['automatic_findings'] = [{'text': '중간 해석', 'observation_ids': ['automatic']}]
    doc['dossier_history'] = [{'id': 'previous', 'status': 'reviewed', 'finding': {
        'title': '과거 판단', 'reason': '원장 보존', 'observation_ids': ['history']}}]
    doc['case_synthesis'] = [{'number': 3, 'question': '실행?', 'status': 'reviewed',
        'scope': {'presented_observations': 3, 'relevant_observations': 3},
        'supporting_evidence_ids': ['supporting'], 'refuting_evidence_ids': ['refuting'],
        'finding': {'title': '검토 중', 'judgment': '미확인', 'reason': '추가 확인',
            'observation_ids': [], 'counterevidence_ids': ['counter'], 'stages': []}}]
    before = deepcopy(doc)
    view = project(doc)['analyst']
    used = {oid for b in view['blocks'] if b['kind'] == 'citations' for oid in b['ids']}
    anchors = {b['id'] for b in view['blocks'] if b['kind'] == 'anchor'}
    assert used == anchors
    assert {'automatic', 'history', 'supporting', 'refuting', 'counter'} <= anchors
    assert view['source_projection']['reader_sources'] == len(anchors)
    assert doc == before


def test_executive_timeline_is_layout_bounded_but_analyst_retains_all(tmp_path):
    c, cid, *_ = fixture(tmp_path)
    doc = report_document(c, cid)
    prototype = doc['observations'][0]
    ids = []
    for i in range(20):
        oid = f'event-{i}'
        ids.append(oid)
        doc['observations'].append({**deepcopy(prototype), 'id': oid, 'type': 'linux_command',
            'timestamp': f'2026-09-01T00:00:{i:02d}+09:00'})
    doc['judgments'][0]['findings'][0]['observation_ids'] = ids
    views = project(doc)
    executive = next(b for b in views['executive']['blocks'] if b.get('headers') == ['원문 시각 / 근거', '대상', '기록된 사실'])
    analyst = next(b for b in views['analyst']['blocks'] if b.get('headers') == ['시각 / 시간 근거', '관측 ID', '기록된 사실'])
    assert len(executive['rows']) == 4
    assert len(analyst['rows']) == 20


def test_file_times_and_assumed_year_cannot_become_executive_first_event(tmp_path):
    c,cid,*_=fixture(tmp_path)
    doc=report_document(c,cid);base=doc['observations'][0]
    events=[]
    for oid,typ,stamp,fields in (
        ('meta','filesystem_time','2020-01-01T00:00:00Z',{'time_type':'ctime','path':'/example','partition_offset':0}),
        ('estimate','linux_command','2022-01-01T00:00:00Z',{'time_basis':'year inferred from rotation filename'}),
        ('exact','linux_command','2026-01-01T00:00:00Z',{'time_basis':'explicit source clock'}),
        ('naive','linux_command','2021-01-01T00:00:00',{}),
    ):
        events.append({**deepcopy(base),'id':oid,'type':typ,'timestamp':stamp,'fields':fields})
    doc['observations'].extend(events)
    doc['judgments'][0]['findings'][0]['observation_ids']=[o['id'] for o in events]
    before=deepcopy(doc);views=project(doc)
    main=next(b for b in views['executive']['blocks'] if b.get('headers')==['원문 시각 / 근거','대상','기록된 사실'])
    assert len(main['rows'])==1 and main['rows'][0][0].startswith('2026-01-01')
    analyst=views['analyst']['blocks']
    inferred=next(b for b in analyst if b.get('headers')==['추정 시각 / 가정','관측 ID','기록된 사실'])
    metadata=next(b for b in analyst if b.get('headers')==['파일 시각 / 의미','관측 ID / 필드','대상 / 근거'])
    assert inferred['rows'][0][1]=='estimate' and '추정' in inferred['rows'][0][0]
    assert metadata['rows'][0][1]=='meta\n/timestamp'
    assert {b['id'] for b in analyst if b['kind']=='anchor'} >= {'meta','estimate','exact','naive'}
    assert doc==before
