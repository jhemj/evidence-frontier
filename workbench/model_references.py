"""Lossless, call-scoped reference handles at the model transport boundary.

Opaque ledger identifiers are storage identities, not language tasks. The model
selects short handles; exact decoding precedes the existing scope/semantic gates.
This is never fuzzy citation repair and never changes retained source fields.
"""
from copy import deepcopy
import hashlib
import json
import re

VERSION = 'model-references-1'
_LEDGER = re.compile(r'^[A-Z][A-Z_]*-[a-f0-9]{12,64}$')
_HANDLE = re.compile(r'^R[0-9]+$')
_LITERAL_KEYS = {'fields', 'value', 'excerpt', 'quote', 'raw', 'raw_output'}
_NARRATIVE_KEYS = {'summary', 'reason', 'reasoning', 'alternatives', 'uncertainty',
                   'missing_checks', 'remaining_checks', 'change_reason', 'statement',
                   'success_condition', 'refutation_condition', 'inconclusive_condition'}


def _reference_key(key):
    return key == 'id' or key.endswith('_id') or key.endswith('_ids') or key == 'ids' or bool(_LEDGER.fullmatch(key))


class ReferenceProjection:
    def __init__(self, pack):
        found = set()
        self.existing_references = set()

        def collect(value, key=''):
            if key in _LITERAL_KEYS:
                return
            if isinstance(value, dict):
                for name, item in value.items():
                    if _LEDGER.fullmatch(name):
                        found.add(name)
                    if _HANDLE.fullmatch(name):
                        self.existing_references.add(name)
                    collect(item, name)
            elif isinstance(value, list):
                for item in value:
                    collect(item, key)
            elif isinstance(value, str) and _reference_key(key):
                self.existing_references.add(value)
                if _LEDGER.fullmatch(value) or (key == 'contract_id' and re.fullmatch(r'[a-f0-9]{64}', value)):
                    found.add(value)

        collect(pack)
        self.encode_map = {}
        counter = 1
        for value in sorted(found):
            while f'R{counter}' in self.existing_references:
                counter += 1
            self.encode_map[value] = f'R{counter}'
            counter += 1
        self.decode_map = {handle: value for value, handle in self.encode_map.items()}
        self.source_sha256 = hashlib.sha256(json.dumps(pack, sort_keys=True, ensure_ascii=False,
                                                     separators=(',', ':')).encode()).hexdigest()

    def encode(self, pack):
        def visit(value, key=''):
            if key in _LITERAL_KEYS:
                return deepcopy(value)
            if isinstance(value, dict):
                return {self.encode_map.get(name, name): visit(item, name) for name, item in value.items()}
            if isinstance(value, list):
                return [visit(item, key) for item in value]
            if isinstance(value, str) and _reference_key(key):
                return self.encode_map.get(value, value)
            return value
        return visit(pack)

    def decode(self, output):
        # Generated prose sometimes cites handles despite the structural-only
        # instruction. Expand exact tokens only; literal values, titles and tool
        # arguments are not rewritten and no approximate match is ever used.
        handles = re.compile(r'(?<![A-Za-z0-9_])R[0-9]+(?![A-Za-z0-9_])')
        def visit(value, key=''):
            if key in _LITERAL_KEYS:
                return deepcopy(value)
            if isinstance(value, dict):
                return {name: visit(item, name) for name, item in value.items()}
            if isinstance(value, list):
                return [visit(item, key) for item in value]
            if isinstance(value, str) and _reference_key(key):
                if _HANDLE.fullmatch(value) and value not in self.decode_map and value not in self.existing_references:
                    raise ValueError(f'Unknown call-scoped reference handle: {value}')
                return self.decode_map.get(value, value)
            if isinstance(value, str) and key in _NARRATIVE_KEYS:
                return handles.sub(lambda match: self.decode_map.get(match[0], match[0]), value)
            return value
        return visit(output)

    def receipt(self):
        return {'version': VERSION, 'scope': 'this model call only; not evidence or citation authority',
                'source_pack_sha256': self.source_sha256, 'bindings': dict(self.decode_map)}


INSTRUCTION = (
    'Transport references: ledger IDs in structural reference fields use call-local handles R1, R2, etc. '
    'Copy the exact handle from the current field/allowlist; do not spell ledger IDs from memory. '
    'Handles are not evidence or permission: all per-finding, check and source-span scopes still apply. '
    'Handles from earlier calls or source text are not usable unless in this call\'s structural allowlist. '
    'Never alter literal source fields or fact_assertions.value to match a handle. '
    'Keep handles in structured reference fields only, not titles or narrative text. '
)
