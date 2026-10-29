#!/usr/bin/env python3
"""Render private Kubernetes phases from a fresh prepared Compose environment."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import string
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[2]
OWNERS=('users','catalog','payments','notifications')
VERSIONS=json.loads((Path(__file__).with_name('versions.json')).read_text())


def run(args,env=None):
    result=subprocess.run(args,env=env,capture_output=True,text=True,timeout=30)
    if result.returncode:raise ValueError('Cannot inspect prepared environment or source checkout')
    return result.stdout.strip()


def load_environment(env_file,docker_context):
    env=os.environ.copy()
    for line in env_file.read_text().splitlines():
        if '=' in line and not line.lstrip().startswith('#'):env.pop(line.split('=',1)[0].strip(),None)
    config=json.loads(run(['docker','--context',docker_context,'compose','--env-file',str(env_file.resolve()),'-f',str(ROOT/'docker-compose.yml'),'config','--format','json'],env))
    sources=json.loads((env_file.parent/'sources.json').read_text())
    if config['name']!=sources['project']:raise ValueError('Prepared project/source manifest mismatch')
    for owner in OWNERS:
        source=sources['services'][owner];path=source['path']
        if run(['git','-C',path,'rev-parse','HEAD'])!=source['sha'] or run(['git','-C',path,'status','--porcelain']):raise ValueError('Prepared source changed or is dirty: '+owner)
        if str(Path(config['services'][owner]['build']['context']).resolve())!=path:raise ValueError('Prepared build context mismatch')
    return config,sources


def render(args):
    output=args.output.resolve();config,sources=load_environment(args.env_file.resolve(),args.docker_context)
    suffix=config['name'].removeprefix('fcg-demo-')
    if len(suffix)!=12 or any(c not in '0123456789abcdef' for c in suffix):raise ValueError('Use a fresh prepare-local environment')
    # No overwrite or partial replacement of any existing rendered environment.
    output.mkdir(parents=True,exist_ok=False,mode=0o700)
    revision=uuid.uuid4().hex[:12];namespace='fcg-'+suffix
    images={owner:namespace+'-'+owner+':'+sources['services'][owner]['sha'] for owner in OWNERS}
    images.update({'postgres':namespace+'-postgres:source-'+VERSIONS['postgres'].split(':')[-1][:12],
                   'rabbitmq':namespace+'-rabbitmq:source-'+VERSIONS['rabbitmq'].split(':')[-1][:12],
                   'diagnostic':namespace+'-diagnostic:source-'+VERSIONS['diagnostic'].split(':')[-1][:12],
                   'bootstrap':namespace+'-bootstrap:'+run(['git','-C',str(ROOT),'rev-parse','HEAD'])})
    services=config['services'];pg=services['postgres']['environment'];mq=services['rabbitmq']['environment']
    values={'NAMESPACE':namespace,'RUN_ID':suffix,'REVISION':revision,'CONFIG_NAME':'infrastructure-'+revision,'SCRIPTS_NAME':'postgres-scripts-'+revision,
            'PG_SECRET':'postgres-'+revision,'MQ_SECRET':'rabbitmq-'+revision,'BOOTSTRAP_SECRET':'bootstrap-'+revision,
            'PG_ADMIN_PASSWORD':pg['POSTGRES_PASSWORD'],'MQ_ADMIN_USER':mq['RABBITMQ_DEFAULT_USER'],'MQ_ADMIN_PASSWORD':mq['RABBITMQ_DEFAULT_PASS'],
            'PG_ENTRYPOINT':(ROOT/'scripts/postgres/entrypoint.sh').read_text(),'PG_HEALTHCHECK':(ROOT/'scripts/postgres/healthcheck.sh').read_text(),'PG_INIT':(ROOT/'scripts/postgres/10-init.sh').read_text(),
            'POSTGRES_IMAGE':images['postgres'],'RABBITMQ_IMAGE':images['rabbitmq'],'BOOTSTRAP_IMAGE':images['bootstrap'],'DIAGNOSTIC_IMAGE':images['diagnostic']}
    for owner in OWNERS:
        values[owner.upper()+'_DB_PASSWORD']=pg['FCG_'+owner.upper()+'_DB_PASSWORD']
        values[owner.upper()+'_MQ_USER']=services[owner]['environment']['RabbitMq__Username']
        values[owner.upper()+'_MQ_PASSWORD']=services[owner]['environment']['RabbitMq__Password']
    phases={'00-namespace':['namespace'],'01-infrastructure':['config','secrets','postgres','rabbitmq'],'02-bootstrap':['bootstrap'],'03-diagnostics':['diagnostic']}
    hashes={}
    for phase,names in phases.items():
        directory=output/phase;directory.mkdir(mode=0o700)
        for name in names:
            source=ROOT/'k8s/templates'/f'{name}.yaml';template=string.Template(source.read_text())
            selected={key:json.dumps(values[key],ensure_ascii=True) for key in template.get_identifiers()}
            # The Job name appends its revision in a YAML scalar; the revision itself is safe hex.
            selected['REVISION']=revision
            target=directory/f'{name}.yaml';target.write_text(template.substitute(selected));target.chmod(0o600)
            hashes[str(source.relative_to(ROOT))]=hashlib.sha256(source.read_bytes()).hexdigest()
    record={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mode':'private-local-render-not-public-release','namespace':namespace,'cluster':namespace,'context':'kind-'+namespace,
            'run_id':suffix,'revision':revision,'bootstrap_job':'rabbitmq-bootstrap-'+revision,'root':str(ROOT),'root_sha':run(['git','-C',str(ROOT),'rev-parse','HEAD']),
            'root_dirty':bool(run(['git','-C',str(ROOT),'status','--porcelain'])),'env_file':str(args.env_file.resolve()),'docker_context':args.docker_context,'images':images,
            'image_sources':{'postgres':VERSIONS['postgres'],'rabbitmq':VERSIONS['rabbitmq'],'diagnostic':VERSIONS['diagnostic']},'node_image':VERSIONS['node'],'sources':sources['services'],'template_sha256':hashes,'phases':list(phases)}
    (output/'RENDER.json').write_text(json.dumps(record,indent=2)+'\n');(output/'RENDER.json').chmod(0o600)
    print('Private phases rendered: '+str(output))
    print('Namespace '+namespace+'; source templates are not directly applicable.')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--env-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--docker-context',default='orbstack')
    try:render(parser.parse_args())
    except Exception as error:print('Kubernetes rendering failed: '+type(error).__name__,file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
