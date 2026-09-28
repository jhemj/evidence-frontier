from workbench.integrity_scope import assess


def test_missing_embedded_digest_is_not_original_hash_match():
    result = assess('SHA256 hash stored in file:\tN/A\nSHA256 hash calculated over data:\t'+'a'*64+'\n')
    assert result['logical_digests']['sha256']['comparison'] == 'not_available'
    assert result['logical_digests']['sha256']['calculated'] == 'a'*64
    assert not result['external_acquisition_hash_compared']


def test_embedded_match_is_not_independent_acquisition_comparison():
    result = assess('MD5 hash stored in file: '+ 'b'*32+'\nMD5 hash calculated over data: '+ 'b'*32+'\n', reused=True)
    assert result['logical_digests']['md5']['comparison'] == 'matched'
    assert result['logical_verification_reused']
    assert not result['external_acquisition_hash_compared']


def test_raw_hash_computation_does_not_claim_logical_image_verification():
    result = assess()
    assert '논리 데이터를 모두 읽고' not in result['scope_note']
    assert all(x['comparison'] == 'not_available' for x in result['logical_digests'].values())
