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


def infrastructure_values(record,config):
    services=config['services'];pg=services['postgres']['environment'];mq=services['rabbitmq']['environment']
    values={'NAMESPACE':record['namespace'],'RUN_ID':record['run_id'],'REVISION':record['revision'],'CONFIG_NAME':'infrastructure-'+record['revision'],'SCRIPTS_NAME':'postgres-scripts-'+record['revision'],
            'PG_SECRET':'postgres-'+record['revision'],'MQ_SECRET':'rabbitmq-'+record['revision'],'BOOTSTRAP_SECRET':'bootstrap-'+record['revision'],
            'PG_ADMIN_PASSWORD':pg['POSTGRES_PASSWORD'],'MQ_ADMIN_USER':mq['RABBITMQ_DEFAULT_USER'],'MQ_ADMIN_PASSWORD':mq['RABBITMQ_DEFAULT_PASS'],
            'PG_ENTRYPOINT':(ROOT/'scripts/postgres/entrypoint.sh').read_text(),'PG_HEALTHCHECK':(ROOT/'scripts/postgres/healthcheck.sh').read_text(),'PG_INIT':(ROOT/'scripts/postgres/10-init.sh').read_text(),
            'POSTGRES_IMAGE':record['images']['postgres'],'RABBITMQ_IMAGE':record['images']['rabbitmq'],'BOOTSTRAP_IMAGE':record['images']['bootstrap'],'DIAGNOSTIC_IMAGE':record['images']['diagnostic']}
    for owner in OWNERS:
        values[owner.upper()+'_DB_PASSWORD']=pg['FCG_'+owner.upper()+'_DB_PASSWORD']
        values[owner.upper()+'_MQ_USER']=services[owner]['environment']['RabbitMq__Username']
        values[owner.upper()+'_MQ_PASSWORD']=services[owner]['environment']['RabbitMq__Password']
    return values


def application_values(record,config,owner):
    env=config['services'][owner]['environment'];revision=record['revision']
    values={'NAMESPACE':record['namespace'],'CONFIG_NAME':owner+'-config-'+revision,'SECRET_NAME':owner+'-credentials-'+revision,
            'KEYS_SECRET_NAME':owner+'-keys-'+revision,'RENDER_REVISION':revision,'SOURCE_SHA':record['sources'][owner]['sha'],'IMAGE':record['images'][owner],
            'GSS_MODE':env['PGGSSENCMODE'],'CONNECTION_STRING':env['ConnectionStrings__Default'],'MQ_USER':env['RabbitMq__Username'],'MQ_PASSWORD':env['RabbitMq__Password']}
    if owner in ('users','catalog'):
        values.update({'JWT_ISSUER':env['Jwt__Issuer'],'JWT_AUDIENCE':env['Jwt__Audience'],'SWAGGER_ENABLED':env['Swagger__Enabled']})
        for volume in config['services'][owner]['volumes']:
            if volume['target']=='/run/secrets/public.pem':values['PUBLIC_KEY_PEM']=Path(volume['source']).read_text()
            if owner=='users' and volume['target']=='/run/secrets/private.pem':values['PRIVATE_KEY_PEM']=Path(volume['source']).read_text()
    if owner=='users':
        for variable,name in [('Enabled','BOOTSTRAP_ENABLED'),('Email','BOOTSTRAP_EMAIL'),('Password','BOOTSTRAP_PASSWORD'),('PlayerEmail','BOOTSTRAP_PLAYER_EMAIL'),('PlayerPassword','BOOTSTRAP_PLAYER_PASSWORD')]:values[name]=env['AdminBootstrap__'+variable]
    if owner=='payments':values.update({'APPROVAL_LIMIT':env['PaymentSimulation__ApprovalLimit'],'PAYMENT_DELAY':env['PaymentSimulation__Delay']})
    return values


def application_text(record,config,owner,name):
    source=Path(record['sources'][owner]['path'])/'k8s'/name
    if not source.resolve().is_relative_to(Path(record['sources'][owner]['path']).resolve()):raise ValueError('Template escaped its service repository')
    template=string.Template(source.read_text());values=application_values(record,config,owner)
    return source,template.substitute({key:json.dumps(values[key],ensure_ascii=True) for key in template.get_identifiers()})


def verify_rendered(output):
    record=json.loads((output/'RENDER.json').read_text());config,sources=load_environment(Path(record['env_file']),record['docker_context'])
    if record['root']!=str(ROOT) or sources['services']!=record['sources']:raise ValueError('Rendered sources or orchestration checkout differ')
    values=infrastructure_values(record,config)
    phase_for={'namespace':'00-namespace','config':'01-infrastructure','secrets':'01-infrastructure','postgres':'01-infrastructure','rabbitmq':'01-infrastructure','bootstrap':'02-bootstrap','diagnostic':'03-diagnostics'}
    for name,expected in record['template_sha256'].items():
        source=ROOT/name
        if hashlib.sha256(source.read_bytes()).hexdigest()!=expected:raise ValueError('Infrastructure template changed after rendering')
        template=string.Template(source.read_text());selected={key:json.dumps(values[key],ensure_ascii=True) for key in template.get_identifiers()};selected['REVISION']=record['revision']
        if (output/phase_for[source.stem]/source.name).read_text()!=template.substitute(selected):raise ValueError('Infrastructure aggregate differs from verified source/configuration')
    for owner,files in record.get('application_sources',{}).items():
        for name,digest in files.items():
            source,expected=application_text(record,config,owner,name)
            if hashlib.sha256(source.read_bytes()).hexdigest()!=digest or (output/'04-applications'/(owner+'-'+name)).read_text()!=expected:raise ValueError('Application aggregate differs from verified source/configuration')
    return record


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
    values=infrastructure_values({'namespace':namespace,'revision':revision,'run_id':suffix,'images':images},config)
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
    if getattr(args,'applications',False):
        directory=output/'04-applications';directory.mkdir(mode=0o700);record['application_sources']={}
        for owner in OWNERS:
            record['application_sources'][owner]={}
            for name in ('configmap.yaml','secret.yaml','deployment.yaml','service.yaml'):
                source,text=application_text(record,config,owner,name)
                target=directory/(owner+'-'+name);target.write_text(text);target.chmod(0o600)
                record['application_sources'][owner][name]=hashlib.sha256(source.read_bytes()).hexdigest()
        record['phases'].append('04-applications')
    (output/'RENDER.json').write_text(json.dumps(record,indent=2)+'\n');(output/'RENDER.json').chmod(0o600)
    print('Private phases rendered: '+str(output))
    print('Namespace '+namespace+'; source templates are not directly applicable.')
    return record


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--env-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--docker-context',default='orbstack');parser.add_argument('--applications',action='store_true')
    try:render(parser.parse_args())
    except Exception as error:print('Kubernetes rendering failed: '+type(error).__name__,file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
