import httpx
import pytest
from fastapi.testclient import TestClient
from scripts.serve_ui_preview import create_app


@pytest.mark.parametrize('url', ['https://127.0.0.1:8000', 'http://example.org:80',
    'http://user:pass@localhost:80', 'http://localhost:80/other', 'http://localhost:80?q=1',
    'http://localhost', 'http://localhost:80#x'])
def test_preview_cannot_proxy_to_remote_or_ambiguous_target(tmp_path, url):
    with pytest.raises(ValueError):
        create_app(tmp_path, url)


def test_preview_preserves_upstream_auth_origin_method_and_response(tmp_path):
    seen = []
    def upstream(request):
        seen.append(request)
        return httpx.Response(401, headers={'X-Report-Scope-Changed': 'true'},
                              stream=httpx.ByteStream(b'{"detail":"auth required"}'))
    app = create_app(tmp_path, 'http://127.0.0.1:8813', transport=httpx.MockTransport(upstream))
    with TestClient(app) as client:
        response = client.post('/api/cases/a/start?q=foo%20bar', json={'x': 1}, headers={
            'Origin': 'http://testserver', 'X-Requested-With': 'frontier', 'X-Workbench-Token': 'test-token'})
        assert response.status_code == 401 and response.json()['detail'] == 'auth required'
        assert response.headers['X-Report-Scope-Changed'] == 'true'
        assert response.headers['Cache-Control'] == 'no-store'
        assert seen[0].method == 'POST' and seen[0].url.host == '127.0.0.1'
        assert seen[0].url.query == b'q=foo%20bar'
        assert seen[0].headers['host'] == 'testserver'
        assert seen[0].headers['origin'] == 'http://testserver'
        assert seen[0].headers['X-Workbench-Token'] == 'test-token'
        assert seen[0].headers['X-Requested-With'] == 'frontier'
        assert b'"x":1' in seen[0].content
        assert client.post('/api/cases/a/start', headers={'Origin': 'http://untrusted.test'}).status_code == 403
        assert client.get('/api/cases', headers={'Host': 'untrusted.test'}).status_code == 400
        assert len(seen) == 1


def test_preview_serves_only_static_files_and_reports_upstream_outage(tmp_path):
    (tmp_path / 'index.html').write_text('<h1>Frontend only</h1>')
    def unavailable(request):
        raise httpx.ConnectError('offline', request=request)
    app = create_app(tmp_path, 'http://127.0.0.1:8813', transport=httpx.MockTransport(unavailable))
    with TestClient(app) as client:
        response = client.get('/')
        assert response.status_code == 200 and 'Frontend only' in response.text
        assert "frame-ancestors 'self'" in response.headers['Content-Security-Policy']
        assert client.get('/case.sqlite3').status_code == 404
        assert client.get('/api/cases').status_code == 502
        assert client.post('/index.html').status_code == 405
