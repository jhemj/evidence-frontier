import hmac
import json
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse,HTMLResponse,JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .controller import Controller,ROOT
from .store import Store
from .models import NewCase,EvidenceRequest,MessageRequest,ProviderConfig,Decision
from .provider import Provider,validate_url
from .reporting import build_report,preview_document,render
from .evidence_access import catalog,worker_request
from .remote_jobs import RemoteRequest,submit as remote_submit,refresh as remote_refresh


def create_app(data_root=None,evidence_root=None,start_worker=True):
    data=Path(data_root or os.getenv('DATA_ROOT',ROOT/'data'))
    evidence=Path(evidence_root or os.getenv('EVIDENCE_ROOT',ROOT/'examples'))
    reports=data/'reports'
    store=Store(data/'case.sqlite3');controller=Controller(store,evidence)
    @asynccontextmanager
    async def lifespan(app):
        controller.recover()
        thread=None
        if start_worker:
            thread=threading.Thread(target=controller.loop,daemon=True);thread.start()
        yield
        controller.stop.set();controller.wake.set()
        if thread:thread.join(timeout=2)

    app=FastAPI(title='Evidence Frontier',version='0.1.0',lifespan=lifespan)
    app.state.controller=controller
    app.add_middleware(TrustedHostMiddleware,allowed_hosts=['localhost','127.0.0.1','[::1]','testserver','workbench']+os.getenv('ALLOWED_HOSTS','').split(','))

    @app.middleware('http')
    async def protect(request:Request,call_next):
        if request.url.path.startswith('/api'):
            origin=request.headers.get('origin')
            if origin and urlsplit(origin).netloc!=request.headers.get('host'):
                return JSONResponse({'detail':'다른 사이트의 요청은 허용하지 않습니다.'},status_code=403)
            token=os.getenv('WORKBENCH_TOKEN')
            if token and not hmac.compare_digest(request.headers.get('X-Workbench-Token',''),token):
                return JSONResponse({'detail':'접속 암호가 필요합니다.'},status_code=401)
            if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('X-Requested-With')!='frontier':
                return JSONResponse({'detail':'요청 출처가 확인되지 않았습니다.'},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-src 'self' blob:; connect-src 'self'; base-uri 'none'; frame-ancestors 'self'"
        response.headers['Cache-Control']='no-store' if request.url.path.startswith('/api') else 'no-cache'
        return response

    @app.exception_handler(ValueError)
    async def invalid(request,exc):return JSONResponse({'detail':str(exc)},status_code=400)

    @app.get('/health')
    def health():return {'status':'ok','version':'0.1.0'}

    @app.get('/api/cases')
    def cases():return store.list('case')[::-1]

    @app.post('/api/cases')
    def new_case(body:NewCase):return controller.create(**body.model_dump())

    from uuid import uuid4
    view_version=uuid4().hex

    @app.get('/api/cases/{case_id}')
    def snapshot(case_id:str,since:str=''):
        revision=view_version+':'+str(store.ui_revision(case_id))
        if since==revision:
            case=store.get(case_id,'case')
            result={'unchanged':True,'view_revision':revision,'case':case,
                    'task':store.list('task',case_id),'evidence':store.list('evidence',case_id)}
        else:
            result=controller.snapshot(case_id)
            result['view_revision']=revision
        if not result.get('unchanged'):
            trim_snapshot(result)
        if os.getenv('WORKER_URL'):
            for task in result['task']:
                if task['status']=='queued' and task.get('started_at') and result['case']['status'] in ('running','pause_requested'):
                    task['status']='running'
                if task['status']=='running' and task['action'] not in ('linux_investigate','ai_judgment','investigation_report'):
                    source=next(e for e in result['evidence'] if e['id']==task['evidence_id'])
                    try:task['progress']=worker_request('GET','/progress',params={'path':source['path']},timeout=2)
                    except Exception:pass
        return result

    def trim_snapshot(result):
        # Keep large event streams out of polling. Search endpoint returns individual pages.
        result['observation_count']=len(result['observation']);result['observation']=result['observation'][:200]
        result['receipt']=result['receipt'][-30:];result['audit']=result['audit'][-50:]
        result['lineage_count']=len(result['lineage']);result['lineage']=result['lineage'][-100:]
        # The full worker result is immutable in the ledger/report. Re-sending
        # tens of thousands of nested observations on every poll stalls the UI.
        result['receipt']=[{**r,'result':{**r['result'],'observation_count':len(r['result']['observations']),
            'observations':[],'observations_omitted_from_polling':True}} if isinstance(r.get('result'),dict) and 'observations' in r['result'] else r
            for r in result['receipt']]
    @app.get('/api/evidence-files')
    def files():
        return catalog(evidence)

    @app.post('/api/cases/{case_id}/evidence')
    def register(case_id:str,body:EvidenceRequest):return controller.register(case_id,**body.model_dump())

    @app.post('/api/cases/{case_id}/evidence/{evidence_id}/disconnect')
    def disconnect(case_id:str,evidence_id:str):return controller.connection(case_id,evidence_id,False)

    @app.post('/api/cases/{case_id}/evidence/{evidence_id}/reconnect')
    def reconnect(case_id:str,evidence_id:str):return controller.connection(case_id,evidence_id,True)

    @app.post('/api/cases/{case_id}/start')
    def start(case_id:str):return controller.start(case_id)

    @app.post('/api/cases/{case_id}/pause')
    def pause(case_id:str):return controller.pause(case_id)

    @app.post('/api/tasks/{task_id}/retry')
    def retry(task_id:str):controller.retry(task_id);return {'ok':True}

    @app.post('/api/tasks/{task_id}/retry-failed-dossiers')
    def retry_failed_dossiers(task_id:str):
        controller.retry(task_id,failed_dossiers_only=True);return {'ok':True}

    @app.post('/api/cases/{case_id}/openrelik')
    def submit_remote(case_id:str,body:RemoteRequest):
        try:
            with store.lock:return remote_submit(store,case_id,body)
        except Exception as e:raise ValueError(f'OpenRelik 제출을 완료하지 못했습니다: {e}')

    @app.get('/api/cases/{case_id}/openrelik')
    def list_remote(case_id:str):
        store.get(case_id,'case');return store.list('remote_job',case_id)

    @app.post('/api/openrelik/{job_id}/refresh')
    def refresh_remote(job_id:str):
        try:return remote_refresh(store,job_id)
        except Exception as e:raise ValueError(f'OpenRelik 상태를 확인하지 못했습니다: {e}')

    def config():
        configs=store.list('config')
        return configs[-1]['provider'] if configs else ProviderConfig().model_dump()

    @app.get('/api/settings')
    def settings():return config()

    @app.put('/api/settings')
    def settings_save(body:ProviderConfig):
        validate_url(body.base_url,body.trusted_lan)
        store.add('config','',provider=body.model_dump());return body

    @app.post('/api/settings/probe')
    def probe(body:ProviderConfig):
        try:return {'models':Provider(body.model_dump()).models()}
        except Exception as e:raise ValueError(f'모델 연결을 확인하지 못했습니다: {e}')

    @app.post('/api/cases/{case_id}/messages')
    def message(case_id:str,body:MessageRequest):
        try:return controller.analyze(case_id,body.message,config())
        except Exception as e:raise ValueError(f'AI 응답을 완료하지 못했습니다: {e}')

    @app.post('/api/claims/{claim_id}/falsify')
    def falsify(claim_id:str):
        try:return controller.falsify(claim_id,config())
        except Exception as e:raise ValueError(f'반증 검토를 완료하지 못했습니다: {e}')

    @app.post('/api/claims/{claim_id}/decision')
    def decision(claim_id:str,body:Decision):return controller.decide(claim_id,**body.model_dump())

    @app.get('/api/cases/{case_id}/observations')
    def observations(case_id:str,q:str='',offset:int=0):
        controller.store.get(case_id,'case')
        items=store.list('observation',case_id)
        if q:items=[i for i in items if q.casefold() in json.dumps(i,ensure_ascii=False).casefold()]
        return {'total':len(items),'items':items[max(0,offset):max(0,offset)+100]}

    @app.get('/api/observations/{observation_id}/source')
    def source(observation_id:str):
        from .investigation_export import located,digest_file
        observation=store.get(observation_id,'observation')
        if observation['evidence_id'] not in controller.active_ids(observation['case_id']):raise ValueError('연결 해제된 증거입니다.')
        fields=observation['fields']
        if not fields.get('artifact_path') or not fields.get('source_sha256'):raise ValueError('이 관측에는 추출 원문이 없습니다.')
        path=located(Path(os.getenv('ANALYSIS_ROOT','/analysis')),fields['artifact_path'])
        if digest_file(path)!=fields['source_sha256']:raise ValueError('추출 원문 해시 불일치')
        return FileResponse(path,filename=observation_id+'.bin',media_type='application/octet-stream')

    @app.get('/api/cases/{case_id}/report-preview',response_class=HTMLResponse)
    def preview(case_id:str):return render(preview_document(controller,case_id))

    @app.post('/api/cases/{case_id}/reports')
    def create_report(case_id:str):
        return build_report(controller,case_id,reports)

    @app.get('/api/reports/{record_id}/download')
    def download(record_id:str):
        report=store.get(record_id,'report')
        return FileResponse(reports/report['report_id']/'report.zip',filename=report['report_id']+'.zip')

    app.mount('/',StaticFiles(directory=ROOT/'dist',html=True),name='ui')
    return app


app=create_app()
