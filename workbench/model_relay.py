"""Fixed local-model relay; no generic URL proxy and no evidence volume access."""
import os
import httpx
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
from .provider import validate_url

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
ALLOWED={('GET','api/tags'),('GET','models'),('POST','api/chat'),('POST','chat/completions')}
MAX_BODY=1024*1024


@app.get('/health')
def health():return {'status':'ok'}


@app.api_route('/{path:path}',methods=['GET','POST','PUT','DELETE','PATCH'])
async def relay(path:str,request:Request):
    if (request.method,path) not in ALLOWED or request.url.query:raise HTTPException(403,'Model operation not allowed')
    body=bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body)>MAX_BODY:raise HTTPException(413,'Model input limit')
    upstream=validate_url(os.getenv('MODEL_UPSTREAM_URL','http://host.docker.internal:11434'),os.getenv('MODEL_UPSTREAM_TRUSTED_LAN')=='1')
    headers={'Content-Type':'application/json'}
    if request.headers.get('authorization'):headers['Authorization']=request.headers['authorization']
    try:
        async with httpx.AsyncClient(timeout=610,follow_redirects=False,trust_env=False) as client:
            async with client.stream(request.method,upstream+'/'+path,content=bytes(body),headers=headers) as response:
                content=bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content)>4*MAX_BODY:raise HTTPException(502,'Model output limit')
                return Response(content=bytes(content),status_code=response.status_code,media_type='application/json')
    except httpx.TransportError:raise HTTPException(502,'Configured local model unavailable')
