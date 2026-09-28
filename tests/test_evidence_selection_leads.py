from workbench.evidence_selection import audit, order


def observation(oid, rule, path=None):
    return {'id': oid, 'type': 'linux_detection',
            'fields': {'rule_id': rule, 'path': path or f'/x/{oid}'}}


def test_sparse_rule_family_gets_a_lane_before_high_volume_family():
    items = [observation(f'a{i}', 'rule_A') for i in range(50)] + [observation('b0', 'rule_B')]
    selected, reasons = order(items)
    first = selected[:2]
    assert {item['fields']['rule_id'] for item in first} == {'rule_A', 'rule_B'}
    assert reasons['b0'] == 'unpresented_lead_family'


def test_family_lane_is_permutation_and_path_agnostic():
    items = [observation('1', 'arbitrary_A', '/renamed/one'), observation('2', 'arbitrary_B', '/other/two'),
             observation('3', 'arbitrary_A', '/different/three')]
    a = order(items)[0][:2]
    b = order(list(reversed(items)))[0][:2]
    assert {x['fields']['rule_id'] for x in a} == {'arbitrary_A', 'arbitrary_B'}
    assert {x['fields']['rule_id'] for x in b} == {'arbitrary_A', 'arbitrary_B'}


def test_audit_records_omitted_lead_families():
    items = [observation('a', 'rule_A'), observation('b', 'rule_B')]
    selected, reasons = order(items)
    info = audit(items, selected[:1], reasons)
    assert info['lead_family_audit']['available'] == ['rule_A', 'rule_B']
    assert info['lead_family_audit']['omitted'] == ['rule_B']


def test_presented_families_fall_back_to_available_sources():
    items = [observation('a', 'rule_A'), observation('b', 'rule_B')]
    selected, _ = order(items, presented=['a', 'b'])
    assert selected
    assert {item['id'] for item in selected} == {'a', 'b'}


def test_selection_is_stable_when_store_ids_change():
    original = [
        observation('old-a', 'rule_A', '/physical/a'),
        observation('old-b', 'rule_B', '/physical/b'),
        observation('old-c', 'rule_A', '/physical/c'),
    ]
    original = [{**item, 'evidence_id': 'old-evidence'} for item in original]
    re_registered = [{**item, 'id': f'new-{item["id"]}', 'evidence_id': 'new-evidence'} for item in reversed(original)]
    selected_old, _ = order(original)
    selected_new, _ = order(re_registered)
    physical_old = [x['fields']['path'] for x in selected_old]
    physical_new = [x['fields']['path'] for x in selected_new]
    assert physical_old == physical_new


def test_optional_mixed_source_coordinates_are_orderable():
    rows = [observation(str(i), 'rule', '/same/source') for i in range(3)]
    for row, line in zip(rows, [None, 3, '4']):
        row['fields']['line'] = line
    selected, _ = order(rows)
    assert len(selected) == 3


def test_retained_invocation_is_presented_beside_arbitrary_config_target():
    target='/arbitrary/renamed/agent'
    lead=observation('config','arbitrary-rule','/config/job')
    lead['fields']['referenced_paths']=[target]
    event={'id':'call','type':'linux_cron_call','fields':{'path':'/records/calls',
        'referenced_paths':[{'absolute':target}], 'command':target}}
    noise=[observation(f'n{i}',f'unrelated-{i}') for i in range(20)]
    selected,reasons=order(noise+[lead,event],preferred=['config'])
    assert event in selected[:4]
    assert reasons['call']=='literal_path_context_not_corroboration'
    # This is a retrieval relation, not synthetic corroboration or a verdict.
    assert 'judgment' not in event


def test_related_context_keeps_scope_case_and_full_path_boundaries():
    from workbench.evidence_selection import related_sources
    lead={**observation('seed','rule','/config/job'),'evidence_id':'image'}
    lead['fields'].update(partition_offset=4096,referenced_paths=['/Dir/target'])
    def row(oid,**fields):
        return {'id':oid,'evidence_id':'image','type':'linux_command',
                'fields':{'path':'/logs/'+oid,'partition_offset':4096,
                          'referenced_paths':['/Dir/target'],**fields}}
    wrong_partition=row('partition',partition_offset=8192)
    wrong_case=row('case',referenced_paths=['/dir/target'])
    basename=row('basename',referenced_paths=['/Other/target'])
    wrong_image={**row('image'),'evidence_id':'other'}
    same_origin=row('copy',path='/config/job')
    good=row('good')
    assert related_sources([lead,wrong_partition,wrong_case,basename,wrong_image,same_origin,good],[lead])==[good]


def test_common_destination_does_not_displace_specific_target_context():
    from workbench.evidence_selection import related_sources
    lead=observation('seed','rule','/config/item')
    lead['fields']['referenced_paths']=['/common/destination','/unique/task']
    noise=[{'id':str(i),'type':'linux_command','fields':{'path':f'/logs/{i}',
                'referenced_paths':['/common/destination']}} for i in range(100)]
    good={'id':'good','type':'linux_cron_call','fields':{'path':'/logs/real',
                'referenced_paths':['/unique/task','/common/destination']}}
    assert related_sources([lead]+noise+[good],[lead])==[good]
