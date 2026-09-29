"""Grade a saved candidate against a separate key; no model/network execution."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from workbench.evaluation import score_investigation

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('candidate',type=Path)
    p.add_argument('--gold',type=Path,required=True)
    args=p.parse_args()
    print(json.dumps(score_investigation(json.loads(args.candidate.read_text()),json.loads(args.gold.read_text())),ensure_ascii=False,indent=2))
