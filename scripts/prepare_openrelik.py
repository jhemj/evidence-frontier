"""Create a separate, pinned OpenRelik deployment. Does not start or modify an existing instance."""
import json
import secrets
import shutil
from pathlib import Path
import yaml

root=Path(__file__).resolve().parents[1]
vendor=root/'vendor/openrelik'
runtime=vendor/'runtime'
if runtime.exists():raise SystemExit('runtime already exists; refusing to replace existing configuration.')
runtime.mkdir(parents=True)
(runtime/'config').mkdir()
password=secrets.token_hex(24)
replacements={'<REPLACE_WITH_STORAGE_PATH>':'/usr/share/openrelik/data/artifacts',
 '<REPLACE_WITH_POSTGRES_USER>':'openrelik','<REPLACE_WITH_POSTGRES_PASSWORD>':password,
 '<REPLACE_WITH_POSTGRES_SERVER>':'openrelik-postgres','<REPLACE_WITH_POSTGRES_DATABASE_NAME>':'openrelik',
 '<REPLACE_WITH_RANDOM_SESSION_STRING>':secrets.token_hex(32),'<REPLACE_WITH_RANDOM_JWT_STRING>':secrets.token_hex(32)}
settings=(vendor/'settings.upstream.toml').read_text(encoding='utf-8')
environment=(vendor/'config.upstream.env').read_text(encoding='utf-8')
for key,value in replacements.items():settings=settings.replace(key,value);environment=environment.replace(key,value)
(runtime/'config/settings.toml').write_text(settings,encoding='utf-8')
(runtime/'.env').write_text(environment,encoding='utf-8')
compose=yaml.safe_load((vendor/'compose.upstream.yaml').read_text(encoding='utf-8'))
compose['name']='frontier-openrelik'
for name in ('openrelik-metrics','openrelik-prometheus'):compose['services'].pop(name,None)
compose['volumes']={'artifacts':{},'postgres-data':{}}
compose['networks']={'default':{'name':'frontier_openrelik'}}
for name,service in compose['services'].items():
    service.pop('container_name',None)
    if 'environment' in service:
        service['environment']=[v for v in service['environment'] if not v.startswith('PROMETHEUS_SERVER_URL=')]
    if name=='openrelik-postgres':service['volumes']=['postgres-data:/var/lib/postgresql/data']
    elif 'volumes' in service:
        service['volumes']=[v.replace('./data:','artifacts:').replace('./config:','./config:').replace(':z',':ro') if v.startswith('./config:') else v.replace('./data:','artifacts:').replace(':z','') for v in service['volumes']]
(runtime/'compose.yaml').write_text(yaml.safe_dump(compose,sort_keys=False),encoding='utf-8')
if (vendor/'compose.pinned.yaml').exists():shutil.copyfile(vendor/'compose.pinned.yaml',runtime/'compose.pinned.yaml')
for path in (runtime/'.env',runtime/'config/settings.toml'):
    path.chmod(0o600)
print('Prepared vendor/openrelik/runtime. Start with: docker compose --project-directory vendor/openrelik/runtime up -d')
print('Then follow docs/OPENRELIK.md for migrations, local user creation, and template approval.')
