"""Resolve image tags to immutable registry digests without pulling their layers."""
import argparse
import json
import subprocess
from pathlib import Path
import yaml

parser=argparse.ArgumentParser()
parser.add_argument('compose_file',type=Path)
args=parser.parse_args()
base=args.compose_file.resolve()
resolved=json.loads(subprocess.check_output(['docker','compose','-f',str(base),'config','--format','json'],text=True))
override={'services':{}}
for name,service in resolved['services'].items():
    image=service.get('image')
    if not image:continue
    if '@sha256:' in image:pin=image
    else:
        metadata=subprocess.check_output(['docker','buildx','imagetools','inspect',image,'--format','{{json .Manifest}}'],text=True)
        digest=json.loads(metadata)['digest']
        pin=image.split('@')[0]+'@'+digest
    override['services'][name]={'image':pin}
target=base.parent/'compose.pinned.yaml'
target.write_text(yaml.safe_dump(override,sort_keys=False),encoding='utf-8')
print(f'Wrote {target.name} with {len(override["services"])} immutable image references. Use it as the final -f overlay.')
