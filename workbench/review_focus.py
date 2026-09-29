"""Model-selected exact excerpts for a bounded, source-backed comparison.

Selection is interpretation, not lossless compression. The original presented
page stays immutable. Only exact matches become separately hashed field spans;
all non-excerpt fields and source locators survive. This certifies the quote's
binding, never its relevance, completeness, or the model's interpretation.
"""
from copy import deepcopy
import hashlib
import json


def project(pack, output):
    from .review_stream import resolved, references, span_rows, span_references
    result = resolved(pack)
    rows = {row['id']: row for row in result.get('observations', [])}
    cited = set(references(output))
    # New working notes make an atomic choice per source. Legacy immutable
    # receipts remain replayable, but mixing the two contracts is forbidden.
    if output.get('source_selections'):
        if output.get('excerpt_selections') or output.get('source_metadata_selections'):
            raise ValueError('Do not mix atomic and legacy source selection contracts')
        output=deepcopy(output);quotes=[];metadata=[];seen=set()
        for choice in output['source_selections']:
            oid=choice['observation_id']
            if oid in seen:raise ValueError('Choose one mode per source, never excerpt and metadata_only together')
            seen.add(oid)
            if choice['mode']=='metadata_only':metadata.append({'observation_id':oid,'reason':choice['reason']})
            elif choice['mode']=='excerpt':
                for passage in choice['passages']:
                    quotes.append({**passage,'observation_id':oid,'reason':choice['reason']})
            else:raise ValueError('Unknown source selection mode')
        output['excerpt_selections']=quotes;output['source_metadata_selections']=metadata
    selections = {}; whole_views=set()
    for selection in output.get('excerpt_selections', []):
        oid = selection['observation_id']
        if oid not in rows or oid not in cited:
            raise ValueError('Excerpt selection must belong to a cited, presented observation')
        row = rows[oid]
        parent_id = selection.get('source_span_id', '')
        if row.get('source_span'):
            parent = row['source_span']
            if parent_id and parent_id != parent['span_id']:
                raise ValueError('Excerpt selection must identify the presented parent span')
            text = row['fields']['excerpt']
        elif 'excerpt_spans' in row.get('fields', {}):
            parents=row['fields']['excerpt_spans']
            parent = parents[0] if not parent_id and len(parents)==1 else next((s for s in parents if s['span_id']==parent_id),None)
            if parent is None:
                raise ValueError('Excerpt selection parent span is not presented')
            text = parent['text']
        else:
            if parent_id:
                raise ValueError('Whole-field excerpt selection has no parent span')
            text = row.get('fields', {}).get('excerpt')
            parent = {}
        quote = selection['quote']
        occurrence = selection.get('occurrence', 0)
        if not isinstance(text,str):
            raise ValueError(f'Observation {oid} has no presented text excerpt. Structured fields are already retained; use observation citations and exact fact_assertions, not excerpt_selections or serialized-JSON quotes.')
        if (not isinstance(text, str) or not isinstance(quote, str) or not quote
                or isinstance(occurrence, bool) or not isinstance(occurrence, int) or occurrence < 0):
            raise ValueError('Excerpt selection requires a nonempty literal quote and a valid occurrence')
        # Overlapping occurrences are counted, with no fuzzy/normalized match.
        start = -1
        for _ in range(occurrence + 1):
            start = text.find(quote, start + 1)
            if start < 0:
                raise ValueError('Excerpt selection does not exactly match the presented source')
        if quote==text and occurrence==0 and len(row.get('fields',{}).get('excerpt_spans',[]))<=1:
            whole_views.add(oid)
        begin = parent.get('byte_start', 0) + len(text[:start].encode('utf-8'))
        raw = quote.encode('utf-8')
        full_hash = parent.get('full_sha256') or hashlib.sha256(text.encode('utf-8')).hexdigest()
        end = begin + len(raw)
        identity = [oid, '/fields/excerpt', begin, end, full_hash]
        span = {'span_id': 'SPAN-' + hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                'observation_id': oid, 'field_pointer': '/fields/excerpt',
                'byte_start': begin, 'byte_end': end, 'full_sha256': full_hash,
                'sha256': hashlib.sha256(raw).hexdigest(), 'text': quote,
                'coordinate_basis': 'canonical_field_utf8', 'extent': 'partial_field', 'truncated': True}
        selected = selections.setdefault(oid, [])
        if any(s['span_id'] == span['span_id'] for s in selected):
            raise ValueError('Duplicate excerpt selection')
        selected.append(span)
    # A selected excerpt cannot discard a literal asserted by this very note.
    for finding in output.get('findings', []):
        for fact in finding.get('fact_assertions', []):
            if fact['observation_id'] in selections and fact['pointer'].startswith('/fields/excerpt'):
                value = fact.get('value')
                if not isinstance(value, str) or not any(value in s['text'] for s in selections[fact['observation_id']]):
                    raise ValueError('Excerpt selection discards an asserted source literal')
    excerpt_ids=set(selections)
    for oid, spans in selections.items():
        # Selecting the complete already-presented view changes no content or
        # provenance. Keep that exact view rather than wrapping a short command
        # in a new, larger partial-span manifest. Still validated every quote
        # and duplicate above; multi-parent views cannot use this identity path.
        if oid in whole_views:continue
        row = rows[oid]
        spans.sort(key=lambda s: (s['byte_start'], s['byte_end']))
        row['fields'].pop('excerpt', None)
        row['fields']['excerpt_spans'] = spans
        row.pop('source_span', None)
        row['source_spans'] = [{k: v for k, v in span.items() if k != 'text'} for span in spans]
        row['projection_omissions'] = {'canonical_field': '/fields/excerpt',
            'replacement': 'model_selected_exact_spans', 'span_ids': [s['span_id'] for s in spans],
            'scope': 'Only these field ranges are re-presented. The full original remains in the page receipt; gaps are not evidence of absence.'}
    metadata_ids=set()
    for selection in output.get('source_metadata_selections',[]):
        oid=selection['observation_id']
        if oid not in rows or oid not in cited or oid in metadata_ids or oid in excerpt_ids:
            raise ValueError('Metadata selection must be a unique cited, presented source, without excerpt selections')
        row=rows[oid]
        if any(oid in check.get('observation_ids',[]) and check.get('outcome') in ('supports','refutes')
               for check in output.get('check_assessments',[])):
            raise ValueError('Metadata-only selection cannot remove the body cited by a positive check outcome. Retain the presented view or select its exact supporting/contrary passage; a partial-status-only assessment is inconclusive.')
        if any(oid in objection.get('observation_ids',[]) for objection in result.get('open_objections',[])):
            raise ValueError('Metadata selection cannot discard a presented open objection source')
        owned={sid for sid,_ in span_rows(row)}
        if owned.intersection(span_references(output)):
            raise ValueError('Metadata selection cannot discard a cited source span')
        for finding in output.get('findings',[]):
            for fact in finding.get('fact_assertions',[]):
                if fact['observation_id']==oid and (fact['pointer'] in ('','/','/fields')
                        or fact['pointer'].startswith(('/fields/excerpt','/source_span'))):
                    raise ValueError('Metadata selection cannot discard an asserted excerpt literal')
        fields=row.get('fields',{})
        if not ('excerpt' in fields or 'excerpt_spans' in fields):
            raise ValueError('Metadata selection requires a presented excerpt body')
        omitted={k:deepcopy(fields[k]) for k in ('excerpt','excerpt_spans') if k in fields}
        parents={k:deepcopy(row[k]) for k in ('source_span','source_spans','projection_omissions') if k in row}
        for key in ('excerpt','excerpt_spans'):fields.pop(key,None)
        for key in ('source_span','source_spans'):row.pop(key,None)
        row['projection_omissions']={
            'canonical_field':'/fields/excerpt','replacement':'model_selected_metadata_only',
            'omitted_presentation_sha256':hashlib.sha256(json.dumps(omitted,sort_keys=True,ensure_ascii=False,
                separators=(',',':')).encode()).hexdigest(),
            'parent_presentation':parents,'reason':selection['reason'],
            'scope':'All non-excerpt fields retained. The body remains in the immutable source page and is NOT re-presented; metadata is not proof of content, execution or absence.'}
        metadata_ids.add(oid)
    return result
