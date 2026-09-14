"""Package only Git-tracked source; never collect runtime data or secrets."""
import hashlib
import subprocess
import zipfile
from pathlib import Path

root=Path(__file__).resolve().parents[1]
files=subprocess.check_output(['git','ls-files','-z'],cwd=root).decode().split('\0')
output=root/'artifacts';output.mkdir(exist_ok=True)
archive=output/'frontier-0.1.0-source.zip'
with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
    for file in files:
        if not file:continue
        if file=='.env' or file.startswith(('data/','evidence/','reports/','vendor/openrelik/runtime/')):raise ValueError('Runtime file unexpectedly tracked: '+file)
        z.write(root/file,'frontier/'+file)
digest=hashlib.sha256(archive.read_bytes()).hexdigest()
(output/'SHA256SUMS').write_text(f'{digest}  {archive.name}\n',encoding='utf-8')
print(f'{archive.name}: {archive.stat().st_size} bytes; source files only')
