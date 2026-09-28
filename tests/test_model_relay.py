from fastapi.testclient import TestClient
from workbench.model_relay import app, MAX_BODY


def test_relay_rejects_arbitrary_targets_and_model_management():
    client=TestClient(app)
    for path in ('/api/pull','/api/delete','/https://example.com','/api/tags?url=https://example.com'):
        assert client.post(path).status_code==403
    assert client.get('/health').json()=={'status':'ok'}
    assert client.post('/api/chat',content=b'x'*(MAX_BODY+1)).status_code==413
