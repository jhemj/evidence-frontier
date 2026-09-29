"""Download and verify a report from the real service without printing evidence.

Usage: python scripts/verify_report_package.py BASE_URL CASE_ID OUTPUT_ZIP
This verifies integrity and reference consistency, not forensic conclusions.
"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import urllib.request
import zipfile

base, cid, output = sys.argv[1:4]
headers = {}
if os.getenv('WORKBENCH_TOKEN'):
    headers['X-Workbench-Token'] = os.environ['WORKBENCH_TOKEN']

def request(path):
    return urllib.request.urlopen(urllib.request.Request(base + path, headers=headers), timeout=120)

state = json.load(request('/api/cases/' + cid))
assert state['case']['status'] not in ('running', 'paused', 'pause_requested'), 'Investigation still active'
assert state['report'], 'No report available'
record = state['report'][-1]
destination = Path(output)
destination.parent.mkdir(parents=True, exist_ok=True)
digest = hashlib.sha256()
with request('/api/reports/' + record['id'] + '/download') as response, destination.open('wb') as stream:
    while chunk := response.read(1024 * 1024):
        digest.update(chunk)
        stream.write(chunk)
assert digest.hexdigest() == record['sha256'], 'Downloaded ZIP hash mismatch'
with zipfile.ZipFile(destination) as archive:
    manifest = json.loads(archive.read('manifest.json'))
    for name, expected in manifest['files'].items():
        with archive.open(name) as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == expected, name
    document = json.loads(archive.read('report.json'))
    if record.get('snapshot'):
        assert record['snapshot']==manifest['snapshot']==document['snapshot']
        assert record.get('result_revision')==document['case'].get('result_revision')
    observations = {o['id']: o for o in document['observations']}
    for claim in document['claims'] + document.get('automatic_findings', []):
        assert set(claim['observation_ids']).issubset(observations), claim['id']
    judgments = [finding for result in document.get('judgments', []) for finding in result['findings']]
    judgments += [row['finding'] for row in document.get('case_synthesis', [])]
    for row in document.get('case_synthesis',[]):
        assert set(row.get('supporting_evidence_ids',[])+row.get('refuting_evidence_ids',[])).issubset(observations)
    for finding in judgments:
        assert finding['judgment'] in ('확인', '유력', '미확인')
        assert set(finding['observation_ids']).issubset(observations)
        assert finding['judgment'] == '미확인' or finding['observation_ids']
        assert all(set(stage['observation_ids']).issubset(observations) for stage in finding.get('stages',[]))
    if document.get('schema_version')=='1.2':
        from html.parser import HTMLParser
        class Anchors(HTMLParser):
            def __init__(self):super().__init__();self.ids=set();self.refs=set()
            def handle_starttag(self,tag,attrs):
                attrs=dict(attrs)
                if 'id' in attrs:self.ids.add(attrs['id'])
                if attrs.get('href','').startswith('#'):self.refs.add(attrs['href'][1:])
        anchors=Anchors();anchors.feed(archive.read('report.html').decode())
        assert anchors.refs.issubset(anchors.ids),'report citation anchor missing'
    dossier_ids={d['id'] for d in document.get('dossiers',[])+document.get('dossier_history',[])}
    for historical in document.get('review_progress',{}).get('historical_assessments',[]):
        assert historical['freshness']=='historical_not_revalidated'
        assert historical['dossier_id'] in dossier_ids
        assert historical['current_dossier_id'] in dossier_ids
        assert set(historical['finding']['observation_ids']).issubset(observations)
    summary = {'status': 'passed', 'case_id': cid, 'report_id': record['id'],
               'zip_bytes': destination.stat().st_size, 'zip_sha256': digest.hexdigest(),
               'verified_files': len(manifest['files']), 'observations': len(observations),
               'candidate_claims': len(document.get('automatic_findings', []))}
    summary['judgment_counts'] = {level:sum(f['judgment']==level for f in judgments) for level in ('확인','유력','미확인')}
    if 'RESULT_REVISION.json' in archive.namelist():
        revision = json.loads(archive.read('RESULT_REVISION.json'))['result_revision']
        assert revision == document['case']['result_revision'] == manifest['result_revision']
        mapped = {r['observation_id'] for r in csv.DictReader(io.StringIO(archive.read('REPORT_EVIDENCE_MAP.csv').decode('utf-8-sig')))}
        assert {o['id'] for o in observations.values() if o['fields'].get('artifact_path')}.issubset(mapped)
        previous = ''; timeline_count = 0
        with archive.open('TIMELINE.csv') as stream:
            for row in csv.DictReader(io.TextIOWrapper(stream, encoding='utf-8-sig')):
                if not row['timestamp_kst']:
                    from datetime import datetime
                    assert datetime.fromisoformat(row['source_timestamp']).tzinfo is None
                    continue
                assert row['timestamp_kst'].endswith('+09:00')
                assert row['timestamp_kst'] >= previous
                previous = row['timestamp_kst']; timeline_count += 1
        summary.update(result_revision=revision, mapped_observations=len(mapped), timeline_rows=timeline_count)
    receipts = document['tool_receipts']
    summary['receipt_counts'] = {kind: sum(r.get('receipt_type') == kind for r in receipts)
        for kind in ('investigator_model', 'investigation_tool', 'automatic_falsifier', 'model_error')}
    summary['investigation_results'] = [r['result'] for r in receipts if r.get('result', {}).get('tool') == 'investigation-graph-1']
print(json.dumps(summary, ensure_ascii=False))
