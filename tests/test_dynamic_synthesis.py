from workbench.investigation import evidence_pack


class S:
    def __init__(self, rows): self.rows = rows
    def list(self, kind, case_id=None): return self.rows if kind == 'hypothesis' else []
    def get(self, case_id): return {'target_os': 'linux'}


class C:
    def __init__(self):
        self.store = S([])
    def active_observations(self, case_id): return []


def test_dynamic_pack_has_current_card_and_omission_disclosure():
    c = C()
    c.store.rows = [{'id': f'r{i}', 'evidence_id': 'e', 'contract': 'dynamic-v1', 'hypothesis_kind': 'dynamic',
                     'hypothesis_card_id': f'card{i}', 'task_id': 't', 'generation': 2, 'revision': 1,
                     'title': f'Title {i}', 'text': f'Text {i}', 'observation_ids': [], 'status': 'open'} for i in range(14)]
    pack = evidence_pack(c, 'case', evidence_id='e', task_id='t', generation=2)
    assert len(pack['dynamic_hypotheses']) == 12
    assert pack['dynamic_hypotheses_total'] == 14 and pack['dynamic_hypotheses_omitted'] == 2
    assert pack['dynamic_hypotheses'][0]['hypothesis_id'] == 'card0'
