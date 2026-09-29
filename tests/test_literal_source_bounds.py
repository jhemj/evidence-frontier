import pytest

from workbench.semantic_contract import assertion_errors, scalar


def check(actual, expected='literal', operator='contains', pointer='/fields/excerpt', cited=True, permitted=True):
    obs={'o':{'fields':{'excerpt':actual}}}
    finding={'observation_ids':['o'] if cited else [], 'fact_assertions':[
        {'observation_id':'o','pointer':pointer,'operator':operator,'value':expected}]}
    return assertion_errors(finding,obs,['o'] if permitted else [])


def test_short_exact_literal_in_long_presented_text_is_valid():
    assert not check('x'*8192+'literal'+'y'*8192)
    assert not check('원문'*4096+'literal')
    with pytest.raises(ValueError):scalar({'fields':{'excerpt':'x'*8192}},'/fields/excerpt')


@pytest.mark.parametrize('actual,expected',[
    ('x'*8192,'literal'), ('literal','Literal'), ('literal',''),
    ('x'*8192,'x'*4097), ({'excerpt':'literal'},'literal'), (['literal'],'literal'),
    (True,'True'), ('literal',None), ('literal',1)])
def test_contains_still_requires_bounded_exact_text_scalar(actual,expected):
    assert check(actual,expected)


@pytest.mark.parametrize('kwargs',[
    {'pointer':'/fields/missing'}, {'pointer':'/fields/excerpt/~2'},
    {'pointer':'/fields'}, {'cited':False}, {'permitted':False}])
def test_long_source_does_not_bypass_pointer_or_citation_scope(kwargs):
    assert check('literal'+'x'*8192,**kwargs)


def test_only_presented_fragment_may_support_literal():
    assert check('selected text only','hidden text elsewhere')
    assert check('x'*8192,'x'*8192,operator='equals')
    assert check(True,1,operator='equals')
    assert not check(True,True,operator='equals')


@pytest.mark.parametrize('whole',['prefix literal suffix','x'*8192+'literal','문맥\x00literal\x00끝'])
def test_selected_fragment_equality_cannot_become_whole_field_equality(whole):
    from copy import deepcopy
    from workbench.review_validation import errors
    shown={'o':{'id':'o','fields':{'excerpt':'literal'}}}
    canonical={'o':{'id':'o','fields':{'excerpt':whole}}}
    output={'findings':[{'dossier_id':'d','judgment':'미확인','observation_ids':['o'],
        'fact_assertions':[{'observation_id':'o','pointer':'/fields/excerpt','operator':'equals','value':'literal'}]}]}
    before=deepcopy(output)
    issues=errors(output,['d'],['o'],{'d':['o']},shown,canonical_observations=canonical)
    assert issues and issues[0]['scope']=='canonical_source'
    assert output==before  # never silently convert equals into contains
    output['findings'][0]['fact_assertions'][0]['operator']='contains'
    assert not errors(output,['d'],['o'],{'d':['o']},shown,canonical_observations=canonical)
    assert not assertion_errors(output['findings'][0],canonical,{'o'})  # report parity


def test_canonical_check_cannot_authorize_unpresented_or_foreign_literal():
    from workbench.review_validation import errors
    shown={'o':{'fields':{'excerpt':'visible'}},'foreign':{'fields':{'excerpt':'secret'}}}
    canonical={'o':{'fields':{'excerpt':'visible hidden'}},'foreign':{'fields':{'excerpt':'secret'}}}
    f={'dossier_id':'d','judgment':'미확인','observation_ids':['o'],
       'fact_assertions':[{'observation_id':'o','pointer':'/fields/excerpt','operator':'contains','value':'hidden'}]}
    assert errors({'findings':[f]},['d'],['o'],{'d':['o']},shown,canonical_observations=canonical)
    f['fact_assertions'][0].update(observation_id='foreign',value='secret')
    assert errors({'findings':[f]},['d'],['o'],{'d':['o']},shown,canonical_observations=canonical)


def test_unchanged_scalar_equality_still_valid_in_both_views():
    from workbench.review_validation import errors
    source={'o':{'fields':{'path':'/synthetic/file'}}}
    f={'dossier_id':'d','judgment':'미확인','observation_ids':['o'],
       'fact_assertions':[{'observation_id':'o','pointer':'/fields/path','operator':'equals','value':'/synthetic/file'}]}
    assert not errors({'findings':[f]},['d'],['o'],{'d':['o']},source,canonical_observations=source)
