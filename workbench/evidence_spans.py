"""Exact displayed field ranges, never guessed offsets into acquired bytes.

The parser may have decoded, redacted or excerpted its input. UTF-8 offsets
below address the retained observation field, not the original file. Original
locators remain separate so presentation coverage cannot become whole-source
review by accident. This module has no source IO and changes no observation.
"""
from copy import deepcopy
import hashlib
import json


VERSION = 'evidence-presentation-1'


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def utf8_spans(value, maximum_bytes):
    """Split text only at Unicode code-point boundaries.

    Returned offsets address the UTF-8 encoding of the retained field, never
    the acquired file.  The concatenation of ``text`` values is exactly the
    original value, including empty strings and multibyte characters.
    """
    if not isinstance(value, str) or isinstance(maximum_bytes, bool) or not isinstance(maximum_bytes, int) or maximum_bytes <= 0:
        raise ValueError('value must be text and maximum_bytes must be positive')
    if not value:
        return [{'text': '', 'byte_start': 0, 'byte_end': 0}]
    result=[]; start=0; byte_start=0; current=[]; current_bytes=0
    for char in value:
        encoded=char.encode('utf-8'); size=len(encoded)
        if current and current_bytes + size > maximum_bytes:
            text=''.join(current)
            result.append({'text':text, 'byte_start':byte_start,
                           'byte_end':byte_start+current_bytes})
            byte_start += current_bytes; current=[]; current_bytes=0
        current.append(char); current_bytes += size
    text=''.join(current)
    result.append({'text':text, 'byte_start':byte_start,
                   'byte_end':byte_start+current_bytes})
    return result


def field_span_manifest(observation, field_pointer='/fields/excerpt', maximum_bytes=4096):
    """Return a deterministic, lossless manifest for paged field spans."""
    fields=observation.get('fields', {})
    key=field_pointer.rsplit('/', 1)[-1]
    value=fields.get(key)
    if not isinstance(value, str):
        raise ValueError('span field is not text')
    raw=value.encode('utf-8'); full_sha256=_digest(raw)
    spans=[]
    for index, span in enumerate(utf8_spans(value, maximum_bytes)):
        span_raw=span['text'].encode('utf-8')
        spans.append({
            'span_id': 'SPAN-' + _digest(json.dumps(
                [observation.get('id'), field_pointer, index, span['byte_start'], span['byte_end'], full_sha256],
                separators=(',', ':')).encode()),
            'observation_id': observation.get('id'), 'field_pointer': field_pointer,
            'byte_start': span['byte_start'], 'byte_end': span['byte_end'],
            'byte_length': len(raw), 'sha256': _digest(span_raw),
            'full_sha256': full_sha256, 'coordinate_basis': 'canonical_field_utf8',
            'extent': 'partial_field', 'truncated': True,
        })
    return spans


def manifest(pack):
    from .review_stream import resolved
    rows = resolved(pack).get('observations', [])
    spans = []
    for row in rows:
        fields = row.get('fields', {})
        if isinstance(fields.get('excerpt_spans'), list):
            for item in fields['excerpt_spans']:
                raw=item.get('text','').encode('utf-8') if isinstance(item.get('text'),str) else b''
                spans.append({'span_id':item.get('span_id'), 'observation_id':row.get('id'),
                    'field_pointer':'/fields/excerpt_spans', 'sha256':item.get('sha256',_digest(raw)),
                    'full_sha256':item.get('full_sha256'), 'byte_start':item.get('byte_start',0),
                    'byte_end':item.get('byte_end',len(raw)), 'coordinate_basis':'canonical_field_utf8',
                    'extent':'partial_field', 'truncated':True, 'source_span':deepcopy(item),
                    'source_location':row.get('source_location'), 'source_sha256':fields.get('source_sha256'),
                    'source_completeness':'partial', 'projection_omissions':deepcopy(row.get('projection_omissions',{}))})
            continue
        partial = bool(row.get('projection_omissions') or fields.get('context_omissions') or fields.get('context_compacted')
                       or any(k.endswith('_truncated') and v for k, v in fields.items()))
        values = [] if isinstance(row.get('source_span'), dict) else [
            ('/fields', json.dumps(fields, sort_keys=True, ensure_ascii=False,
                                    separators=(',', ':')), 'canonical_fields_json_utf8')]
        if isinstance(fields.get('excerpt'), str):
            values.append(('/fields/excerpt', fields['excerpt'], 'canonical_field_utf8'))
        for pointer, value, basis in values:
            raw = value.encode('utf-8')
            identity = [row['id'], pointer, basis, _digest(raw), len(raw)]
            spans.append({
                'span_id': 'SPAN-' + _digest(json.dumps(identity).encode()),
                'observation_id': row['id'], 'field_pointer': pointer,
                'sha256': _digest(raw), 'byte_start': 0, 'byte_end': len(raw),
                'coordinate_basis': basis,
                'extent': 'partial_field' if partial else 'complete_field',
                'truncated': partial,
                'source_location': row.get('source_location'),
                'source_sha256': fields.get('source_sha256'),
                'source_completeness': 'partial' if partial or fields.get('source_complete') is False else 'unknown',
                'projection_omissions':deepcopy(row.get('projection_omissions',{})),
                'source_locator': {k: deepcopy(fields[k]) for k in (
                    'artifact_path', 'source_offset', 'byte_offset', 'byte_length',
                    'image_file_byte_offset', 'source_range_start', 'source_member',
                    'json_pointer', 'partition_offset', 'inode', 'locator_basis') if k in fields},
            })
            if isinstance(row.get('source_span'), dict):
                # A paged fragment is never a complete field, even though its
                # local excerpt is internally complete. Carry the canonical
                # UTF-8 coordinates and full digest into the ledger receipt.
                source_span=deepcopy(row['source_span'])
                spans[-1].update({
                    'span_id': source_span.get('span_id', spans[-1]['span_id']),
                    'byte_start': source_span.get('byte_start', 0),
                    'byte_end': source_span.get('byte_end', len(raw)),
                    'sha256': source_span.get('sha256', spans[-1]['sha256']),
                    'full_sha256': source_span.get('full_sha256'),
                    'extent': 'partial_field', 'truncated': True,
                    'source_span': source_span,
                })
    return {'version': VERSION, 'spans': spans,
            'scope': 'Exact retained observation fields presented, not acquired-file byte coverage. '
                     'Prepared/transmitted/validated are separate states; none proves semantic understanding.'}
