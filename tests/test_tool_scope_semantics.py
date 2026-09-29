from workbench.retrieval import fingerprint_scope


def test_static_file_ignores_non_applied_range_but_keeps_identity():
    base = {'tool': 'static_file', 'path': '/bin/x', 'byte_length': 8192,
            'byte_offset': 0, 'partition_offset': 4096, 'inode': 17,
            'reason': 'one'}
    same_operation = {**base, 'byte_length': 65536, 'byte_offset': 9000,
                      'reason': 'different explanation'}
    assert fingerprint_scope(base) == fingerprint_scope(same_operation)
    assert fingerprint_scope(base) != fingerprint_scope({**base, 'inode': 18})
    assert fingerprint_scope(base) != fingerprint_scope({**base, 'partition_offset': 8192})


def test_read_file_range_remains_distinct():
    base = {'tool': 'read_file', 'path': '/bin/x', 'byte_length': 8192,
            'byte_offset': 0, 'partition_offset': 4096, 'inode': 17}
    assert fingerprint_scope(base) != fingerprint_scope({**base, 'byte_length': 65536})
    assert fingerprint_scope(base) != fingerprint_scope({**base, 'byte_offset': 4096})


def test_other_tool_semantics_are_not_collapsed():
    search = {'tool': 'search', 'query': 'needle', 'path': '/logs', 'limit': 10}
    assert fingerprint_scope(search) != fingerprint_scope({**search, 'query': 'other'})
    source = {'tool': 'read_source', 'path': 'RUN-x/objects/a',
              'byte_length': 8192, 'byte_offset': 0, 'source_offset': 0}
    assert fingerprint_scope(source) != fingerprint_scope({**source, 'byte_offset': 1})
