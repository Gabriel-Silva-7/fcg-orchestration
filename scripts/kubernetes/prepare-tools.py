#!/usr/bin/env python3
"""Install the exercised official tool versions into an explicit local directory."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sys
import urllib.request


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    target=platform.system().lower()+'-'+{'aarch64':'arm64','arm64':'arm64','x86_64':'amd64'}.get(platform.machine(),platform.machine())
    versions=json.loads(Path(__file__).with_name('versions.json').read_text());args.output.mkdir(parents=True,exist_ok=True);reports={}
    for name in ('kind','kubectl'):
        if target not in versions[name]:raise ValueError('Unexercised platform; this toolchain currently supports darwin-arm64')
        selected=versions[name][target];path=args.output/name
        with urllib.request.urlopen(selected['checksumUrl'],timeout=30) as response:published=response.read().decode().strip().split()[0]
        if published!=selected['sha256']:raise ValueError('Official published checksum differs from pinned version')
        if path.exists():data=path.read_bytes()
        else:
            with urllib.request.urlopen(selected['url'],timeout=30) as response:data=response.read()
        if hashlib.sha256(data).hexdigest()!=published:raise ValueError('Tool checksum mismatch; existing files were not overwritten')
        if not path.exists():path.write_bytes(data)
        path.chmod(0o755);reports[name]={'version':versions[name]['version'],'platform':target,'url':selected['url'],'sha256':published,'bytes':len(data)}
    (args.output/'TOOLS.json').write_text(json.dumps(reports,indent=2)+'\n');print('Verified local kind/kubectl: '+str(args.output))

if __name__=='__main__':
    try:main()
    except Exception as error:print('Tool preparation failed: '+type(error).__name__,file=sys.stderr);sys.exit(1)
