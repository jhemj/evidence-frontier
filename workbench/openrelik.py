"""Restricted adapter; no arbitrary spec, local path or workflow parameter from callers."""
import hashlib
import json
import httpx


class OpenRelikGateway:
    def __init__(self,base_url,token,approved_templates,client=None):
        self.base=base_url.rstrip('/')
        self.approved=approved_templates
        self.client=client or httpx.Client(timeout=30,follow_redirects=False,trust_env=False)
        self.headers={'x-openrelik-access-token':token}

    def request(self,method,path,**kwargs):
        r=self.client.request(method,self.base+path,headers=self.headers,**kwargs)
        r.raise_for_status();return r.json()

    def submit(self,capability,folder_id,file_ids,allowed_file_ids):
        contract=self.approved.get(capability)
        if not contract or not file_ids or not set(file_ids).issubset(set(allowed_file_ids)):
            raise ValueError('승인된 템플릿과 사건 소속 파일만 사용할 수 있습니다.')
        template=self.request('GET',f"/workflows/templates/{int(contract['id'])}")
        spec=json.loads(template['spec_json'])
        digest=hashlib.sha256(json.dumps(spec,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        if digest!=contract['sha256']:
            raise ValueError('승인 이후 OpenRelik 템플릿이 변경되었습니다.')
        created=self.request('POST',f'/folders/{folder_id}/workflows/',json={'folder_id':folder_id,'file_ids':file_ids,'template_id':contract['id'],'template_params':{}})
        # Server creates a workflow in its own child folder; use returned folder authority.
        actual_folder=created['folder']['id']
        return {'id':created['id'],'folder_id':actual_folder,'spec':json.loads(created['spec_json'])}

    def run(self,registered):
        return self.request('POST',f"/folders/{registered['folder_id']}/workflows/{registered['id']}/run/",json={'workflow_spec':registered['spec']})

    def status(self,folder_id,workflow_id):
        return self.request('GET',f'/folders/{folder_id}/workflows/{workflow_id}/status')
