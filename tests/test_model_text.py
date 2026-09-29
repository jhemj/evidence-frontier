from copy import deepcopy
import json

import httpx
import pytest

from workbench.model_text import encode,decode,MANIFEST
from workbench.model_tables import encode as tables,decode as untables
from workbench.review_context import model_view_size,serialize
from workbench.model_references import ReferenceProjection
from workbench.provider import Provider


def test_exact_text_runs_preserve_control_unicode_nulls_and_source_objects():
    text='첫 줄\n'+('\x00'*2000)+'e\u0301😀\r\n'+('字'*1000)+'end'
    shaped={'text_runs':['not encoded'], 'unicode_characters':11, 'utf8_sha256':'source'}
    pack={'observations':[{'id':f'o{i}','fields':{'excerpt':text,'ordinal':i,'optional':None,'source':shaped}} for i in range(4)]}
    before=deepcopy(pack);encoded=tables(encode(pack))
    assert decode(untables(encoded))==pack==before
    assert len(serialize(encoded))<len(serialize(pack))//2
    assert model_view_size(pack)==len(serialize(tables(encode(ReferenceProjection(pack).encode(pack)))))


@pytest.mark.parametrize('kind',['text','count','path','duplicate','boolean','length','pack_hash'])
def test_text_transport_tampering_and_malformed_runs_fail_closed(kind):
    encoded=encode({'observations':[{'fields':{'excerpt':'head'+('\x00'*1000)+'tail'}}]})
    item=encoded['observations'][0]['fields']['excerpt']
    if kind=='text':item['text_runs'][0]='tampered'
    if kind=='count':item['text_runs'][1]['repeat']=999
    if kind=='boolean':item['text_runs'][1]['repeat']=True
    if kind=='length':item['unicode_characters']=10**12
    if kind=='path':encoded[MANIFEST]['paths']=[['observations',False,'fields','excerpt']]
    if kind=='duplicate':encoded[MANIFEST]['paths']*=2
    if kind=='pack_hash':encoded[MANIFEST]['input_sha256']='tampered'
    with pytest.raises(ValueError):decode(encoded)


def test_plain_small_and_source_shaped_data_remain_unchanged():
    pack={'plain':'Natural source text\n','source':{'text_runs':[{'repeat':1000,'text':'x'}]}}
    assert encode(pack)==pack and decode(pack)==pack
    with pytest.raises(ValueError):encode({MANIFEST:{}})


def test_provider_uses_same_exact_text_codec_and_records_manifest():
    pack={'target_os':'linux','review_stream':{'phase':'source_page'},
          'observations':[{'id':'o','fields':{'excerpt':'head'+('\x00'*2000)+'tail'}}]}
    p=Provider({'protocol':'ollama','base_url':'http://127.0.0.1:11434','model':'fixture'});p.client.close()
    def response(request):
        data=json.loads(request.content);model_pack=json.loads(data['messages'][1]['content'])['evidence_pack']
        assert 'Lossless text runs' in data['messages'][0]['content']
        assert decode(untables(model_pack))==ReferenceProjection(pack).encode(pack)
        return httpx.Response(200,json={'done_reason':'stop','message':{'content':json.dumps({
            'summary':'Only metadata','findings':[{'dossier_id':'d','title':'Metadata','judgment':'미확인','reason':'Partial'}],
            'source_selections':[]})}})
    p.client=httpx.Client(transport=httpx.MockTransport(response))
    _,receipt=p.generate('review',pack,role='judgment')
    assert receipt['text_projection']['version']=='model-text-runs-1'
