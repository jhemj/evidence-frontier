"""One isolated, time-limited static investigation tool process."""
import json
import os
import sys
from pathlib import Path
from .models import InvestigationToolRequest
from .linux_tools import execute_tool

if __name__ == '__main__':
    body = InvestigationToolRequest.model_validate_json(Path(sys.argv[1]).read_bytes())
    result = execute_tool(os.getenv('EVIDENCE_ROOT','/evidence'), os.getenv('ANALYSIS_ROOT','/analysis'), body)
    Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
