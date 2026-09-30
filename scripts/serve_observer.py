"""Loopback-only replay or bounded query-only live observer UI.

Only fixed snapshot reads and verified historical report downloads are routed.
The original E2E and its frozen code are never imported or changed.
"""
import argparse
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import re
import sys

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workbench.observer_view import validate

ROOT = Path(__file__).resolve().parents[1]
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
       "connect-src 'self'; font-src 'self'; base-uri 'none'; object-src 'none'; "
       "frame-src 'none'; frame-ancestors 'none'; form-action 'none'")
FORMATS = {'executive.html', 'executive.docx', 'analyst.html', 'analyst.docx'}


def create_app(snapshot_files=(), *, static_root=None, report_root=None, live_source=None, source_database=None):
    views = [validate(json.loads(Path(p).read_text())) for p in snapshot_files]
    if not views and not live_source:
        raise ValueError('At least one snapshot is required')
    if views and live_source:
        raise ValueError('Replay and live sources cannot be mixed')
    identities = {(v['envelope']['case_id'], v['envelope']['run_id'], v['envelope']['data_mode']) for v in views}
    if views and len(identities) != 1:
        raise ValueError('Mixed cases, runs or data modes are forbidden')
    views.sort(key=lambda v: v['envelope']['sequence'])
    seq = [v['envelope']['sequence'] for v in views]
    if len(set(seq)) != len(seq):
        raise ValueError('Duplicate snapshot sequence')
    versions = {v['envelope']['projection_revision']: v for v in views}
    report_base = Path(report_root).resolve(strict=True) if report_root else None
    @asynccontextmanager
    async def lifespan(app):
        if live_source:
            live_source.start()
        try:
            yield
        finally:
            if live_source:
                live_source.close()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver'])

    @app.middleware('http')
    async def safe(request: Request, call_next):
        if request.method not in ('GET', 'HEAD'):
            response = JSONResponse({'detail': '읽기 전용 화면입니다. 조사·모델 호출은 제공하지 않습니다.'}, status_code=405)
        elif request.headers.get('sec-fetch-site') == 'cross-site' or (
                request.headers.get('origin') and request.headers['origin'] != str(request.base_url).rstrip('/')):
            response = JSONResponse({'detail': 'Cross-origin read blocked'}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({'Content-Security-Policy': CSP, 'X-Content-Type-Options': 'nosniff',
                                 'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store',
                                 'Cross-Origin-Resource-Policy': 'same-origin'})
        return response

    @app.get('/api/session')
    def session():
        if live_source:
            return {**live_source.session(), 'report_downloads_available': report_base is not None}
        return {'mode': 'read_only_replay', 'latest': views[-1]['envelope'],
                'snapshots': [v['envelope'] for v in views], 'automatic_model_calls': 0,
                'report_downloads_available': report_base is not None}

    @app.get('/api/snapshots/{revision}')
    def snapshot(revision: str):
        if live_source:
            result = live_source.snapshot(revision)
            if result is None:
                raise HTTPException(404, '관측 캐시에서 만료됨. 현재 스냅샷을 다시 확인하세요.')
            return result
        if revision not in versions:
            raise HTTPException(404)
        return versions[revision]

    @app.get('/api/reports/{record_id}/{filename}')
    def download(record_id: str, filename: str):
        report_views = live_source.all_views() if live_source else views
        if not report_base or not report_views or filename not in FORMATS or report_views[-1]['envelope']['data_mode'] == 'example':
            raise HTTPException(404)
        report = next((r for v in reversed(report_views) for r in v['reports'] if r['id'] == record_id), None)
        if not report or not re.fullmatch(r'REPORT-[A-Za-z0-9_-]+', report.get('report_id') or ''):
            raise HTTPException(404)
        path = (report_base / report['report_id'] / filename).resolve()
        if not path.is_relative_to(report_base) or not path.is_file():
            raise HTTPException(404)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != report['formats'].get(filename):
            raise HTTPException(409, '보존 보고서 해시 불일치. 원본을 변경하거나 대체하지 않았습니다.')
        # Download only: never execute an evidence-bearing HTML document in this UI.
        return Response(data, media_type='application/octet-stream',
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @app.get('/api/sources/{revision}/{observation_id}')
    def source(revision:str,observation_id:str,offset:int=0,limit:int=12000):
        # Explicit selected observation and exact version, never arbitrary paths,
        # raw-image extraction, a model request, or source URL access.
        view=live_source.snapshot(revision) if live_source else versions.get(revision)
        if not source_database or not view or view['envelope']['data_mode']=='example':raise HTTPException(404)
        selected=view['objects'].get('observation:'+observation_id)
        if not selected:raise HTTPException(404)
        if offset<0 or not 1<=limit<=65536:raise HTTPException(400,'Invalid character range')
        import sqlite3
        from workbench.observer_view import digest
        with sqlite3.connect(Path(source_database).resolve(strict=True).as_uri()+'?mode=ro',uri=True) as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            result=db.execute('SELECT body FROM records WHERE id=? AND kind=? AND case_id=?',
                (observation_id,'observation',view['envelope']['case_id'])).fetchone()
            if not result:raise HTTPException(404)
            original=json.loads(result[0])
            if digest(original)!=selected['source_version']:raise HTTPException(409,'Source version changed; resynchronize')
            text=original.get('fields',{}).get('excerpt')
            if not isinstance(text,str):raise HTTPException(404,'Retained text field unavailable')
            return {'source_version':selected['source_version'],'observation_id':observation_id,
                'offset_characters':offset,'text':text[offset:offset+limit],'retained_characters':len(text),
                'next_offset':offset+limit if offset+limit<len(text) else None,
                'retained_field_only':True,'original_file_complete':original.get('fields',{}).get('source_complete')}

    @app.get('/api/explanations/{revision}/{hypothesis_id}/{entry_revision}')
    def explanation(revision: str, hypothesis_id: str, entry_revision: int):
        view=live_source.snapshot(revision) if live_source else versions.get(revision)
        if not source_database or not view or view['envelope']['data_mode']=='example':raise HTTPException(404)
        selected=view['objects'].get('hypothesis:'+hypothesis_id)
        manifest=(selected or {}).get('explanation_history')
        if not manifest or entry_revision<1:raise HTTPException(404)
        descriptor=next((r for r in manifest['entries'] if r['revision']==entry_revision),None)
        if not descriptor:raise HTTPException(404)
        import sqlite3
        from workbench.observer_history import explanation_entries, explanation_manifest
        with sqlite3.connect(Path(source_database).resolve(strict=True).as_uri()+'?mode=ro',uri=True) as db:
            db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
            result=db.execute("SELECT body FROM records WHERE id=? AND case_id=? AND kind='hypothesis'",
                (hypothesis_id,view['envelope']['case_id'])).fetchone()
            if not result:raise HTTPException(404)
            original=json.loads(result[0])
            if (original.get('hypothesis_kind')!='dynamic' or
                any(original.get(k)!=selected.get(k) for k in ('task_id','evidence_id')) or
                original.get('generation',0)!=selected.get('generation',0) or
                explanation_manifest(original)!=manifest):
                raise HTTPException(409,'Explanation history changed; resynchronize')
            entry=next(e for e in explanation_entries(original) if e['revision']==entry_revision)
            # These refs identify source versions in the requested snapshot,
            # NOT the source contents/freshness at the historical explanation.
            refs=[{'key':'observation:'+i,'version':view['objects']['observation:'+i]['version']}
                for i in entry['observation_ids'] if 'observation:'+i in view['objects']]
            return {'envelope':view['envelope'],'owner_ref':{'key':selected['key'],'version':selected['version']},
                'history_version':manifest['version'],'entry':entry,'source_refs':refs,
                'source_reference_scope':'requested_snapshot_not_historical_source_versions'}

    app.mount('/', StaticFiles(directory=static_root or ROOT / 'ui' / 'observer', html=True), name='observer')
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--snapshot', type=Path, action='append')
    source.add_argument('--database', type=Path)
    parser.add_argument('--case-id')
    parser.add_argument('--run-id')
    parser.add_argument('--source-binding')
    parser.add_argument('--observer-cache', type=Path)
    parser.add_argument('--interval', type=int, default=30)
    parser.add_argument('--report-root', type=Path)
    parser.add_argument('--port', type=int, required=True)
    args = parser.parse_args()
    live = None
    if args.database:
        if not all((args.case_id, args.run_id, args.source_binding, args.observer_cache)):
            parser.error('Live mode requires --case-id, --run-id, --source-binding and --observer-cache')
        from scripts.observer_snapshot import capture
        from workbench.observer_session import LiveObserver
        if args.observer_cache.resolve().is_relative_to(args.database.resolve().parent):
            parser.error('Observer cache must be outside the source database directory')
        live = LiveObserver(lambda seq: capture(args.database, args.case_id, args.run_id, seq, data_mode='live'),
                            case_id=args.case_id, run_id=args.run_id, source_binding=args.source_binding,
                            cache_dir=args.observer_cache, interval=args.interval)
    import uvicorn
    uvicorn.run(create_app(args.snapshot or (),report_root=args.report_root,live_source=live,source_database=args.database),host='127.0.0.1',port=args.port)
