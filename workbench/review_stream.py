"""Durable source-page review, followed by source-backed synthesis.

The canonical input is a ledger snapshot, never the growing prompt. Page
assessments are working notes, not published findings or independent evidence.
Only a final synthesis may flow back into the normal dossier acceptance path.
"""
from copy import deepcopy
import hashlib
import json

from .review_context import fit_metadata_only as fit, InputBudgetError, expand_metadata
from .review_paging import build_span_pages, ProjectionTooLarge
from .structured_context import expand
from .review_diagnostics import FEEDBACK_LIMIT


VERSION = 'review-stream-4'
FOCUS_VERSION = 'source-focus-4'


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')).encode()).hexdigest()


def scope_fingerprint(canonical):
    # Newly discovered objections and correction feedback are live obligations,
    # not a changed source corpus. All sources/contracts/owners remain bound.
    return fingerprint({k:v for k,v in canonical.items()
                        if k not in ('open_objections','validation_feedback','question_context')})


def resolved(pack):
    result = deepcopy(pack)
    expand(result)
    expand_metadata(result)
    return result


def references(output):
    """Every explicit evidentiary reference, not only headline citations."""
    result = []
    result.extend(output.get('supporting_evidence_ids', []))
    result.extend(output.get('refuting_evidence_ids', []))
    for finding in output.get('findings', []):
        result.extend(finding.get('observation_ids', []))
        result.extend(finding.get('counterevidence_ids', []))
        result.extend(finding.get('incident_relevance', {}).get('observation_ids', []))
        for stage in finding.get('stages', []):
            result.extend(stage.get('observation_ids', []))
        result.extend(fact['observation_id'] for fact in finding.get('fact_assertions', []))
    for check in output.get('check_assessments', []):
        result.extend(check.get('observation_ids', []))
    for objection in output.get('objection_assessments', []):
        result.extend(objection.get('observation_ids', []))
    return list(dict.fromkeys(result))


def span_references(output):
    result=[]
    for finding in output.get('findings',[]):
        result.extend(finding.get('evidence_span_ids',[]))
    for check in output.get('check_assessments',[]):
        result.extend(check.get('evidence_span_ids',[]))
    return list(dict.fromkeys(result))


def span_rows(row):
    """One exact source-field range per entry, including comparison views."""
    if row.get('source_span'):
        yield row['source_span']['span_id'],deepcopy(row)
    for span in row.get('fields',{}).get('excerpt_spans',[]):
        item=deepcopy(row)
        item['fields'].pop('excerpt_spans',None)
        item['fields']['excerpt']=span['text']
        item.pop('source_spans',None)
        item['source_span']={k:v for k,v in span.items() if k!='text'}
        item['source_span'].update(observation_id=row['id'],field_pointer='/fields/excerpt',
            coordinate_basis='canonical_field_utf8',extent='partial_field',truncated=True)
        yield span['span_id'],item


def start(store, cid, task, batch, canonical, maximum=36000):
    """Commit all page memberships before any page is presented to a model."""
    existing = batch.get('review_stream_id')
    if existing:
        stream = store.get(existing)
        if stream['round'] == batch['round']:
            return stream
    # Reserve room for the stable stream contract injected below, not more
    # model context. The compiler preserves source identity and ownership.
    if maximum<=1200+FEEDBACK_LIMIT:
        raise ProjectionTooLarge('Measured model context leaves no source envelope after the stream and repair contracts')
    pages = build_span_pages(canonical, maximum=maximum - 1200 - FEEDBACK_LIMIT, fit_fn=fit)
    span_catalog={sid:row for page in pages for observation in resolved(page)['observations']
                  for sid,row in span_rows(observation)}
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
            status='pages', maximum=maximum,feedback_reserve=FEEDBACK_LIMIT,source_span_catalog=span_catalog)
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
    previous=canonical.pop('previous_assessment',None)
    if previous:
        # Previous model interpretations are not raw evidence. Current child
        # assessments replace that historical narrative; retain its unresolved
        # alternatives/questions and an explicit pointer to the immutable copy.
        # Source fields, test predicates and live counterevidence stay intact.
        canonical['prior_assessment_context']={
            'canonical_input_sha256':stream['canonical_sha256'],
            'retrieval':'canonical_pack.previous_assessment',
            'narrative_not_repeated':True,
            'reason':'New source-page assessments supersede prior narrative for this comparison, not its source ledger.',
            'unresolved_context':[{
                'dossier_id':f.get('dossier_id'),
                'alternatives':deepcopy(f.get('alternatives',[])),
                'remaining_checks':deepcopy(f.get('remaining_checks',[])),
                'counterevidence_ids':deepcopy(f.get('counterevidence_ids',[]))}
                for f in previous.get('findings',[])]}
    prior_findings=canonical.pop('prior_findings_untrusted',None)
    if prior_findings:
        # Case synthesis has the same history boundary as dossier review.
        # Reintroducing full, derived temporal anchors from old findings here
        # can exceed the envelope even after every raw source has been paged.
        # Child assessments replace interpretations, NOT unresolved obligations
        # or original sources; the immutable canonical ledger keeps both.
        canonical['prior_findings_context']={
            'canonical_input_sha256':stream['canonical_sha256'],
            'retrieval':'canonical_pack.prior_findings_untrusted',
            'narrative_not_repeated':True,
            'findings_retained':len(prior_findings),
            'reason':'New source-page assessments supersede prior narrative for this comparison, not its source ledger.',
            'unresolved_context':[{
                'dossier_id':f.get('dossier_id'),
                'alternatives':deepcopy(f.get('alternatives',[])),
                'remaining_checks':deepcopy(f.get('remaining_checks',[])),
                'counterevidence_ids':deepcopy(f.get('counterevidence_ids',[]))}
                for f in prior_findings]}
    anchors = {oid for page in pages for oid in references(page['output'])}
    inherited = {oid for page in pages for oid in page.get('objection_ids',[])}
    objections = [o for o in stream.get('open_objections',[]) if final or o['id'] in inherited]
    canonical['open_objections'] = deepcopy(objections)
    anchors.update(oid for objection in objections for oid in objection['observation_ids'])
    # A page may legitimately find no positive evidence. Do not fabricate a
    # citation in order to make the final result look supported.
    # Synthesis must consume the bounded source view actually presented. Do
    # not pull an oversized canonical observation back into the final prompt.
    presented_by_id={}
    available_spans={}
    selected_spans=set()
    for page in pages:
        source_view=resolved(page.get('selected_pack') or (page if page.get('observations') is not None else page.get('pack',{})))
        page_explicit=set(span_references(page.get('output',{})))
        page_refs=set(references(page['output']))
        page_spans={sid:row for observation in source_view.get('observations',[]) for sid,row in span_rows(observation)}
        original_spans={sid for observation in resolved(page.get('pack',{})).get('observations',[]) for sid,_ in span_rows(observation)}
        if page_explicit-set(page_spans)-original_spans:raise ValueError('Synthesis cites an unpresented evidence span')
        available_spans.update(page_spans)
        for row in source_view.get('observations',[]):
            if row.get('id') not in page_refs:continue
            owned={sid for sid,item in page_spans.items() if item['id']==row['id']}
            if owned:
                selected_spans.update((page_explicit&owned) or owned)
            else:
                presented_by_id[row['id']]=deepcopy(row)
    catalog=stream.get('source_span_catalog',{})
    pinned={sid for objection in objections for sid in objection.get('span_ids',[])}
    for sid in pinned:
        if sid in available_spans or sid in catalog:
            available_spans[sid]=available_spans.get(sid) or deepcopy(catalog[sid])
            selected_spans.add(sid)
    # Unfragmented source manifests use field hashes rather than span catalog
    # entries. Their exact originals are re-presented independently of notes.
    counter_ids={oid for objection in objections for oid in objection['observation_ids']}
    # Older/full-observation objections cannot be silently narrowed to a later
    # selected fragment. Re-present their entire retained field, or fail the
    # comparison explicitly when that mandatory floor cannot fit.
    from .evidence_spans import manifest
    whole_counter_ids=set()
    for original in canonical['observations']:
        owned=[o for o in objections if original['id'] in o['observation_ids']]
        if not owned:continue
        full_ids={s['span_id'] for s in manifest({'observations':[original]})['spans']}
        if any(not o.get('span_ids') or full_ids.intersection(o['span_ids']) for o in owned):
            whole_counter_ids.add(original['id'])
    rows=[]
    for original in canonical['observations']:
        if original['id'] not in anchors:continue
        chosen=[(available_spans[sid]['source_span'],available_spans[sid]) for sid in selected_spans
                if available_spans[sid]['id']==original['id']]
        chosen.sort(key=lambda pair:(pair[0]['byte_start'],pair[0]['byte_end'],pair[0]['span_id']))
        if original['id'] in whole_counter_ids:
            row=deepcopy(original)
        elif chosen:
            if len(chosen)==1:
                row=deepcopy(chosen[0][1])
            else:
                row=deepcopy(chosen[0][1]); fields=row.setdefault('fields',{})
                fields.pop('excerpt',None)
                fields['excerpt_spans']=[{'span_id':span.get('span_id'),
                    'byte_start':span.get('byte_start'),'byte_end':span.get('byte_end'),
                    'full_sha256':span.get('full_sha256'),'sha256':span.get('sha256'),
                    'text':item.get('fields',{}).get('excerpt','')}
                    for span,item in chosen]
                row.pop('source_span',None);row['source_spans']=[span for span,_ in chosen]
                row['projection_omissions']={'canonical_field':'/fields/excerpt',
                    'replacement':'exact_presented_excerpt_spans',
                    'span_ids':[span.get('span_id') for span,_ in chosen]}
        else:
            row=deepcopy(original) if original['id'] in counter_ids else presented_by_id.get(original['id'])
        if row is None:
            raise InputBudgetError('Synthesis citation has no presented source span; retain or page the source before synthesis')
        rows.append(row)
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
    # Use only the exact selected source views above, never the hidden
    # canonical body or candidates from an earlier, different presentation.
    from .semantic_contract import source_facts
    canonical['literal_fact_candidates'] = source_facts(rows)
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
    fit(canonical, stream['maximum'] - stream.get('feedback_reserve',0))
    return canonical


def _schedule_comparisons(store,cid,stream,pages):
    """Reduce a growing notebook in bounded groups, not a new giant prompt.

    Every parent records every child. Require strict frontier shrinkage, so a
    pathological irreducible source/contract floor cannot create an endless
    summarization loop. It remains an explicit input gap instead.
    """
    groups=[];remaining=list(pages)
    while remaining:
        children=[remaining.pop(0)];best_pack=None
        for candidate in list(remaining):
            try:pack=reduction_pack(stream,children+[candidate],final=False)
            except InputBudgetError:continue
            children.append(candidate);remaining.remove(candidate);best_pack=pack
        # Non-adjacent notes can fit together even if neighboring notes do not.
        # Membership and exact original sources are unchanged; this is input
        # packing, not relevance-based pruning or a semantic conclusion.
        groups.append((children,best_pack))
    if len(groups)>=len(pages):
        # One bounded, model-directed refocusing pass per frontier generation.
        # It rereads the original page, not a synthetic summary, and cannot
        # publish or close objections. If no progress follows, fail explicitly.
        if all(page.get('focus_policy_version')==FOCUS_VERSION for page in pages):
            raise InputBudgetError('No bounded comparison can reduce this source/contract frontier after source refocusing; original pages remain unadopted')
        frontier=[]
        with store.tx():
            for page in pages:
                if page.get('focus_policy_version')==FOCUS_VERSION:
                    frontier.append(page['id']);continue
                node=store.add('review_page',cid,task_id=stream['task_id'],batch_id=stream['batch_id'],
                    stream_id=stream['id'],index=len(frontier),level=page.get('level',0),status='pending',
                    pack=deepcopy(page['pack']),focus_of=page['id'],
                    focus_generation=page.get('focus_generation',0)+1,focus_policy_version=FOCUS_VERSION,
                    focus_budget_chars=(stream['maximum']-stream.get('feedback_reserve',0))//2,
                    covered_source_count=page.get('covered_source_count',len(page['included_ids'])))
                frontier.append(node['id'])
            store.update(stream['id'],frontier=frontier)
        return
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
    from .objection_ledger import current, view
    task=store.get(stream['task_id'])
    owner_ids={d['id'] for d in stream['canonical']['required_dossiers']}
    # Preserve live objections whose original owner was rebound to a current
    # hypothesis/dossier. A scoped stream must not erase a foreign open dispute
    # merely because the current dossier roster changed.
    for objection in stream['canonical'].get('open_objections',[]):
        owner_ids.add(objection.get('dossier_id'))
        owner_ids.add(objection.get('origin_dossier_id'))
        owner_ids.add(objection.get('rebound_dossier_id'))
    live=view(current(store,cid,task,[owner for owner in owner_ids if owner]))
    known={row['id']:row for row in live}
    rebound={o['id']:o for o in stream['canonical'].get('open_objections',[])}
    stream['open_objections'] = [dict(row,dossier_id=rebound[row['id']]['dossier_id'],
        origin_dossier_id=row['dossier_id']) if row['id'] in rebound else row for row in known.values()]
    pages = [store.get(pid) for pid in stream['frontier']]
    if not pages or any(p['stream_id']!=stream_id for p in pages):
        raise ValueError('Incomplete durable review page manifest')
    pending = next((p for p in pages if p['status'] != 'reviewed'), None)
    if pending:
        pack = deepcopy(pending['pack'])
        if pending.get('level',0)>0 and not pending.get('focus_of'):
            pack=reduction_pack(stream,[store.get(pid) for pid in pending['child_ids']],final=False)
            return pack,{'stream_id':stream_id,'page_id':pending['id'],'phase':'comparison_page'}
        visible={o['id'] for o in pack['observations']}
        pack['open_objections']=[o for o in stream['open_objections'] if set(o['observation_ids'])&visible]
        pack['unpresented_open_objection_count']=len(stream['open_objections'])-len(pack['open_objections'])
        pack['review_stream'] = {
            'id': stream_id, 'page_id': pending['id'], 'phase': 'focus_page' if pending.get('focus_of') else 'source_page',
            'pages_reviewed': sum(p['status'] == 'reviewed' for p in pages),
            'pages_total': stream['page_count'], 'finalization_allowed': False,
            'instruction': ('Assess this source page only. Your output is an unpublished working note. '
                'Do not infer whole-dossier absence/completeness. Express competing explanations '
                'and discriminating next checks. Check outcomes apply only to sources visible on this page. '
                'Other open objections remain in the ledger and must not be inferred resolved. '
                'Final synthesis happens only after every page has a validated assessment.')}
        previous=pack.pop('previous_assessment',None)
        if previous:
            pack['prior_assessment_context']={
                'canonical_input_sha256':stream['canonical_sha256'],
                'retrieval':'canonical_pack.previous_assessment','narrative_not_repeated':True,
                'unresolved_context':[{'dossier_id':f.get('dossier_id'),
                    'alternatives':deepcopy(f.get('alternatives',[])),
                    'remaining_checks':deepcopy(f.get('remaining_checks',[])),
                    'counterevidence_ids':deepcopy(f.get('counterevidence_ids',[]))}
                    for f in previous.get('findings',[])]}
        if pending.get('focus_of'):
            pack['review_stream']['selection_budget_characters']=pending['focus_budget_chars']
            pack['review_stream']['instruction']=(
                'The previous combined source selection exceeded the bounded comparison context. '
                'Reread this ORIGINAL page and choose the smallest sufficient sources for each narrow proposition, '
                'with source_selections mode=excerpt containing exact decisive passages from long excerpts, '
                'or mode=metadata_only when no body fact is asserted, never both for one source. Preserve material '
                'contrary context, distinct explanations, every open objection and unassessed contract. '
                'Do not repeat every row as a citation or make count/range claims without their sources. '
                'This is an unpublished working note, not a final conclusion. Keep prose concise. '
                'Aim for selected sources PLUS note and required contracts within selection_budget_characters. '
                'This is a planning target, not equal ownership of shared contracts: a larger note is admissible '
                'only when its measured contribution shrinks and fits the actual comparison envelope. '
                'The final combination must still fit under the normal lossless representation. '
                'Every finding, stage, relevance and check citation carries its source forward; choose '
                'sufficient direct sources, not every returned row. Quotes of already short fields add overhead; '
                'focus long excerpts when necessary. Unknown stages need not repeat citations or prose. '
                'A partial search is inconclusive; its every matching row is not needed to establish that limit. '
                'If essential contrary evidence cannot fit, disclose the limitation; never discard it for budget.')
        pack['finalization_allowed'] = False
        fit(pack, stream['maximum'])
        return pack, {'stream_id': stream_id, 'page_id': pending['id'], 'phase':pack['review_stream']['phase']}
    try:pack = reduction_pack(stream, pages)
    except InputBudgetError:
        _schedule_comparisons(store,cid,stream,pages)
        return prepare(store,cid,stream_id)
    return pack, {'stream_id': stream_id, 'phase': 'synthesis'}


def focus_budget_errors(store,meta,pack,output,selected):
    """Reject a non-progressing selection, not the underlying source evidence."""
    if meta.get('phase')!='focus_page':return []
    page=store.get(meta['page_id']);stream=store.get(meta['stream_id'])
    stream['open_objections']=deepcopy(pack.get('open_objections',[]))
    candidate={**page,'pack':pack,'selected_pack':selected,'output':output,
        'included_ids':[o['id'] for o in resolved(pack)['observations']],
        'receipt_id':'pending-validation','objection_ids':[o['id'] for o in stream['open_objections']]}
    try:
        reduced=reduction_pack(stream,[candidate],final=False)
        from .review_context import model_view_size
        def compact_size(value):
            # Force every reversible metadata sharing pass for a like-for-like
            # marginal-cost comparison. Never trim source or contract fields.
            value=deepcopy(value)
            try:fit(value,1)
            except InputBudgetError:pass
            return model_view_size(value)
        after=compact_size(reduced)
        if after>page['focus_budget_chars']:
            previous=store.get(page['focus_of'])
            sizing=deepcopy(stream);sizing['maximum']=10**12
            before=compact_size(reduction_pack(sizing,[previous],final=False))
            if after>=before:
                raise InputBudgetError(f'Source reselection made no measured progress: {before} -> {after} characters; target {page["focus_budget_chars"]}')
        # A strict reduction is not a final fit guarantee. Only one focus pass
        # is allowed for each frontier generation; subsequent real combination
        # still must fit, or remains an explicit input gap.
    except InputBudgetError as exc:
        cited=set(references(output))
        return [{'code':'focus_selection_over_budget','maximum_characters':page['focus_budget_chars'],
                 'selected_source_sizes':[{'observation_id':o['id'],
                    'characters_before_shared_metadata':len(json.dumps(o,ensure_ascii=False,separators=(',',':')))}
                    for o in selected['observations'] if o['id'] in cited],
                 'detail':str(exc),'instruction':'Reread originals. Narrow the proposition and select sufficient direct sources/exact long-excerpt quotes, preserving contrary evidence. Repeated citations in stages, relevance and check assessments also carry their sources. Do not fabricate absence or remove necessary evidence to pass.'}]
    return []


def accept_page(store, batch, meta, output, receipt_id, included_ids, objection_ids=(), presented_pack=None):
    """Caller holds the validated-attempt transaction and stale-scope guard."""
    page = store.get(meta['page_id'])
    if page['status'] != 'pending':
        raise ValueError('Review page already accepted')
    from .review_focus import project
    presented=presented_pack if presented_pack is not None else page['pack']
    selected=project(presented,output)
    stream=store.get(meta['stream_id'])
    catalog=dict(stream.get('source_span_catalog',{}))
    catalog.update({sid:row for observation in selected['observations'] for sid,row in span_rows(observation)})
    store.update(stream['id'],source_span_catalog=catalog)
    store.update(page['id'], status='reviewed', output=deepcopy(output),pack=deepcopy(presented),selected_pack=selected,
                 receipt_id=receipt_id, included_ids=list(included_ids),objection_ids=list(objection_ids))
    store.update(batch['id'], attempts=0, validation_feedback=None, active_attempt_id=None)


def complete(store, meta, receipt_id):
    store.update(meta['stream_id'], status='synthesized', synthesis_receipt_id=receipt_id)
