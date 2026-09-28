import json
import os
from pathlib import Path
from pydantic import BaseModel,Field,ConfigDict
from .openrelik import OpenRelikGateway


class RemoteRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    capability:str=Field(min_length=1,max_length=100)
    folder_id:int=Field(gt=0)
    evidence_ids:list[str]=Field(min_length=1,max_length=20)


def gateway():
    path=Path(os.getenv('OPENRELIK_TEMPLATES','config/openrelik-templates.json'))
    if not os.getenv('OPENRELIK_URL') or not os.getenv('OPENRELIK_TOKEN') or not path.exists():
        raise ValueError('OpenRelik 주소, API 키, 승인 템플릿 설정이 필요합니다.')
    return OpenRelikGateway(os.environ['OPENRELIK_URL'],os.environ['OPENRELIK_TOKEN'],json.loads(path.read_text(encoding='utf-8')))


def submit(store,case_id,request):
    store.get(case_id,'case');client=gateway()
    try:
        files=[]
        for id in request.evidence_ids:
            evidence=store.get(id,'evidence')
            if not evidence.get('connected',True) or evidence['case_id']!=case_id or not evidence.get('openrelik_file_id') or not evidence.get('segment_manifest'):
                raise ValueError('무결성이 확인되고 OpenRelik 파일 ID가 연결된 사건 증거만 실행할 수 있습니다.')
            file=client.request('GET',f"/files/{evidence['openrelik_file_id']}")
            expected=evidence['segment_manifest'][0]['sha256']
            if file.get('hash_sha256')!=expected:
                raise ValueError('OpenRelik 원본 파일의 SHA-256이 로컬 증거와 일치하지 않습니다.')
            if file.get('folder',{}).get('id')!=request.folder_id:raise ValueError('사건 폴더와 원본 파일 소속이 일치하지 않습니다.')
            files.append(evidence['openrelik_file_id'])
        # Record before external mutation. Ambiguous failures never auto-resubmit.
        job=store.add('remote_job',case_id,status='submitting',capability=request.capability,evidence_ids=request.evidence_ids)
        try:
            registered=client.submit(request.capability,request.folder_id,files,files)
            store.update(job['id'],registered=registered,status='created')
            client.run(registered)
            return store.update(job['id'],status='running')
        except Exception as e:
            store.update(job['id'],status='needs_review',error=str(e));raise
    finally:client.client.close()


def refresh(store,id):
    job=store.get(id,'remote_job')
    if 'registered' not in job:raise ValueError('제출 결과가 불확실합니다. OpenRelik에서 직접 확인하세요.')
    client=gateway()
    try:
        data=client.status(job['registered']['folder_id'],job['registered']['id'])
        store.add('receipt',job['case_id'],receipt_type='openrelik_status',remote_job_id=id,result=data)
        # Workflow SUCCESS is not evidence coverage. A validated normalizer must attest output scope.
        return store.update(id,status=data['status'],last_status=data)
    finally:client.client.close()
