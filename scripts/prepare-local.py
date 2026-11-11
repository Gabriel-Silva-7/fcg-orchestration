#!/usr/bin/env python3
"""Prepare a fresh, PRIVATE demo environment from explicit clean source checkouts."""
import argparse
import datetime
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import uuid
from urllib.parse import urlsplit, urlunsplit

OWNERS = ('users', 'catalog', 'payments', 'notifications')
ROOT = Path(__file__).resolve().parents[1]

def git(path, *args):
    run = subprocess.run(['git', '-C', str(path), *args], capture_output=True, text=True, timeout=15)
    if run.returncode: raise ValueError('Cannot inspect source checkout: '+str(path))
    return run.stdout.strip()

def prepare(args):
    output = args.output.resolve()
    default_env = ROOT/'.env'
    if args.compose_default and os.path.lexists(default_env):
        raise ValueError('.env already exists; reuse the existing environment or choose --output without --compose-default')
    sources = {}
    for owner in OWNERS:
        path = getattr(args, owner).resolve()
        if not (path/'Dockerfile').is_file(): raise ValueError(owner+' source has no Dockerfile at '+str(path)+'; provide --'+owner+' /path/to/clean/checkout')
        if git(path, 'status', '--porcelain'): raise ValueError(owner+' source must be clean; commit the reviewed increment first')
        origin = git(path, 'config', '--get', 'remote.origin.url')
        parsed = urlsplit(origin)
        if parsed.scheme and parsed.hostname:
            origin = urlunsplit((parsed.scheme, parsed.hostname, parsed.path, '', ''))
        sources[owner] = {'path': str(path), 'sha': git(path, 'rev-parse', 'HEAD'),
                          'tree': git(path, 'rev-parse', 'HEAD^{tree}'), 'origin': origin}
    # No overwrite, including an existing symlink/directory. Preserve keys and data of prior environments.
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    project = 'fcg-demo-'+uuid.uuid4().hex[:12]
    values = {'COMPOSE_PROJECT_NAME': project, 'POSTGRES_PASSWORD': secrets.token_hex(24),
              'POSTGRES_PORT':'0', 'RABBITMQ_PORT':'0', 'RABBITMQ_MANAGEMENT_PORT':'0',
              'RABBITMQ_BOOTSTRAP_USER':'fcg_bootstrap', 'RABBITMQ_BOOTSTRAP_PASSWORD': secrets.token_hex(24),
              'FCG_JWT_PUBLIC_KEY_PATH':str(output/'public.pem'), 'FCG_JWT_PRIVATE_KEY_PATH':str(output/'private.pem'),
              'FCG_JWT_ISSUER':'fcg-users-api', 'FCG_JWT_AUDIENCE':'fcg-clients',
              'ADMIN_BOOTSTRAP_ENABLED':'true', 'ADMIN_BOOTSTRAP_EMAIL':'administrator@example.com',
              'ADMIN_BOOTSTRAP_PASSWORD':secrets.token_hex(20)+'!Aa1',
              'ADMIN_BOOTSTRAP_PLAYER_EMAIL':'player@example.com',
              'ADMIN_BOOTSTRAP_PLAYER_PASSWORD':secrets.token_hex(20)+'!Aa1',
              'PAYMENT_APPROVAL_LIMIT':'500.00', 'PAYMENT_DELAY':'00:00:02', 'FCG_GSS_ENCRYPTION_MODE':'disable'}
    for owner in OWNERS:
        prefix='FCG_'+owner.upper()
        values.update({prefix+'_SOURCE':sources[owner]['path'], prefix+'_IMAGE':project+'-'+owner+':'+sources[owner]['sha'],
                       prefix+'_DB_PASSWORD':secrets.token_hex(24), prefix+'_RABBITMQ_USER':'fcg_'+owner,
                       prefix+'_RABBITMQ_PASSWORD':secrets.token_hex(24), prefix+'_PORT':'0'})
    for command in [ ['openssl','genpkey','-algorithm','RSA','-pkeyopt','rsa_keygen_bits:2048','-out',str(output/'private.pem')],
                     ['openssl','pkey','-in',str(output/'private.pem'),'-pubout','-out',str(output/'public.pem')]]:
        result = subprocess.run(command, capture_output=True, timeout=30)
        if result.returncode: raise ValueError('RSA key preparation failed; remove only the new incomplete environment after inspection')
    # Parent0700 protects on host; read-only mounts must be readable by runtime UID1654.
    (output/'private.pem').chmod(0o444); (output/'public.pem').chmod(0o444)
    def quoted(value):
        if any(c in value for c in '\r\n\0'): raise ValueError('Environment paths cannot contain newline/NUL')
        return "'"+value.replace("'", "\\'")+"'"
    envfile=output/'demo.env'
    with open(envfile, 'x', opener=lambda path, flags: os.open(path, flags, 0o600)) as stream:
        stream.write('\n'.join(key+'='+quoted(value) for key,value in values.items())+'\n')
    (output/'sources.json').write_text(json.dumps({'mode':'local-preparation-not-public-release', 'project':project,
        'at':datetime.datetime.now(datetime.timezone.utc).isoformat(), 'services':sources}, indent=2)+'\n')
    if args.compose_default:
        # Atomic creation refuses even a dangling symlink created after the preflight.
        default_env.symlink_to(envfile)
    print('Prepared private local environment: '+str(output))
    if args.compose_default:
        print('Default .env linked; from '+str(ROOT)+' run docker --context orbstack compose up --build.')
    else:
        print('Use --env-file '+str(envfile)+' with docker compose; no credentials were printed.')

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for owner in OWNERS: parser.add_argument('--'+owner, type=Path, default=ROOT.parent/('fcg-'+owner+'-api'))
    parser.add_argument('--output', type=Path, default=ROOT/'.local/demo')
    parser.add_argument('--compose-default', action='store_true', help='Create the ignored root .env link for docker compose up; never overwrite it')
    try: prepare(parser.parse_args())
    except (ValueError, OSError, subprocess.TimeoutExpired) as error:
        print('Preparation failed: '+str(error), file=sys.stderr); sys.exit(1)
