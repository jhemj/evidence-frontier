from copy import deepcopy

import pytest

from workbench.model_tables import MANIFEST, VERSION, decode, encode


def _rows(count=20):
    return [
        {
            'row_identifier': f'row-{index}',
            'long_repeated_field_name': 'value-' + ('x' * 40),
            'second_long_repeated_field_name': 'other-' + ('y' * 40),
            'ordinal': index,
        }
        for index in range(count)
    ]


def test_roundtrip_preserves_unicode_newlines_and_scalar_types():
    rows = _rows()
    rows[0].update({'text': '첫 줄\n第二行\r\n끝', 'integer': 7, 'decimal': 2.5,
                    'boolean': True, 'null_value': None})
    rows[1].update({'text': 'emoji 😀\n', 'integer': -3, 'decimal': 0.0,
                    'boolean': False, 'null_value': None})
    pack = {'observations': rows}

    encoded = encode(pack)
    assert MANIFEST in encoded
    assert decode(encoded) == pack
    assert encoded['observations']['rows'][0][encoded['observations']['columns'].index('text')] == '첫 줄\n第二行\r\n끝'
    assert encoded['observations']['rows'][1][encoded['observations']['columns'].index('boolean')] is False


def test_roundtrip_preserves_duplicate_rows_and_order():
    duplicate = _rows(1)[0]
    rows = [deepcopy(duplicate), deepcopy(duplicate), _rows(1)[0], deepcopy(duplicate)]
    rows[2]['row_identifier'] = 'middle'
    rows.extend(_rows(4))
    pack = {'observations': rows}

    encoded = encode(pack)
    assert MANIFEST in encoded
    assert decode(encoded)['observations'] == rows
    assert [row['row_identifier'] for row in decode(encoded)['observations']] == [row['row_identifier'] for row in rows]


def test_missing_cells_are_distinct_from_literal_null():
    rows = _rows()
    rows[0]['optional_value'] = None
    rows[1]['optional_value'] = 'present'
    # The third row intentionally has no optional_value key.
    pack = {'observations': rows}

    encoded = encode(pack)
    table = encoded['observations']
    column = table['columns'].index('optional_value')
    assert [0, column] not in table.get('missing_cells', [])
    assert [2, column] in table['missing_cells']
    decoded = decode(encoded)
    assert 'optional_value' in decoded['observations'][0]
    assert decoded['observations'][0]['optional_value'] is None
    assert 'optional_value' not in decoded['observations'][2]


def test_nested_tables_use_manifest_paths_and_roundtrip_exactly():
    # Two outer rows avoid encoding the outer list; each nested entries list is
    # large enough to use the table representation independently.
    pack = {
        'containers': [
            {'container_id': 'first', 'entries': _rows(20)},
            {'container_id': 'second', 'entries': _rows(20)},
        ],
    }

    encoded = encode(pack)
    paths = encoded[MANIFEST]['paths']
    assert ['containers', 0, 'entries'] in paths
    assert ['containers', 1, 'entries'] in paths
    assert decode(encoded) == pack


def test_source_shaped_like_table_is_not_decoded_without_manifest_path():
    source_table = {'columns': ['path', 'excerpt'], 'rows': [['/raw', 'source data']]}
    pack = {'source_record': source_table, 'observations': _rows()}

    encoded = encode(pack)
    assert encoded['source_record'] == source_table
    assert decode(encoded)['source_record'] == source_table
    assert ['source_record'] not in encoded[MANIFEST]['paths']


def test_mutated_table_value_fails_hash_check():
    encoded = encode({'observations': _rows()})
    mutated = deepcopy(encoded)
    mutated['observations']['rows'][0][1] = 'tampered'

    with pytest.raises(ValueError, match='hash mismatch'):
        decode(mutated)


def test_invalid_missing_mask_fails_closed():
    encoded = encode({'observations': _rows()})
    malformed = deepcopy(encoded)
    malformed['observations']['missing_cells'] = [[0, 0]]

    with pytest.raises(ValueError, match='missing cell'):
        decode(malformed)


@pytest.mark.parametrize('path', [
    ['observations', 'not-a-row-index'],
    ['observations', 'rows', -1, 0],
    ['observations', 'rows', True, 0],
])
def test_invalid_manifest_path_fails_closed(path):
    encoded = encode({'observations': _rows()})
    malformed = deepcopy(encoded)
    malformed[MANIFEST]['paths'] = [path]

    with pytest.raises(ValueError, match='Invalid table path'):
        decode(malformed)


def test_global_no_savings_returns_original_without_manifest():
    pack = {'small': [{'a': 1}, {'b': 2}, {'c': 3}]}

    encoded = encode(pack)
    assert encoded == pack
    assert MANIFEST not in encoded


def test_manifest_version_is_explicit_for_encoded_tables():
    encoded = encode({'observations': _rows()})
    assert encoded[MANIFEST]['version'] == VERSION
