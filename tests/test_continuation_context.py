from workbench.review_context import bounded
from workbench.review_context import fit, serialize
from copy import deepcopy


def test_bounded_context_preserves_continuation_request_verbatim():
    continuation = {
        'tool': 'search', 'query': 'needle', 'path': '/a/' + 'x' * 300,
        'cursor': 'signed-cursor-' + 'z' * 300, 'limit': 8,
        'partition_offset': 4096, 'inode': 17,
    }
    scope = {'status': 'partial', 'continuation_request': continuation,
             'nested': {'long': 'y' * 500}}
    compact = bounded(scope, text=20, items=2)
    assert compact['continuation_request'] == continuation
    assert len(compact['nested']['long']) == 20


def test_bounded_context_does_not_invent_continuation_when_absent():
    assert 'continuation_request' not in bounded({'status': 'covered'}, text=20, items=2)


def test_fit_preserves_whole_continuations_or_omits_history_entry():
    request={'tool':'search','query':'literal','path':'/long/'+'x'*400,'cursor':'c'*500,'limit':8}
    history=[{'id':f'j{i}','request':{'tool':'search','query':'literal'},
              'result_scope':{'continuation_request':deepcopy(request),'detail':'y'*1000}} for i in range(12)]
    pack={'observations':[{'id':'o','fields':{'excerpt':'source','path':'/original'}}],
          'completed_tools':deepcopy(history),'completed_tools_total':12,'completed_tools_omitted':0}
    fit(pack,maximum=6000)
    assert len(serialize(pack))<=6000
    assert len(pack['completed_tools'])+pack['completed_tools_omitted']==12
    assert pack['completed_tools_omitted']>0
    for row in pack['completed_tools']:
        assert row['result_scope']['continuation_request']==request
    assert pack['observations'][0]['id']=='o'
