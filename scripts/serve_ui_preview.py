"""Serve a versioned UI against a running loopback API without touching its runtime.

No controller, database, evidence root, or model is loaded here. The upstream
retains all authentication and mutation checks; the browser's Host/Origin and
token headers are preserved. This is a local development/test frontend only.
"""
import argparse
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.middleware.trustedhost import TrustedHostMiddleware

HOP_HEADERS = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
               'te', 'trailer', 'transfer-encoding', 'upgrade'}
CSP = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data:; frame-src 'self' blob:; connect-src 'self'; "
       "base-uri 'none'; frame-ancestors 'self'")


def headers_for_forwarding(headers):
    blocked = HOP_HEADERS | {x.strip().lower() for x in headers.get('connection', '').split(',')}
    return {k: v for k, v in headers.items() if k.lower() not in blocked}


def create_app(static_root, upstream, *, transport=None):
    url = urlsplit(upstream)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
            or not url.port or url.username or url.password or url.path not in ('', '/')
            or url.query or url.fragment):
        raise ValueError('Upstream must be an explicit loopback HTTP origin with a port.')
    origin = upstream.rstrip('/')

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(timeout=90, trust_env=False, follow_redirects=False,
                                     transport=transport) as client:
            app.state.client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])

    @app.middleware('http')
    async def security(request, call_next):
        if request.url.path.startswith('/api'):
            request_origin = request.headers.get('origin')
            if request_origin and urlsplit(request_origin).netloc != request.headers.get('host'):
                return JSONResponse({'detail': '다른 사이트의 요청은 허용하지 않습니다.'}, status_code=403)
        response = await call_next(request)
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = CSP
        response.headers['Cache-Control'] = 'no-store' if request.url.path.startswith('/api') else 'no-cache'
        return response

    @app.api_route('/api/{path:path}', methods=['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
    async def proxy(path: str, request: Request):
        # Preserve the encoded path/query and bind to the configured origin.
        raw_path = request.scope['raw_path'].decode('ascii')
        query = request.scope['query_string'].decode('ascii')
        target = origin + raw_path + ('?' + query if query else '')
        outgoing = app.state.client.build_request(request.method, target,
            headers=headers_for_forwarding(request.headers), content=await request.body())
        try:
            response = await app.state.client.send(outgoing, stream=True)
        except httpx.HTTPError:
            return JSONResponse({'detail': '분석 서버에 연결하지 못했습니다. 잠시 후 다시 시도하세요.'}, status_code=502)
        return StreamingResponse(response.aiter_raw(), status_code=response.status_code,
            headers=headers_for_forwarding(response.headers), background=BackgroundTask(response.aclose))

    app.mount('/', StaticFiles(directory=Path(static_root), html=True), name='ui')
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--static-root', type=Path, required=True)
    parser.add_argument('--upstream', required=True)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    import uvicorn
    uvicorn.run(create_app(args.static_root, args.upstream), host='127.0.0.1', port=args.port)
