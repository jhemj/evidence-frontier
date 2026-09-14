"""Content-addressed investigation package, with verifiable source locators."""
import csv
import hashlib
import io
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path


def digest_file(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): h.update(chunk)
    return h.hexdigest()


def located(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file() or path.is_symlink():
        raise ValueError('파생 증거 경로가 유효하지 않습니다.')
    return path


def prepare(document, destination):
    root = Path(os.getenv('ANALYSIS_ROOT', '/analysis'))
    run_ids = {o['fields']['run_id'] for o in document['observations'] if o['type'] == 'linux_environment'}
    if not run_ids: return {}, {}, {}
    files = {}; ledger = []; object_ids = {}; generated = {}; pinned_hashes={}
    bases={(o['fields'].get('artifact_path'),o['fields'].get('path')):o['fields']['locator_basis']
           for o in document['observations'] if o['fields'].get('locator_basis')}
    for run_id in sorted(run_ids):
        manifest_path = located(root, run_id + '/manifest.json')
        reference = next(o['fields']['source_sha256'] for o in document['observations'] if o['type'] == 'linux_environment' and o['fields']['run_id'] == run_id)
        if digest_file(manifest_path) != reference: raise ValueError('분석 원장 해시가 변경되었습니다.')
        pinned_hashes['evidence/'+run_id+'/manifest.json']=reference
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        for name in ('manifest.json', 'filesystem_inventory.ndjson', 'events.ndjson', 'SHA256SUMS.json'):
            path = located(root, run_id + '/' + name); files['evidence/' + run_id + '/' + name] = path
        if (root/run_id/'hunt_files.ndjson').exists():files['evidence/'+run_id+'/hunt_files.ndjson']=located(root,run_id+'/hunt_files.ndjson')
        expected = json.loads((root / run_id / 'SHA256SUMS.json').read_text())
        for name,value in expected.items():
            key='evidence/'+run_id+'/'+name
            if key in pinned_hashes and pinned_hashes[key]!=value:raise ValueError('분석 원장 기준 해시 상충')
            pinned_hashes[key]=value
        for name in expected:
            if name.startswith('package_audit-') and name.endswith('.json'):
                files['evidence/'+run_id+'/'+name]=located(root,run_id+'/'+name)
        for source in manifest['sources']:
            if not source.get('relative_path') or not source.get('sha256'): continue
            relative = run_id + '/' + source['relative_path']; path = located(root, relative)
            if digest_file(path) != source['sha256']: raise ValueError('추출 증거 해시 불일치: ' + relative)
            ev_id = object_ids.setdefault(relative, f'EV-{len(object_ids) + 1:06d}')
            files['evidence/' + relative] = path
            pinned_hashes['evidence/'+relative]=source['sha256']
            if source.get('compressed_relative_path'):
                parent_relative=run_id+'/'+source['compressed_relative_path']
                parent=located(root,parent_relative)
                if digest_file(parent)!=source['compressed_sha256']:raise ValueError('압축 원문 해시 불일치')
                files['evidence/'+parent_relative]=parent
                pinned_hashes['evidence/'+parent_relative]=source['compressed_sha256']
            ledger.append({'evidence_id': ev_id, 'relative_path': 'evidence/' + relative, 'sha256': source['sha256'],
                           'image': manifest['image'], 'partition': source['partition_offset'], 'server_path': source['path'],
                           'locator': f"inode:{source['inode']}:offset:{source.get('source_offset',0)}:{source.get('locator_basis') or bases.get((relative,source['path']),'original file bytes')}", 'complete': source['complete'], 'status': source['status'],
                           'limitation': source.get('reason', '')})
        for name in ('filesystem_inventory.ndjson', 'events.ndjson'):
            if digest_file(root / run_id / name) != expected[name]: raise ValueError('행위 기록/인벤토리 해시 불일치')
    for observation in document['observations']:
        f = observation['fields']; relative = f.get('artifact_path'); expected = f.get('source_sha256')
        if not relative or not expected: continue
        path = located(root, relative)
        if relative not in object_ids:
            if digest_file(path) != expected: raise ValueError('추가 조사 원문 해시 불일치')
            ev_id = object_ids.setdefault(relative, f'EV-{len(object_ids) + 1:06d}')
            files['evidence/' + relative] = path
            pinned_hashes['evidence/'+relative]=expected
            ledger.append({'evidence_id': ev_id, 'relative_path': 'evidence/' + relative, 'sha256': expected,
                           'image': observation['source_location'].split(':')[0], 'partition': f.get('partition_offset'),
                           'server_path': f.get('path'), 'locator': observation['source_location'],
                           'complete': f.get('source_complete', True), 'status': 'extracted', 'limitation': f.get('interpretation_limit', '')})
    def csv_bytes(rows, columns):
        stream = io.StringIO(newline=''); writer = csv.DictWriter(stream, fieldnames=columns, extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows); return stream.getvalue().encode('utf-8-sig')
    generated['EVIDENCE_MANIFEST.csv'] = csv_bytes(ledger, ['evidence_id', 'relative_path', 'sha256', 'image', 'partition', 'server_path', 'locator', 'complete', 'status', 'limitation'])
    mapping = []
    for o in document['observations']:
        f = o['fields']; relative = f.get('artifact_path')
        if relative in object_ids:
            mapping.append({'observation_id': o['id'], 'evidence_id': object_ids[relative], 'locator': o['source_location'],
                            'line': f.get('line'), 'byte_offset': f.get('byte_offset'), 'sha256': f.get('source_sha256')})
    generated['REPORT_EVIDENCE_MAP.csv'] = csv_bytes(mapping, ['observation_id', 'evidence_id', 'locator', 'line', 'byte_offset', 'sha256'])
    timeline = destination / 'TIMELINE.csv'
    sort_path = destination / 'timeline-sort.sqlite3'
    sort_db = sqlite3.connect(sort_path)
    sort_db.execute('CREATE TABLE timeline (timestamp TEXT, row TEXT)')
    with timeline.open('w', newline='', encoding='utf-8-sig') as out:
        writer = csv.writer(out); writer.writerow(['timestamp_kst', 'source_timestamp', 'event', 'stage', 'evidence_id', 'server_path', 'line', 'byte_offset', 'time_basis', 'interpretation_limit'])
        # Sort on disk so full timelines remain chronological without an in-memory cap.
        for run_id in sorted(run_ids):
            with (root / run_id / 'events.ndjson').open(encoding='utf-8') as stream:
                for line in stream:
                    event = json.loads(line); timestamp = event.get('timestamp')
                    if not timestamp: continue
                    f = event['fields']; evidence_id = object_ids.get(f.get('artifact_path'))
                    if not evidence_id: raise ValueError('타임라인 원문 증거 연결 누락')
                    converted = datetime.fromisoformat(timestamp).astimezone(timezone(timedelta(hours=9))).isoformat()
                    row = [converted, timestamp, event['type'], f.get('stage'), evidence_id, f.get('path'), f.get('line'), f.get('byte_offset'), f.get('time_basis'), f.get('interpretation_limit')]
                    sort_db.execute('INSERT INTO timeline VALUES (?,?)', (converted, json.dumps(row, ensure_ascii=False)))
        sort_db.commit()
        for row in sort_db.execute('SELECT row FROM timeline ORDER BY timestamp,rowid'): writer.writerow(json.loads(row[0]))
    sort_db.close(); sort_path.unlink()
    files['TIMELINE.csv'] = timeline
    iocs = []
    for o in document['observations']:
        f = o['fields']
        for indicator in f.get('indicators', []):
            iocs.append({**indicator, 'observation_id': o['id'], 'evidence_id': object_ids.get(f.get('artifact_path'), ''),
                         'source': f.get('path'), 'stage': f.get('stage'), 'inspection_context': f.get('inspection_context', False),
                         'judgment': '미확인', 'limitation': '관찰된 지표 후보. 악성 IOC·목적 달성을 확정하지 않음'})
    generated['IOC_LIST.csv'] = csv_bytes(iocs, ['type', 'value', 'port', 'scope', 'source', 'stage', 'inspection_context', 'judgment', 'observation_id', 'evidence_id', 'limitation'])
    generated['HYPOTHESES.json'] = json.dumps(document['hypotheses'], ensure_ascii=False, indent=2).encode()
    generated['CASE_MEMORY.md'] = ('# ' + document['case']['name'] + '\n\n자동 침해조사 결과. 미확인 범위와 AI 검토 후보를 포함합니다.\n\n' +
        '\n\n'.join(f"## {h['text']}\n{h.get('judgment', '미확인')} (AI 후보)\n{h.get('reasoning', h.get('uncertainty', ''))}" for h in document['hypotheses'])).encode()
    generated['ANALYSIS_LEDGER.json'] = json.dumps(document['tool_receipts'], ensure_ascii=False, indent=2).encode()
    generated['RESULT_REVISION.json'] = json.dumps({'report_id':document['id'], 'result_revision':document['case'].get('result_revision'),
        'generated_at':document['generated_at'], 'artifacts':['report.json','IOC_LIST.csv','TIMELINE.csv','EVIDENCE_MANIFEST.csv']},ensure_ascii=False,indent=2).encode()
    generated['README-investigation.md'] = ('# 조사 패키지\n\n원본 E01은 포함하지 않습니다. EVIDENCE_MANIFEST.csv → evidence/ 파일 SHA-256으로 검증하세요. '
        'REPORT_EVIDENCE_MAP.csv는 화면 관측 ID와 원문 증거 ID, 위치를 연결합니다. TIMELINE.csv는 실제 레코드별 KST 시각 오름차순입니다. '
        '연도 추정 여부를 time_basis에서 확인하세요. IOC_LIST.csv는 후보 지표이며 감염 목록이 아닙니다. '
        '각 원장의 제외·부분 수집·파서 미지원 기록과 AI 반증 검토를 함께 읽으세요.\n').encode()
    hashes={}
    for name,path in files.items():
        hashes[name]=digest_file(path)
        if name in pinned_hashes and hashes[name]!=pinned_hashes[name]:raise ValueError('보고서 원문 산출물 해시 불일치: '+name)
    return generated, files, hashes
