from pydantic import ValidationError
import pytest
from workbench.models import JudgmentFinding
from workbench.procedures import instructions, identity
from test_investigator_strategy import provider


def test_reader_brief_is_compatible_with_history_but_size_bounded():
    fields={'title':'대상 경로의 예약 호출 기록','judgment':'미확인','reason':'실행 결과 미확인'}
    assert JudgmentFinding(**fields).card_summary==''
    assert JudgmentFinding(**fields,card_summary='등록과 호출은 확인됐지만 실행 결과는 미확인입니다.').card_summary
    with pytest.raises(ValidationError):JudgmentFinding(**fields,card_summary='가'*281)


def test_actual_provider_schema_and_playbook_request_self_contained_cards():
    fields={'dossier_id':'d','title':'대상 경로의 예약 호출 기록','judgment':'미확인','reason':'실행 결과 미확인',
        'card_summary':'예약 설정과 호출 기록에 같은 대상 경로가 나타납니다. 실제 실행 성공 여부는 확인하지 못했습니다.'}
    p,requests=provider(content={'summary':'검토','findings':[fields]})
    result,receipt=p.generate('검토',{'target_os':'linux','observations':[]},role='judgment')
    assert result['findings'][0]['card_summary']==fields['card_summary']
    assert 'Skill Reader-facing evidence cards' in requests[-1][1]['messages'][0]['content']
    assert receipt['procedures']==identity()
    assert 'Skill Reader-facing evidence cards' in instructions('windows')


def test_falsifier_output_contract_does_not_inherit_card_or_planner_fields():
    result={'alternatives':['승인된 작업일 가능성'],'contradicting_observation_ids':[],'missing_checks':['승인 기록 미제공']}
    p,requests=provider(content=result)
    output,_=p.generate('경쟁 설명 검토',{'target_os':'linux','observations':[]},role='falsifier')
    prompt=requests[-1][1]['messages'][0]['content']
    assert output==result and 'ONLY output contract is Falsification' in prompt
    assert 'Evidence-driven hypothesis evolution' not in prompt
    assert 'Skill Reader-facing evidence cards' not in prompt
    assert '"alternatives":[],"contradicting_observation_ids":[],"missing_checks":[]' in prompt
    assert 'Test the EXACT stated claim' in prompt
    assert 'are NOT contradicting IDs' in prompt
    assert 'arrays of strings, never objects' in prompt


def test_candidate_procedure_preserves_event_counts_and_inferred_dates():
    procedure=instructions('linux',role='investigator')
    assert 'Multiple observation IDs or copies do not mean multiple actions' in procedure
    assert 'Preserve inferred years' in procedure
    assert "rotated file's date is not an observed last event" in procedure


def test_wrong_hypothesis_shaped_falsifier_response_remains_rejected():
    from workbench.provider import ModelOutputError
    p,_=provider(content={'hypothesis_id':'h','hypothesis_number':1,'status':'confirmed','evidence':[],'next_checks':[]})
    with pytest.raises((ModelOutputError,ValueError)):
        p.generate('경쟁 설명 검토',{'target_os':'linux','observations':[]},role='falsifier')
