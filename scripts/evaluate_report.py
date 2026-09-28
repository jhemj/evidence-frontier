"""Evaluate a completed inert-fixture report against predeclared criteria."""
import json
import sys
import zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.evaluation import evaluate

archive_path=Path(sys.argv[1]);criteria_path=Path(__file__).resolve().parents[1]/'config/hunt-evaluation.json'
criteria=json.loads(criteria_path.read_text(encoding='utf-8'))
with zipfile.ZipFile(archive_path) as archive:
    document=json.loads(archive.read('report.json'))
    scanned=[]
    for name in archive.namelist():
        if name.endswith('/hunt_files.ndjson'):
            for line in archive.read(name).splitlines():
                f=json.loads(line)
                if f['status']=='scanned':scanned.append(f['path'])
    document['evaluation_scanned_paths']=scanned
metrics=evaluate(document,criteria)
passed=(metrics['pattern_recall'] is not None and metrics['pattern_recall']>=criteria['minimum_pattern_recall']
    and metrics['normal_false_positives']<=criteria['maximum_normal_pattern_false_positives']
    and metrics['normal_unprocessed']==0 and metrics['review_rate'] is not None
    and metrics['review_rate']>=criteria['minimum_dossier_review_rate']
    and metrics['unknown_citations']==0 and not metrics['stage_scope_violations'])
metrics['acceptance']='passed' if passed else 'failed_or_incomplete'
archive_path.with_suffix('.evaluation.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(metrics,ensure_ascii=False,indent=2))
sys.exit(0 if passed else 1)
