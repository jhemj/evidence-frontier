from copy import deepcopy
import pytest

from workbench.evidence_semantics import STATIC_CONTENT_TYPES, stage_issues
from workbench.review_validation import errors, check_errors


@pytest.mark.parametrize('kind', sorted(STATIC_CONTENT_TYPES))
def test_static_artifacts_cannot_confirm_an_invocation(kind):
    source={'id':'s','type':kind,'fields':{'path':'/unfamiliar/name','excerpt':'$setting="value";'}}
    stage={'stage':'invocation','judgment':'확인','observation_ids':['s']}
    before=deepcopy((source,stage))
    issues=stage_issues(stage,[source])
    assert issues[0]['code']=='static_content_not_invocation'
    assert issues[0]['source_ids']==['s']
    assert (source,stage)==before
    assert not stage_issues({**stage,'stage':'configuration'},[source])


@pytest.mark.parametrize('kind',['linux_command','linux_cron_call','linux_audit','windows_scriptblock',
                                'linux_tool_result','windows_source_excerpt'])
def test_invocation_with_runtime_or_unclassified_raw_source_needs_semantic_review(kind):
    # Passing this narrow structural guard is not automatic semantic approval.
    static={'id':'s','type':'linux_persistence','fields':{}}
    event={'id':'e','type':kind,'fields':{}}
    stage={'stage':'invocation','judgment':'확인','observation_ids':['s','e']}
    assert not stage_issues(stage,[static,event])


def test_static_invocation_rejection_reaches_dossier_validator_without_repair():
    sources={f's{i}':{'id':f's{i}','type':'linux_persistence','fields':{}} for i in range(3)}
    finding={'dossier_id':'d','judgment':'확인','observation_ids':list(sources),
             'stages':[{'stage':'invocation','judgment':'확인','statement':'variable declarations',
                        'observation_ids':list(sources)}]}
    output={'findings':[finding]};before=deepcopy(output)
    issues=errors(output,['d'],list(sources),{'d':list(sources)},sources)
    assert any(i['code']=='static_content_not_invocation' and i['dossier_id']=='d' for i in issues)
    assert output==before


def test_check_scope_feedback_explains_exact_output_and_comparison_boundary():
    checks=[{'id':'job','status':'covered','observation_ids':['returned','other-dossier'],
             'contracts':[{'dossier_id':'d','contract_id':'c','refutation_condition':'mismatch'}]}]
    output={'check_assessments':[{'check_id':'job','dossier_id':'d','contract_id':'c',
              'outcome':'supports','observation_ids':['returned','baseline','other-dossier']}]}
    before=deepcopy(output)
    issues=check_errors(output,checks,{'d':['returned','baseline']})
    assert issues[0]['code']=='check_citation_scope'
    assert issues[0]['allowed_observation_ids']==['returned']
    assert issues[0]['invalid_observation_ids']==['baseline','other-dossier']
    assert output==before
