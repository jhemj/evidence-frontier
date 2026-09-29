from copy import deepcopy

from workbench.review_context import expand_metadata, fit, serialize


def test_single_dossier_view_is_bounded_and_reversible_for_identity_fields():
    observations=[]
    for index in range(25):
        observations.append({
            'id': f'OBS-{index:04d}', 'type': 'linux_tool_result',
            'timestamp': f'2026-01-01T00:00:{index:02d}Z',
            'source_location': f'RUN/objects/{index:04d}.bin',
            'fields': {
                'path': '/arbitrary/retained',
                'artifact_path': 'RUN/objects/shared.bin',
                'source_sha256': 'a' * 64,
                'time_basis': 'recorded UTC',
                'byte_offset': index * 4096, 'byte_length': 8192,
                'excerpt': ('retained source text ' * 300),
                'display_noise': 'repeated explanatory metadata ' * 80,
            },
            'context_request': {'tool': 'read_source', 'path': '/arbitrary/retained',
                                'byte_offset': index * 4096, 'byte_length': 8192},
        })
    checks=[{'id': f'JOB-{i}', 'request': {'tool': 'read_file', 'path': '/arbitrary/retained',
             'byte_offset': i, 'reason': 'explanatory request ' * 20},
             'contracts':[{'dossier_id':'DOSSIER-1','contract_id':f'C-{i}',
                           'success_condition':'success condition ' * 20,
                           'refutation_condition':'refutation condition ' * 20,
                           'inconclusive_condition':'inconclusive condition ' * 20}],
             'status':'partial','scope':{'status':'partial','detail':'x'*500},
             'observation_ids':[f'OBS-{i:04d}']} for i in range(20)]
    pack={'observations':observations,'executed_checks':checks,
          'literal_fact_candidates':[{'observation_id':'OBS-0000','pointer':'/fields/path',
                                      'operator':'equals','value':'/arbitrary/retained'}],
          'allowed_observation_ids':[o['id'] for o in observations],
          'selection_audit':{'included':25,'omitted':0}}
    original=deepcopy(pack)
    fit(pack)
    assert len(serialize(pack)) <= 36000
    assert pack.get('shared_observation_field_defaults')
    expand_metadata(pack)
    assert [o['id'] for o in pack['observations']] == [o['id'] for o in original['observations']]
    for before, after in zip(original['observations'], pack['observations']):
        assert after['fields']['path'] == before['fields']['path']
        assert after['fields']['artifact_path'] == before['fields']['artifact_path']
        assert after['fields']['source_sha256'] == before['fields']['source_sha256']
        assert after['fields']['byte_offset'] == before['fields']['byte_offset']
        assert after['context_request'] == before['context_request']


def test_fit_is_idempotent_after_dictionary_projection():
    pack={'observations':[{'id':'o','type':'linux_command','fields':{
        'path':'/arbitrary/file','artifact_path':'RUN/objects/a.bin',
        'source_sha256':'a'*64,'display_noise':'noise '*500}}],
          'selection_audit':{'included':1,'omitted':0}}
    fit(pack)
    first=serialize(pack)
    fit(pack)
    assert serialize(pack) == first
