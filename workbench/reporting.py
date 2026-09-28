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
    document={'schema_version':'1.2','id':uid('REPORT'),'generated_at':now(),'case':snap['case'],'evidence':snap['evidence'],'judgments':snap['judgments'],
            'coverage':snap['coverage'],'summary':snap['summary'],'review_progress':snap['review_progress'],'claims':claims,'automatic_findings':automatic,'observations':snap['observation'],
            'dossiers':[d for d in snap['dossier'] if d['evidence_id'] in active and set(d['observation_ids']).issubset(observations)
                and d['task_id'] in tasks and d.get('generation',0)==tasks[d['task_id']].get('retry_generation',0)],
            'investigation_jobs':jobs,
            'dossier_batches':[b for b in snap['dossier_batch'] if b.get('evidence_id') in active
                and b.get('task_id') in tasks and b.get('generation',0)==tasks[b['task_id']].get('retry_generation',0)],
            'limitations':[c for c in snap['coverage'] if c['status'] not in GOOD],
            'tool_receipts':[r for r in snap['receipt'] if r.get('task_id') and r.get('evidence_id') in active],
            'lineage':[l for l in snap['lineage'] if l.get('observation_id') in observations],
            'hypotheses':[h for h in snap['hypothesis'] if set(h.get('observation_ids',[])).issubset(observations) and (not h.get('evidence_id') or h['evidence_id'] in active)],
            'disconnected_evidence':disconnected,
            'notice':f"AI가 확인·유력·미확인으로 자동 판단한 결과입니다. 확인은 명시한 사실의 범위에 한정하며, 유력한 판단에도 근거·대안·남은 검사를 포함합니다. 연결 해제한 증거 {len(disconnected)}개는 제외합니다. 미수집 범위를 정상 또는 흔적 없음으로 해석하지 않습니다."}
    from .check_ledger import project as checks
    from .hypothesis_ledger import current_scope
    document['hypotheses']=[h for h in document['hypotheses'] if h.get('hypothesis_kind')!='dynamic' or current_scope(h,tasks,active)]
    from .session_links import project as sessions
    from .completion import project as completion, materials, presentations
    document['check_ledger']=checks(jobs,document['dossier_batches'])
    from .discovery import project as frontier
    document['investigation_frontier']=frontier(document,[r for r in controller.store.list('discovery_lead',case_id)
        if r['evidence_id'] in active and r['task_id'] in tasks and r.get('generation',0)==tasks[r['task_id']].get('retry_generation',0)])
    document['dossier_history']=[d for d in snap['dossier'] if d['evidence_id'] in active and d['task_id'] in tasks
        and d.get('generation',0)<tasks[d['task_id']].get('retry_generation',0)
        and set(d['observation_ids']).issubset(observations)]
    document['session_links']=sessions(document['observations'])
    document['case_synthesis']=[r for r in controller.store.list('case_synthesis',case_id)
        if r['evidence_id'] in active and r['task_id'] in tasks and r['generation']==tasks[r['task_id']].get('retry_generation',0)
        and set(r['finding']['observation_ids']+r.get('supporting_evidence_ids',[])+r.get('refuting_evidence_ids',[])
            +r['finding'].get('counterevidence_ids',[])+[oid for stage in r['finding'].get('stages',[]) for oid in stage['observation_ids']]).issubset(observations)]
    inputs=presentations(controller.store,case_id,tasks)
    document['completion']=completion(document,inputs)
    document['required_materials']=materials(document)
    document['review_failures']=[{'id':r['id'],'batch_id':r.get('batch_id'),'failure_category':r.get('failure_category','legacy_unclassified'),
        'error':r.get('error'),'diagnostic_id':r.get('diagnostic_id'),'validation_errors':r.get('validation_errors',[])}
        for r in document['tool_receipts'] if r.get('receipt_type') in ('dossier_model_error','synthesis_error')]
    return document


def cited_ids(document):
    """Every human-report citation has a visible source anchor, including stages."""
    findings=[f for j in document['judgments'] for f in j['findings']]
    findings += [s['finding'] for s in document.get('case_synthesis',[])]
    findings += [d['finding'] for d in document.get('dossier_history',[]) if d.get('finding')]
    findings += [d['finding'] for d in document.get('dossiers',[]) if d.get('finding')]
    ids={oid for f in findings+document['claims']+document.get('automatic_findings',[]) for oid in f.get('observation_ids',[])}
    ids.update(oid for f in findings for stage in f.get('stages',[]) for oid in stage['observation_ids'])
    ids.update(oid for f in findings for oid in f.get('counterevidence_ids',[]))
    ids.update(oid for s in document.get('case_synthesis',[]) for oid in s.get('supporting_evidence_ids',[])+s.get('refuting_evidence_ids',[]))
    ids.update(oid for s in document.get('session_links',[]) for oid in s['observation_ids'])
    ids.update(oid for c in document.get('check_ledger',{}).get('checks',[]) for contract in c['contracts']
        for oid in (contract.get('assessment') or {}).get('observation_ids',[]))
    ids.update(oid for c in document.get('investigation_frontier',{}).get('discovery_inventory',[]) for oid in c['observation_ids'])
    return ids


def render_view(view):
    env=Environment(loader=FileSystemLoader(ROOT/'templates'),autoescape=select_autoescape(['html']))
    env.policies['json.dumps_kwargs']={'sort_keys':True,'ensure_ascii':False}
    return env.get_template('report_readers.html').render(view=view)


def render(document,reader='analyst'):
    from .report_views import project
    return render_view(project(document)[reader])


def preview_document(controller,case_id):
    doc=report_document(controller,case_id)
    cited=cited_ids(doc)
    observations=doc['observations']
    doc['observations']=[o for i,o in enumerate(observations) if i<200 or o['id'] in cited]
    omitted=len(observations)-len(doc['observations'])
    if omitted:
        doc['notice']+=f' 화면 미리보기에는 일부 관측만 표시합니다. 나머지 {omitted}건을 포함한 전체 기록은 저장한 보고서에 포함됩니다.'
        doc['preview_omitted_observations']=omitted
    return doc


def build_report(controller,case_id,report_root):
    with controller.store.tx():
        revision=controller.store.report_revision(case_id)
        doc=report_document(controller,case_id)
    identity={'case_id':case_id,'scope_revision':revision,'result_revision':doc['case'].get('result_revision'),
              'evidence':[{k:e.get(k) for k in ('id','signature','connected')} for e in doc['evidence']]}
    doc['snapshot']={**identity,'scope_sha256':hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest(),
                     'captured_at':doc['generated_at'],'version':1}
    from .report_views import project, VERSION
    from .report_docx import render as render_docx
    views=project(doc)
    doc['synthesis_gate']=views['analyst']['synthesis_gate']
    doc['reader_contract']=VERSION
    doc['finding_map']=views['analyst']['finding_map']
    doc['reader_counts']=views['analyst']['counts']
    distributed={}
    for reader,view in views.items():
        distributed[reader+'.html']=render_view(view).encode()
        distributed[reader+'.docx']=render_docx(view)
    # Compatibility alias is redacted analyst output, never a separate selection.
    html=distributed['analyst.html']
    data=json.dumps(doc,ensure_ascii=False,indent=2).encode()
    template_hash=hashlib.sha256(b''.join(p.read_bytes() for p in sorted((ROOT/'templates').glob('report*.html')))).hexdigest()
    dest=Path(report_root)/doc['id'];dest.mkdir(parents=True,exist_ok=False)
    from .investigation_export import prepare,digest_file
    generated,external,external_hashes=prepare(doc,dest)
    base_files={'report.html':html,'report.json':data,**distributed,**generated}
    from .disk_budget import require_space
    require_space([(dest,2*sum(len(v) for v in base_files.values())+sum(p.stat().st_size for p in external.values())+1024*1024)])
    manifest={'schema_version':'1.2','report_id':doc['id'],'reader_contract':VERSION,'result_revision':doc['case'].get('result_revision'),'snapshot':doc['snapshot'],'template_sha256':template_hash,'files':{**{name:hashlib.sha256(value).hexdigest() for name,value in base_files.items()},**external_hashes}}
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
            reader_contract=VERSION,distributed_files={name:hashlib.sha256(value).hexdigest() for name,value in distributed.items()},
            claim_count=len(doc['claims']),gap_count=len(doc['limitations']))
