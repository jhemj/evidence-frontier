"""Read-only technical audit for triage cards in a snapshot or Store DB."""
import argparse
import json
import sqlite3
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.triage import project as triage_project
from workbench.evidence_semantics import exact_utc_ns


def audit_snapshot(snapshot):
    observations = snapshot.get('observations', snapshot.get('observation', []))
    by_id = {o.get('id'): o for o in observations if o.get('id')}
    evidence = {e.get('id'): e for e in snapshot.get('evidence', [])}
    tasks = {t.get('id'): t for t in snapshot.get('task', []) if not t.get('superseded')}
    cards = (snapshot.get('triage') or {}).get('cards', [])
    rendered_html = snapshot.get('rendered_html')
    diagnostics = []
    examples = {}
    def add(code, ids=()):
        diagnostics.append(code)
        if ids and code not in examples: examples[code] = list(ids)[:3]
    seen_titles = {}
    previous_time = None
    for card in cards:
        refs = card.get('observation_ids', [])
        missing = [i for i in refs if i not in by_id]
        if missing: add('missing_observation_refs', missing)
        stale = card.get('state') == 'stale' or card.get('generation_stale')
        did = card.get('id')
        dossier = next((d for d in snapshot.get('dossiers', snapshot.get('dossier', [])) if d.get('id') == did), None)
        if dossier:
            task = tasks.get(dossier.get('task_id'))
            if task and dossier.get('generation', 0) != task.get('retry_generation', 0): stale = True
        if card.get('task_id') and card['task_id'] not in tasks: add('missing_task_ref', [card['id']])
        if stale: add('stale_generation_card', [did])
        disconnected = [i for i in refs if by_id.get(i, {}).get('evidence_id') in evidence and evidence[by_id[i]['evidence_id']].get('connected') is False]
        if disconnected: add('disconnected_observation_refs', disconnected)
        summary = card.get('card_summary', '')
        title = str(card.get('title', ''))
        if not summary:
            add('missing_card_summary', [did])
            if card.get('reason'): add('card_summary_fallback', [did])
            summary = card.get('reason', '')
        if title and title.strip() == str(summary).strip(): add('title_summary_repetition', [did])
        if len(title) > 180 or len(str(summary)) > 800: add('card_text_too_long', [did])
        seen_titles.setdefault(title, []).append(did)
        display = card.get('display', {})
        if display.get('collapsed_suggestion') and display.get('must_surface'): add('collapsed_must_surface_contradiction', [did])
        if rendered_html is not None:
            missing_anchors = [i for i in refs if f'id="{i}"' not in rendered_html and f"id='{i}'" not in rendered_html]
            if missing_anchors: add('missing_render_citation_anchor', missing_anchors)
        event = card.get('event_time') or {}
        raw = event.get('raw')
        ns = exact_utc_ns(raw) if raw else None
        if ns is not None and event.get('time_kind') == 'occurred':
            if previous_time is not None and ns < previous_time: add('non_chronological_nanosecond_order', [did])
            previous_time = ns
    for title, ids in seen_titles.items():
        if title and len(ids) > 1: add('repeated_card_title', ids)
    return {'case_id': (snapshot.get('case') or {}).get('id'), 'source_version': (snapshot.get('triage') or {}).get('version'),
            'card_count': len(cards), 'observation_count': len(observations),
            'diagnostic_counts': {code: diagnostics.count(code) for code in sorted(set(diagnostics))},
            'diagnostics': diagnostics, 'examples': examples, 'anchors_verified': rendered_html is not None,
            'note': '기술적 연결·표시 검사이며 의미적 충분성이나 악성·정상 판단이 아님.'}


def load_db(path, case_id):
    db = sqlite3.connect(f'file:{Path(path)}?mode=ro', uri=True)
    try:
        rows = db.execute('SELECT body FROM records WHERE case_id=?', (case_id,)).fetchall()
    finally: db.close()
    result = {'case': None}
    for raw, in rows:
        item = json.loads(raw)
        if item['kind'] == 'case': result['case'] = item
        else: result.setdefault(item['kind'], []).append(item)
    result['case'] = result['case'] or {'id': case_id}
    result['triage'] = triage_project(result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--db', type=Path)
    parser.add_argument('--case-id')
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--rendered-html', type=Path)
    args = parser.parse_args(argv)
    if bool(args.snapshot) == bool(args.db): parser.error('exactly one of --snapshot/--db is required')
    snapshot = json.loads(args.snapshot.read_text(encoding='utf-8')) if args.snapshot else load_db(args.db, args.case_id)
    if args.rendered_html: snapshot['rendered_html'] = args.rendered_html.read_text(encoding='utf-8')
    args.out.write_text(json.dumps(audit_snapshot(snapshot), ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__': main()
