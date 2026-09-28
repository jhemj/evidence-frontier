import httpx
from fastapi.testclient import TestClient
from workbench import gateway


def test_gateway_keeps_fixed_authority_and_streams_response(monkeypatch):
    seen=[]
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'first';yield b'second'
    def transport(request):
        seen.append(request)
        return httpx.Response(200,stream=Body(),headers={'Content-Security-Policy':"default-src 'self'"})
    original=httpx.AsyncClient
    monkeypatch.setattr(gateway.httpx,'AsyncClient',lambda **kw:original(**kw,transport=httpx.MockTransport(transport)))
    with TestClient(gateway.app) as client:
        result=client.get('/api/report?url=https://untrusted.invalid',headers={'X-Workbench-Token':'fixture'})
    assert result.content==b'firstsecond' and result.headers['content-security-policy']=="default-src 'self'"
    assert str(seen[0].url).startswith('http://workbench:8765/api/report?')
    assert seen[0].headers['x-workbench-token']=='fixture'


def test_gateway_rejects_oversized_mutation():
    with TestClient(gateway.app) as client:
        assert client.post('/api/cases',content=b'x'*(1024*1024+1)).status_code==413


def test_gateway_serves_only_public_ui_assets():
    with TestClient(gateway.app) as client:
        response=client.get('/')
        assert 'investigation-progress' in response.text
        assert "script-src 'self'" in response.headers['content-security-policy']
        assert client.get('/progress.js').status_code==200
        assert 'workbench/gateway.py' not in gateway.UI_FILES
