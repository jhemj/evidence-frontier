import hashlib
import io
import json
import os
import zipfile
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape
from .controller import GOOD,ROOT
from .store import now,uid


def report_document(controller,case_id):
    with controller.store.lock:
        return _report_document(controller,case_id)


def _report_document(controller,case_id):
    snap=controller.snapshot(case_id)
    disconnected=[e for e in snap['evidence'] if not e.get('connected',True)]
    active=controller.active_ids(case_id)
    snap['evidence']=[e for e in snap['evidence'] if e['id'] in active]
    snap['coverage']=[c for c in snap['coverage'] if c['evidence_id'] in active]
    snap['observation']=controller.active_observations(case_id)
    observations={o['id']:o for o in snap['observation']}
    tasks={t['id']:t for t in snap['task']}
    jobs=[j for j in controller.store.list('investigation_job',case_id)
          if j.get('evidence_id') in active and j.get('task_id') in tasks
          and j.get('generation',0)==tasks[j['task_id']].get('retry_generation',0)]
    claims=[c for c in snap['claim'] if c['status']=='approved' and set(c['observation_ids']).issubset(observations)]
    automatic=[c for c in snap['claim'] if c.get('automatic') and set(c.get('observation_ids',[])).issubset(observations)]
    for claim in claims:
        if not claim.get('falsification') or claim['falsification']['missing_checks']:
            raise ValueError('승인된 주장에 미완료 검토가 있습니다.')
        for id in claim['observation_ids']:
            if id not in observations:raise ValueError('보고서 근거 연결이 끊어졌습니다.')
    return {'schema_version':'1.1','id':uid('REPORT'),'generated_at':now(),'case':snap['case'],'evidence':snap['evidence'],'judgments':snap['judgments'],
            'coverage':snap['coverage'],'summary':snap['summary'],'review_progress':snap['review_progress'],'claims':claims,'automatic_findings':automatic,'observations':snap['observation'],
            'dossiers':[d for d in snap['dossier'] if d['evidence_id'] in active and set(d['observation_ids']).issubset(observations)],
            'investigation_jobs':jobs,
            'dossier_batches':[b for b in snap['dossier_batch'] if b.get('evidence_id') in active
                and b.get('task_id') in tasks and b.get('generation',0)==tasks[b['task_id']].get('retry_generation',0)],
            'limitations':[c for c in snap['coverage'] if c['status'] not in GOOD],
            'tool_receipts':[r for r in snap['receipt'] if r.get('task_id') and r.get('evidence_id') in active],
            'lineage':[l for l in snap['lineage'] if l.get('observation_id') in observations],
            'hypotheses':[h for h in snap['hypothesis'] if set(h.get('observation_ids',[])).issubset(observations) and (not h.get('evidence_id') or h['evidence_id'] in active)],
            'disconnected_evidence':disconnected,
            'notice':f"AI가 확인·유력·미확인으로 자동 판단한 결과입니다. 확인은 명시한 사실의 범위에 한정하며, 유력한 판단에도 근거·대안·남은 검사를 포함합니다. 연결 해제한 증거 {len(disconnected)}개는 제외합니다. 미수집 범위를 정상 또는 흔적 없음으로 해석하지 않습니다."}


def render(document):
    env=Environment(loader=FileSystemLoader(ROOT/'templates'),autoescape=select_autoescape(['html']))
    env.policies['json.dumps_kwargs']={'sort_keys':True,'ensure_ascii':False}
    return env.get_template('report.html').render(report=document)


def preview_document(controller,case_id):
    doc=report_document(controller,case_id)
    cited={id for claim in doc['claims'] for id in claim['observation_ids']}
    observations=doc['observations']
    doc['observations']=[o for i,o in enumerate(observations) if i<200 or o['id'] in cited]
    omitted=len(observations)-len(doc['observations'])
    if omitted:
        doc['notice']+=f' 화면 미리보기에는 일부 관측만 표시합니다. 나머지 {omitted}건을 포함한 전체 기록은 저장한 보고서에 포함됩니다.'
    return doc


def build_report(controller,case_id,report_root):
    with controller.store.tx():
        revision=controller.store.report_revision(case_id)
        doc=report_document(controller,case_id)
    identity={'case_id':case_id,'scope_revision':revision,'result_revision':doc['case'].get('result_revision'),
              'evidence':[{k:e.get(k) for k in ('id','signature','connected')} for e in doc['evidence']]}
    doc['snapshot']={**identity,'scope_sha256':hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),
                     'captured_at':doc['generated_at'],'version':1}
    # Keep the human-readable report focused; the full JSON and evidence remain included.
    display={**doc,'observations':[o for o in doc['observations'] if o['type'].startswith('linux_')][:200] if doc.get('automatic_findings') else doc['observations']}
    html=render(display).encode()
    data=json.dumps(doc,ensure_ascii=False,indent=2).encode()
    template_hash=hashlib.sha256((ROOT/'templates/report.html').read_bytes()).hexdigest()
    dest=Path(report_root)/doc['id'];dest.mkdir(parents=True,exist_ok=False)
    from .investigation_export import prepare,digest_file
    generated,external,external_hashes=prepare(doc,dest)
    base_files={'report.html':html,'report.json':data,**generated}
    manifest={'schema_version':'1.1','report_id':doc['id'],'result_revision':doc['case'].get('result_revision'),'snapshot':doc['snapshot'],'template_sha256':template_hash,'files':{**{name:hashlib.sha256(value).hexdigest() for name,value in base_files.items()},**external_hashes}}
    manifest_bytes=json.dumps(manifest,indent=2).encode()
    files={**base_files,'manifest.json':manifest_bytes}
    checksums=(''.join(f'{hashlib.sha256(content).hexdigest()}  {name}\n' for name,content in files.items())+''.join(f'{digest}  {name}\n' for name,digest in external_hashes.items())).encode()
    for name,content in {**files,'SHA256SUMS':checksums}.items():(dest/name).write_bytes(content)
    with zipfile.ZipFile(dest/'report.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name,content in {**files,'SHA256SUMS':checksums}.items():z.writestr(name,content)
        for name,path in external.items():
            digest=hashlib.sha256()
            with path.open('rb') as source,z.open(name,'w',force_zip64=True) as output:
                for chunk in iter(lambda:source.read(1024*1024),b''):
                    output.write(chunk);digest.update(chunk)
            if digest.hexdigest()!=external_hashes[name]:raise ValueError('보고서 생성 중 원문 산출물이 변경되었습니다: '+name)
    with (dest/'report.zip').open('r+b') as stream:os.fsync(stream.fileno())
    archive_hash=digest_file(dest/'report.zip')
    with controller.store.tx():
        if controller.store.report_revision(case_id)!=revision:
            raise ValueError('보고서 생성 중 조사 근거나 판단이 변경되었습니다. 현재 결과로 다시 생성하세요.')
        return controller.store.add('report',case_id,report_id=doc['id'],sha256=archive_hash,
            snapshot=doc['snapshot'],result_revision=doc['case'].get('result_revision'),
            claim_count=len(doc['claims']),gap_count=len(doc['limitations']))
