import hmac
import os
import threading
import time
from datetime import datetime,timezone
from fastapi import FastAPI,Header,HTTPException,Depends
from .evidence_access import metadata,catalog
from .models import WorkerRequest,InvestigationToolRequest,WorkerJobRequest
from .worker import execute

app=FastAPI(docs_url=None,redoc_url=None)
progress_lock=threading.Lock()
progress_records={}
job_manager=None
from .runtime_contract import code_identity
runtime_code=code_identity()

def auth(x_worker_token:str=Header(default='')):
    secret=os.getenv('WORKER_TOKEN','')
    if not secret or not hmac.compare_digest(secret,x_worker_token):raise HTTPException(403)

def jobs():
    global job_manager
    with progress_lock:
        if job_manager is None:
            from .worker_jobs import WorkerJobs
            job_manager=WorkerJobs(os.getenv('ANALYSIS_ROOT','/analysis'),os.getenv('EVIDENCE_ROOT','/evidence'))
        return job_manager

@app.post('/jobs',dependencies=[Depends(auth)])
def submit_job(body:WorkerJobRequest):return jobs().submit(body)

@app.get('/jobs/{job_id}',dependencies=[Depends(auth)])
def job_status(job_id:str):
    import re
    if not re.fullmatch(r'[a-f0-9]{64}',job_id):raise HTTPException(400)
    try:return jobs().status(job_id)
    except ValueError as ex:raise HTTPException(404, str(ex))

@app.post('/execute',dependencies=[Depends(auth)])
def run(body:WorkerRequest):
    started=datetime.now(timezone.utc).isoformat();last=[0]
    with progress_lock:
        if len(progress_records)>100:progress_records.clear()
        progress_records[body.path]={'action':body.action,'stage':body.action,'started_at':started}
    def progress(**fields):
        if fields.get('stage')=='segment_hash' and time.monotonic()-last[0]<1:return
        last[0]=time.monotonic()
        with progress_lock:progress_records[body.path]={'action':body.action,'started_at':started,**fields}
    try:return execute(os.getenv('EVIDENCE_ROOT','/evidence'),body.action,body.path,progress)
    finally:
        with progress_lock:progress_records.pop(body.path,None)

@app.get('/progress',dependencies=[Depends(auth)])
def progress(path:str):
    with progress_lock:record=progress_records.get(path)
    if not record and job_manager:
        with job_manager.lock:
            rows=job_manager.db.execute("SELECT request FROM jobs WHERE status='running'").fetchall()
        import json
        for row in rows:
            request=json.loads(row[0])
            if request['path']==path:record={'action':request['action'],'stage':request['action']}
    if record and record['action']=='linux_scan':
        try:
            import json
            from pathlib import Path
            detail=json.loads((Path(os.getenv('ANALYSIS_ROOT','/analysis'))/'progress.json').read_text())
            return {**record,**detail}
        except (OSError,ValueError):pass
    return record

@app.post('/investigate-tool',dependencies=[Depends(auth)])
def investigate_tool(body:InvestigationToolRequest):
    from .linux_tools import execute_tool
    return execute_tool(os.getenv('EVIDENCE_ROOT','/evidence'),os.getenv('ANALYSIS_ROOT','/analysis'),body)

@app.get('/files',dependencies=[Depends(auth)])
def files():return catalog(os.getenv('EVIDENCE_ROOT','/evidence'))

@app.get('/metadata',dependencies=[Depends(auth)])
def info(path:str):return metadata(os.getenv('EVIDENCE_ROOT','/evidence'),path)

@app.get('/health')
def health():return {'status':'ok'}

@app.get('/runtime',dependencies=[Depends(auth)])
def runtime():return {'source_sha256':runtime_code}
