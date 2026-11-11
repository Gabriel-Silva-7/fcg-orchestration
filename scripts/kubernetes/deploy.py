#!/usr/bin/env python3
"""Create a fresh owned local Kind stack, or explicitly delete that recorded cluster."""
import argparse
import json
from pathlib import Path
import sys
from render import verify_rendered
from runtime import Runtime,ensure,RuntimeFailure


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('operation',choices=['up','down']);parser.add_argument('--rendered',type=Path,required=True);parser.add_argument('--tools',type=Path,required=True);parser.add_argument('--delete-owned-cluster',action='store_true')
    args=parser.parse_args();verify_rendered(args.rendered);runtime=Runtime(args.rendered,args.tools)
    if args.operation=='down':
        ensure(args.delete_owned_cluster,'Deleting the recorded cluster requires --delete-owned-cluster')
        runtime.attach_owned();result=runtime.cleanup();print(json.dumps({'state':'deleted','cluster':runtime.name,'cleanup_complete':result['complete']}));return
    ensure(not args.delete_owned_cluster,'Deletion flag is only valid for down')
    ensure('application_sources' in runtime.meta,'Render the complete stack with --applications')
    try:
        preflight=runtime.preflight();infra=runtime.build_infrastructure();apps=runtime.build_applications();versions=runtime.create(infra);runtime.load_archive(apps);runtime.infrastructure();runtime.applications();runtime.apply_phase('03-diagnostics');runtime.kube('rollout','status','deployment/diagnostic','--timeout=120s')
        evidence={'state':'ready','cluster':runtime.name,'namespace':runtime.namespace,'context':runtime.context,'preflight':preflight,'images':runtime.image_records,'versions':{'client':versions['clientVersion']['gitVersion'],'server':versions['serverVersion']['gitVersion']},'sources':runtime.meta['sources']}
        (runtime.directory/'LIVE.json').write_text(json.dumps(evidence,indent=2)+'\n');(runtime.directory/'LIVE.json').chmod(0o600)
        print(json.dumps({'state':'ready','cluster':runtime.name,'namespace':runtime.namespace,'kubeconfig':str(runtime.kubeconfig),'next':'Start access.py or scripts/e2e.sh --target k8s'}))
    except Exception:
        runtime.cleanup();raise

if __name__=='__main__':
    try:main()
    except Exception as error:print('Owned deployment failed: '+(str(error) if isinstance(error,RuntimeFailure) else type(error).__name__),file=sys.stderr);sys.exit(1)
