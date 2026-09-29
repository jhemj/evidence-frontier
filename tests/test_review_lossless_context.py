from copy import deepcopy

import pytest

from workbench.review_context import (
    InputBudgetError,
    expand_metadata,
    fit_metadata_only,
)
from workbench.structured_context import expand as expand_structured


def _pack():
    request = {
        "tool": "read_file",
        "path": "/evidence/source.bin",
        "reason": "retain this complete request rationale",
        "nested": {"cursor": 17, "conditions": ["a", "b"]},
    }
    observations = []
    for index in range(4):
        observations.append({
            "id": f"OBS-{index}",
            "evidence_id": "EVIDENCE-1",
            "fields": {
                "excerpt": "full source text " + ("x" * 120),
                "source_sha256": "a" * 64,
                "artifact_path": "/evidence/source.bin",
                "context_limit": 4096,
                "custom": {"list": [index, "keep"], "marker": "exact"},
            },
            "context_request": deepcopy(request),
        })
    contract = {
        "dossier_id": "D-1",
        "contract_id": "C-1",
        "success_condition": "confirmed by source",
        "refutation_condition": "positive contrary source",
        "inconclusive_condition": "not enough evidence",
        "timeline_role": "supporting",
        "counterevidence_ids": ["OBS-9"],
        "extension": {"preserve": [1, 2, 3]},
    }
    return {
        "observations": observations,
        "executed_checks": [
            {"id": "CHECK-1", "request": request, "contracts": [contract]},
            {"id": "CHECK-2", "request": request, "contracts": [deepcopy(contract)]},
        ],
        "previous_assessment": {
            "summary": "previous assessment must remain exact",
            "findings": [{"dossier_id": "D-1", "counterevidence": ["OBS-9"],
                          "stages": [{"stage": "review", "statement": "retain all text"}]}],
        },
    }


def _restore(pack):
    expand_structured(pack)
    expand_metadata(pack)


def test_metadata_only_round_trips_all_source_and_contract_fields():
    original = _pack()
    packed = deepcopy(original)
    # A generous bound still exercises the reversible envelope after the
    # initial size check by using a deliberately smaller bound than original.
    # The source pack is just above this bound; factoring must make it fit.
    fit_metadata_only(packed, maximum=3300)
    _restore(packed)
    assert packed == original


def test_repeated_nested_metadata_is_exactly_factored_without_merging_sources():
    from workbench.review_context import share_observation_fields, serialize
    original=_pack()
    nested=[{'original':'/var/lib/example/agent', 'absolute':'/var/lib/example/agent',
             'basis':'literal argument, not proof of execution', 'flags':[True,1,'1']}]
    for row in original['observations']:row['fields']['referenced_paths']=deepcopy(nested)
    packed=deepcopy(original); before=len(serialize(packed))
    share_observation_fields(packed)
    assert len(serialize(packed))<before
    default=next(x for x in packed['shared_observation_field_defaults'] if x['field']=='referenced_paths')
    assert default['value']==nested and default['observation_indexes']==[0,1,2,3]
    _restore(packed)
    assert packed==original


def test_nested_provenance_defaults_preserve_raw_times_and_mixed_origins():
    from workbench.review_context import share_observation_path_defaults
    original=_pack()
    for i,row in enumerate(original['observations']):
        row['time_semantics']={'raw':f'2026-01-01T00:00:0{i}Z', 'epoch_nanoseconds':str(i),
            'source_origin':{'artifact_path':'/objects/'+('a'*64 if i<3 else 'b'*64),
                             'source_sha256':('a' if i<3 else 'b')*64}}
    packed=deepcopy(original);share_observation_path_defaults(packed)
    entry=packed['shared_observation_path_defaults'][0]
    assert entry['observation_indexes']==[0,1,2]
    assert packed['observations'][3]['time_semantics']==original['observations'][3]['time_semantics']
    assert [r['time_semantics']['raw'] for r in packed['observations']]==[r['time_semantics']['raw'] for r in original['observations']]
    _restore(packed);assert packed==original


def test_repeated_short_labels_factor_keys_but_never_merge_record_coordinates():
    from workbench.review_context import share_observation_fields, share_observation_path_defaults, serialize
    original={'observations':[]}
    for index in range(20):
        original['observations'].append({'id':f'O-{index}', 'fields':{
            'excerpt':f'raw-{index}', 'byte_offset':index*17, 'line':index+1,
            'inspection_context':False, 'source_complete':True, 'signals':[]},
            'time_semantics':{'raw':f'time-{index}', 'time_kind':'occurred',
                              'precision':'second', 'timezone_assumed':False}})
    packed=deepcopy(original)
    share_observation_fields(packed)
    share_observation_path_defaults(packed)
    assert len(serialize(packed))<len(serialize(original))
    assert any(e['field']=='inspection_context' for e in packed['shared_observation_field_defaults'])
    for index,row in enumerate(packed['observations']):
        assert row['fields']['byte_offset']==index*17
        assert row['fields']['excerpt']==f'raw-{index}'
        assert row['time_semantics']['raw']==f'time-{index}'
    _restore(packed);assert packed==original


def test_metadata_only_oversize_is_typed_and_does_not_truncate():
    original = _pack()
    original["observations"][0]["fields"]["excerpt"] = "immutable " + "Z" * 10000
    packed = deepcopy(original)
    with pytest.raises(InputBudgetError):
        fit_metadata_only(packed, maximum=500)
    _restore(packed)
    assert packed == original


def test_invalid_lossless_references_fail_closed():
    bad = {"observations": [{"id": "O", "fields": {}, "context_request_ref": 99}],
           "observation_context_request_definitions": []}
    with pytest.raises(ValueError):
        expand_metadata(bad)

    bad = {"observations": [], "observation_context_request_definitions": [
        {"ref": 1, "request": {}}, {"ref": 1, "request": {}}]}
    with pytest.raises(ValueError):
        expand_metadata(bad)

    bad = {"observations": [{"id": "O", "fields": {}}],
           "shared_observation_field_defaults": [
               {"field": "x", "value": 1, "observation_indexes": [4]}]}
    with pytest.raises(ValueError):
        expand_metadata(bad)


def test_absent_contract_identity_and_duplicate_observation_ids_round_trip():
    pack = {"observations": [
        {"id": "DUP", "fields": {"marker": "same long marker value"}},
        {"id": "DUP", "fields": {"marker": "same long marker value"}},
        {"id": "DUP", "fields": {"marker": "same long marker value"}},
    ], "executed_checks": [{"contracts": [{"success_condition": "ok", "custom": [1, 2]}]}]}
    original = deepcopy(pack)
    with pytest.raises(InputBudgetError):
        fit_metadata_only(pack, maximum=1)
    _restore(pack)
    assert pack == original
    assert "dossier_id" not in original["executed_checks"][0]["contracts"][0]


@pytest.mark.parametrize("maximum", [0, -1, True, "36000"])
def test_metadata_only_requires_positive_numeric_budget(maximum):
    with pytest.raises(ValueError):
        fit_metadata_only(_pack(), maximum=maximum)

    bad = {"executed_checks": [{"contracts": [{"condition_ref": 4}]}],
           "contract_definitions": [{"ref": 1, "fields": {}}]}
    with pytest.raises(ValueError):
        expand_metadata(bad)


def test_check_request_fields_are_reversible_and_not_compacted():
    original = _pack()
    packed = deepcopy(original)
    # Force the factoring path while avoiding a budget failure.
    with pytest.raises(InputBudgetError):
        fit_metadata_only(packed, maximum=1)
    assert "check_request_definitions" in packed
    _restore(packed)
    assert packed["executed_checks"] == original["executed_checks"]
