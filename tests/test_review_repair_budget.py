from copy import deepcopy
import json

from test_review_stream import fixture, result
from workbench.dossiers import finish
from workbench.review_context import model_view_size
from workbench.review_diagnostics import repair_feedback, FEEDBACK_LIMIT


def test_feedback_envelope_is_bounded_without_modifying_full_errors():
    issues=[{'code':'unknown_observation','ids':['invalid-'+str(i) for i in range(3000)]}]
    before=deepcopy(issues)
    feedback=repair_feedback(ValueError('bad response'),issues,'diagnostic')
    assert len(json.dumps(feedback,ensure_ascii=False,separators=(',',':')))<=FEEDBACK_LIMIT
    assert feedback['errors']==[{'code':'unknown_observation'}]
    assert feedback['diagnostic_id']=='diagnostic'
    assert 'detail_scope' in feedback
    assert issues==before


def test_large_source_page_can_receive_schema_feedback_and_retry(tmp_path,monkeypatch):
    from workbench.provider import ModelOutputError
    c,cid,e,t,d,b,sources=fixture(tmp_path,1,excerpt='full-source '*10000)
    calls=[]
    def model(config,question,pack,**kwargs):
        calls.append(deepcopy(pack))
        assert model_view_size(pack)<=36000
        if len(calls)==1:
            raise ModelOutputError('Invalid schema fields: '+'x'*1900,'{}','output_schema')
        return result(pack),{}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    finish(c,cid,e,t);finish(c,cid,e,t)
    assert len(calls)==2
    assert 'validation_feedback' in calls[1]
    assert calls[0]['observations']==calls[1]['observations']
    assert c.store.get(b['id'])['status']=='pending'
    assert any(page['status']=='reviewed' for page in c.store.list('review_page',cid))


def test_measured_context_pressure_repages_smaller_without_adopting_output(tmp_path,monkeypatch):
    from workbench.provider import ModelOutputError
    c,cid,e,t,d,b,sources=fixture(tmp_path,3,excerpt='source '*7000)
    before=deepcopy(sources);calls=[]
    def model(config,question,pack,**kwargs):
        calls.append(deepcopy(pack))
        if len(calls)==1:
            raise ModelOutputError('measured pressure','{}','input_context_pressure',
                {'prompt_budget':{'exact_input_tokens':32000,'context_tokens_requested':32768,'output_tokens_reserved':4000}})
        return result(pack),{}
    monkeypatch.setattr('workbench.dossiers.consult',model)
    finish(c,cid,e,t)
    changed=c.store.get(b['id'])
    assert 0<changed['review_input_maximum']<36000
    assert changed['attempts']==0 and changed['review_stream_id'] is None
    assert c.store.list('review_stream',cid)[0]['status']=='superseded'
    assert not c.store.get(d['id']).get('finding')
    finish(c,cid,e,t)
    assert len(calls)==2 and model_view_size(calls[1])<model_view_size(calls[0])
    assert [c.store.get(o['id']) for o in sources]==before
