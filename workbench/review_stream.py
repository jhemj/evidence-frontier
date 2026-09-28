"""Durable source-page review, followed by source-backed synthesis.

The canonical input is a ledger snapshot, never the growing prompt. Page
assessments are working notes, not published findings or independent evidence.
Only a final synthesis may flow back into the normal dossier acceptance path.
"""
from copy import deepcopy
import hashlib
import json

from .review_context import fit, InputBudgetError, expand_metadata
from .review_paging import build_pages, ProjectionTooLarge
from .structured_context import expand


VERSION = 'review-stream-1'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def resolved(pack):
    result = deepcopy(pack)
    expand(result)
    expand_metadata(result)
    return result


def references(output):
    """Every explicit evidentiary reference, not only headline citations."""
    result = []
    for finding in output.get('findings', []):
        result.extend(finding.get('observation_ids', []))
        result.extend(finding.get('counterevidence_ids', []))
        for stage in finding.get('stages', []):
            result.extend(stage.get('observation_ids', []))
        result.extend(fact['observation_id'] for fact in finding.get('fact_assertions', []))
    for check in output.get('check_assessments', []):
        result.extend(check.get('observation_ids', []))
    return list(dict.fromkeys(result))


def start(store, cid, task, batch, canonical, maximum=36000):
    """Commit all page memberships before any page is presented to a model."""
    existing = batch.get('review_stream_id')
    if existing:
        stream = store.get(existing)
        if stream['round'] == batch['round']:
            return stream
    # Reserve room for the stable stream contract injected below, not more
    # model context. The compiler preserves source identity and ownership.
    pages = build_pages(canonical, maximum=maximum - 1200, fit_fn=fit)
    with store.tx():
        current = store.get(batch['id'])
        if current.get('review_stream_id'):
            previous = store.get(current['review_stream_id'])
            if previous['round'] == batch['round']:
                return previous
        stream = store.add('review_stream', cid, task_id=task['id'], batch_id=batch['id'],
            generation=task.get('retry_generation', 0), round=batch['round'],
            version=VERSION, canonical=deepcopy(canonical),
            canonical_sha256=fingerprint(canonical), page_count=len(pages),
            status='pages', maximum=maximum)
        frontier=[]
        for index, page in enumerate(pages):
            node=store.add('review_page', cid, task_id=task['id'], batch_id=batch['id'],
                stream_id=stream['id'], index=index, level=0, status='pending', pack=page,
                covered_source_count=len(page['observations']))
            frontier.append(node['id'])
        stream=store.update(stream['id'],frontier=frontier,level=0)
        store.update(batch['id'], review_stream_id=stream['id'])
    return stream


def _notes(page):
    output = page['output']
    # Notes are bounded by the model's existing output schema. No raw source
    # facts or check predicates are summarized here. Full output is in receipt.
    return {'page_id': page['id'], 'receipt_id': page['receipt_id'],
            'source_observation_count': len(page['included_ids']),
            'summary': output.get('summary', ''),
            'findings': deepcopy(output.get('findings', [])),
            'check_assessments': deepcopy(output.get('check_assessments', [])),
            'proposed_checks': deepcopy(output.get('next_checks', []))}


def reduction_pack(stream, pages, final=True):
    canonical = deepcopy(stream['canonical'])
    anchors = {oid for page in pages for oid in references(page['output'])}
    # A page may legitimately find no positive evidence. Do not fabricate a
    # citation in order to make the final result look supported.
    rows = [o for o in canonical['observations'] if o['id'] in anchors]
    present = {o['id'] for o in rows}
    if anchors - present:
        raise ValueError('Page assessment cites a source outside its canonical input')
    canonical['observations'] = rows
    canonical['allowed_observation_ids'] = [o['id'] for o in rows]
    canonical['allowed_observation_ids_by_dossier'] = {
        did: [oid for oid in ids if oid in present]
        for did, ids in canonical['allowed_observation_ids_by_dossier'].items()}
    canonical['shared_check_observation_ids'] = [
        oid for oid in canonical.get('shared_check_observation_ids', []) if oid in present]
    for dossier in canonical['required_dossiers']:
        original = dossier['observation_ids']
        dossier['observation_ids'] = [oid for oid in original if oid in present]
        dossier['not_represented_in_synthesis'] = len(set(original) - present)
    if not final:
        # Intermediate comparisons need only contracts actually discussed by
        # their children. The final decision retains the full contract ledger.
        relevant_checks={a['check_id'] for p in pages for a in p['output'].get('check_assessments',[])}
        canonical['executed_checks']=[c for c in canonical['executed_checks'] if c['id'] in relevant_checks]
    for check in canonical['executed_checks']:
        original = check['observation_ids']
        check['observation_ids'] = [oid for oid in original if oid in present]
        check['omitted_observations'] = check.get('omitted_observations', 0) + len(set(original) - present)
    canonical['literal_fact_candidates'] = [fact for fact in canonical.get('literal_fact_candidates', [])
        if fact.get('observation_id') in present]
    canonical['page_review_notes'] = [_notes(page) for page in pages]
    canonical['review_stream'] = {
        'id': stream['id'], 'phase': 'synthesis' if final else 'comparison_page',
        'canonical_sha256': stream['canonical_sha256'],
        'pages_reviewed': stream['page_count'], 'pages_total': stream['page_count'],
        'comparison_notes':len(pages),
        'source_observations_presented': sum(p.get('covered_source_count',len(p['included_ids'])) for p in pages),
        'synthesis_source_count': len(rows),
        'sources_not_repeated_in_synthesis': len(stream['canonical']['observations']) - len(rows),
        'notes_are_evidence': False,
        'instruction': (('All source pages have working assessments, NOT accepted findings. ' if final else
            'This is an intermediate comparison of ONLY these child notes, not a dossier-wide decision. ')+
            'Reconcile competing explanations and proposed checks; do not vote or count notes as independent evidence. '
            'Cite only the original observations actually included in this synthesis. '
            'A page summary is not proof of absence, whole-source coverage, or a passed check. '
            'Keep unassessed checks and unresolved contradictions explicit. Unselected source records and '
            'full page receipts remain in the canonical ledger.')}
    canonical['selection_is_partial'] = True
    canonical['finalization_allowed'] = final
    fit(canonical, stream['maximum'])
    return canonical


def _schedule_comparisons(store,cid,stream,pages):
    """Reduce a growing notebook in bounded groups, not a new giant prompt.

    Every parent records every child. Require strict frontier shrinkage, so a
    pathological irreducible source/contract floor cannot create an endless
    summarization loop. It remains an explicit input gap instead.
    """
    groups=[];index=0
    while index<len(pages):
        if index==len(pages)-1:
            groups.append(([pages[index]],None));break
        best=None
        for end in range(index+2,len(pages)+1):
            try:pack=reduction_pack(stream,pages[index:end],final=False)
            except InputBudgetError:break
            best=(end,pack)
        if best is None:
            # Carry one irreducible child unchanged; another pair may still
            # reduce this frontier and make room for the final comparison.
            groups.append(([pages[index]],None));index+=1
        else:
            end,pack=best;groups.append((pages[index:end],pack));index=end
    if len(groups)>=len(pages):
        raise InputBudgetError('No bounded comparison can reduce this source/contract frontier; original pages remain unadopted')
    frontier=[]
    with store.tx():
        for children,pack in groups:
            if pack is None:
                frontier.append(children[0]['id']);continue
            node=store.add('review_page',cid,task_id=stream['task_id'],batch_id=stream['batch_id'],
                stream_id=stream['id'],index=len(frontier),level=stream.get('level',0)+1,
                status='pending',pack=pack,child_ids=[c['id'] for c in children],
                covered_source_count=sum(c.get('covered_source_count',len(c['included_ids'])) for c in children))
            frontier.append(node['id'])
        store.update(stream['id'],frontier=frontier,level=stream.get('level',0)+1)


def prepare(store, cid, stream_id):
    stream = store.get(stream_id)
    pages = [store.get(pid) for pid in stream['frontier']]
    if not pages or any(p['stream_id']!=stream_id for p in pages):
        raise ValueError('Incomplete durable review page manifest')
    pending = next((p for p in pages if p['status'] != 'reviewed'), None)
    if pending:
        pack = deepcopy(pending['pack'])
        if pending.get('level',0)>0:
            return pack,{'stream_id':stream_id,'page_id':pending['id'],'phase':'comparison_page'}
        pack['review_stream'] = {
            'id': stream_id, 'page_id': pending['id'], 'phase': 'source_page',
            'pages_reviewed': sum(p['status'] == 'reviewed' for p in pages),
            'pages_total': stream['page_count'], 'finalization_allowed': False,
            'instruction': ('Assess this source page only. Your output is an unpublished working note. '
                'Do not infer whole-dossier absence/completeness. Express competing explanations '
                'and discriminating next checks. Check outcomes apply only to sources visible on this page. '
                'Final synthesis happens only after every page has a validated assessment.')}
        pack['finalization_allowed'] = False
        fit(pack, stream['maximum'])
        return pack, {'stream_id': stream_id, 'page_id': pending['id'], 'phase': 'source_page'}
    try:pack = reduction_pack(stream, pages)
    except InputBudgetError:
        _schedule_comparisons(store,cid,stream,pages)
        return prepare(store,cid,stream_id)
    return pack, {'stream_id': stream_id, 'phase': 'synthesis'}


def accept_page(store, batch, meta, output, receipt_id, included_ids):
    """Caller holds the validated-attempt transaction and stale-scope guard."""
    page = store.get(meta['page_id'])
    if page['status'] != 'pending':
        raise ValueError('Review page already accepted')
    store.update(page['id'], status='reviewed', output=deepcopy(output),
                 receipt_id=receipt_id, included_ids=list(included_ids))
    store.update(batch['id'], attempts=0, validation_feedback=None, active_attempt_id=None)


def complete(store, meta, receipt_id):
    store.update(meta['stream_id'], status='synthesized', synthesis_receipt_id=receipt_id)
