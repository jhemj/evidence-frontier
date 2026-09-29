"""Source-bound scenario comparison, separate from investigation scheduling.

Only an accepted model assessment supplies ordinal evidence fit. Source counts,
severity, UI limits and detector names never determine ranks. Equal fit ties;
different questions are not competing scenarios. No case-specific rules.
"""
from .models import ScenarioAssessment
from .case_memory import _digest, _object
from .hypothesis_ledger import current_scope

FIT = {'strong': 3, 'moderate': 2, 'limited': 1}


def dependency(observations, refs):
    by_id = {o['id']: o for o in observations}
    origins = {_object(by_id[i]) for i in refs if i in by_id} - {None}
    return _digest(sorted((o['id'], o) for o in observations if o['id'] in refs or _object(o) in origins))


def accepted(value, support, refute, basis='positive_evidence'):
    if value is None:
        return None
    a = ScenarioAssessment.model_validate(value).model_dump()
    if not (support or refute) or set(support) & set(refute):
        raise ValueError('scenario requires source-bound, nonconflicting evidence roles')
    if a['evidence_fit'] != 'limited' and (not support or basis == 'absence'):
        raise ValueError('strong scenario fit requires positive supporting sources')
    a['comparison_question'] = ' '.join(a['comparison_question'].split())
    return a


def project(document):
    active = {e['id'] for e in document.get('evidence', []) if e.get('connected', True)}
    tasks = {t['id']: t for t in document.get('task', [])}
    observations = [o for o in document.get('observation', document.get('observations', [])) if o.get('evidence_id') in active]
    by_id = {o['id']: o for o in observations}
    hypotheses = document.get('hypothesis', document.get('hypotheses', []))
    cards = []
    for h in hypotheses:
        if h.get('hypothesis_kind') != 'dynamic' or h.get('superseded') or not current_scope(h, tasks, active):
            continue
        support, refute = h.get('supporting_evidence_ids', []), h.get('refuting_evidence_ids', [])
        refs = set(support + refute)
        if not refs.issubset(by_id):
            continue
        try:
            a = accepted(h.get('scenario_assessment'), support, refute)
        except ValueError:
            a = None
        fresh = bool(a and h.get('scenario_source_revision') == dependency(observations, refs))
        if fresh and h.get('scenario_review_revision'):
            from .case_synthesis import review_revision
            dossiers=[d for d in document.get('dossier',document.get('dossiers',[])) if d.get('task_id')==h['task_id']
                and d.get('generation',0)==h.get('generation',0)]
            fresh=review_revision(dossiers,h.get('scenario_review_source_ids',[]))==h['scenario_review_revision']
        state = h.get('lifecycle', 'investigating')
        ranked = fresh and state not in ('inconclusive', 'refuted')
        history = h.get('scenario_rank_history', [])
        cards.append({'id': h['id'], 'hypothesis_card_id': h.get('hypothesis_card_id'),
            'title': h.get('title') or h.get('text'), 'summary': h.get('card_summary') or h.get('reasoning', ''),
            'lifecycle': state if fresh or not a else 'inconclusive', 'assessment_current': fresh,
            'comparison_question': a['comparison_question'] if a else '',
            'evidence_fit': a['evidence_fit'] if ranked else None, 'rank': None,
            'ranking_reason': a['ranking_reason'] if fresh else '현재 근거 범위의 비교 평가를 기다립니다.',
            'alternative_explanation': a['alternative_explanation'] if fresh else '',
            'next_check': a['next_check'] if fresh else ' · '.join(h.get('remaining_checks', [])),
            'investigation_priority': a['investigation_priority'] if fresh else 'normal',
            'priority_reason': a['priority_reason'] if fresh else '비교 평가 대기 · 조사 원장은 유지됩니다.',
            'supporting_evidence_ids': list(dict.fromkeys(support)), 'refuting_evidence_ids': list(dict.fromkeys(refute)),
            'rank_history': history, 'change_reason': (h.get('revision_history') or [{}])[-1].get('change_reason', ''),
            'revision': h.get('revision', 0)})
    groups = {}
    for c in cards:
        if c['evidence_fit']:
            groups.setdefault(c['comparison_question'], []).append(c)
    for members in groups.values():
        for c in members:
            strength = FIT[c['evidence_fit']]
            # Competition rank; do not manufacture a tiebreak using counts.
            c['rank'] = 1 + sum(FIT[x['evidence_fit']] > strength for x in members)
            c['tied'] = sum(x['evidence_fit'] == c['evidence_fit'] for x in members) > 1
    # Main view samples leading alternatives across questions before runners-up.
    cards.sort(key=lambda c: (c['rank'] is None, c['rank'] or 0,
        c['comparison_question'], c['id']))
    return {'version': 'scenario-comparison-1', 'cards': cards, 'total': len(cards),
            'default_visible': 3, 'expanded_visible': 5,
            'ranking_basis': '질문별 증거 설명력 · 공동 순위 허용 · 침해 확률 아님',
            'scope': '표시는 조사 범위를 제한하지 않습니다. 다른 질문의 가설은 함께 성립할 수 있습니다.'}


def record_rank_changes(store, cid, before, reason):
    """Persist comparison movement, including a peer's changed rank, on adoption."""
    after = project({kind: store.list(kind, cid) for kind in ('evidence', 'task', 'observation', 'hypothesis','dossier')})
    prior = {c['id']: c for c in before['cards']}
    from .store import now
    for card in after['cards']:
        old = prior.get(card['id'], {})
        if (card['rank'], card['comparison_question']) == (old.get('rank'), old.get('comparison_question', '')):
            continue
        row = store.get(card['id'])
        history = row.get('scenario_rank_history', [])
        store.update(card['id'], scenario_rank_history=history + [{
            'at': now(), 'previous_rank': old.get('rank'), 'rank': card['rank'],
            'comparison_question': card['comparison_question'],
            'previous_question': old.get('comparison_question', ''), 'reason': reason}])
