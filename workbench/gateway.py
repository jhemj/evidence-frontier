"""Fixed UI ingress for Docker hosts that cannot publish internal-only networks."""
import httpx
from pathlib import Path
from fastapi import FastAPI,Request,HTTPException
from fastapi.responses import StreamingResponse,FileResponse
from starlette.background import BackgroundTask

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
UPSTREAM='http://workbench:8765'
UI_ROOT=Path(__file__).resolve().parents[1]/'dist'
UI_FILES={'':'index.html','index.html':'index.html',**{name:name for name in
    ('app.js','style.css','timeline.js','timeline.css','progress.js','progress.css')}}

@app.get('/health')
def health():return {'status':'ok'}

@app.api_route('/{path:path}',methods=['GET','HEAD','POST','PUT','DELETE','PATCH','OPTIONS'])
async def forward(path:str,request:Request):
    # UI assets can be released independently of a running forensic worker.
    # Only this fixed public asset list is served; all API traffic stays proxied.
    if request.method in ('GET','HEAD') and path in UI_FILES:
        return FileResponse(UI_ROOT/UI_FILES[path],headers={
            'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
            'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'"})
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>1024*1024:raise HTTPException(413,'Request input limit')
    headers={k:v for k,v in request.headers.items() if k.lower() in
        ('host','origin','content-type','x-requested-with','x-workbench-token','range','if-none-match','if-modified-since')}
    client=httpx.AsyncClient(timeout=httpx.Timeout(610,connect=5),trust_env=False,follow_redirects=False)
    try:
        # Assign path and query fields separately; request text can never change the authority.
        url=httpx.URL(UPSTREAM).copy_with(path='/'+path,query=request.url.query.encode())
        upstream=await client.send(client.build_request(request.method,url,headers=headers,content=bytes(body)),stream=True)
    except httpx.TransportError:
        await client.aclose();raise HTTPException(502,'Workbench unavailable')
    async def close():
        await upstream.aclose();await client.aclose()
    response_headers={k:v for k,v in upstream.headers.items() if k.lower() not in
        ('connection','transfer-encoding','keep-alive','proxy-authenticate','proxy-authorization','te','trailer','upgrade')}
    return StreamingResponse(upstream.aiter_raw(),status_code=upstream.status_code,headers=response_headers,background=BackgroundTask(close))
