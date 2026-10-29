#!/usr/bin/env python3
"""Validate service-owned manifests, real Kind hosts and public config rollouts."""
import argparse
from decimal import Decimal
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from render import render,verify_rendered,load_environment,OWNERS
from runtime import Runtime,RuntimeFailure,ensure,poll
from journey import KubeJourney


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--rendered',type=Path,required=True);parser.add_argument('--tools',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    ensure(not args.output.exists(),'Evidence exists');runtime=Runtime(args.rendered,args.tools);result={'status':'failed','checks':{},'scope':'four-applications-and-config-rollout'};journey=None
    config,_=load_environment(Path(runtime.meta['env_file']),runtime.meta['docker_context'])
    original_keys={v['target']:Path(v['source']).read_bytes() for v in config['services']['users']['volumes'] if v['target'].startswith('/run/secrets/')}
    def pods():
        values={}
        for owner in OWNERS:
            active=[x for x in runtime.get('pods','-l','app='+owner)['items'] if not x['metadata'].get('deletionTimestamp') and any(c['type']=='Ready' and c['status']=='True' for c in x['status'].get('conditions',[]))]
            ensure(len(active)==1,'Expected one active Ready service pod');values[owner]=active[0]
        return values
    def same_credentials(before,after):
        for owner in OWNERS:
            for key,value in before['services'][owner]['environment'].items():
                if any(marker in key.lower() for marker in ('password','connectionstrings','email','username')):ensure(value==after['services'][owner]['environment'].get(key),'Public override cannot rotate credentials')
        for before_volume in before['services']['users']['volumes']:
            if before_volume['target'].startswith('/run/secrets/'):
                after_volume=next(v for v in after['services']['users']['volumes'] if v['target']==before_volume['target'])
                ensure(original_keys[before_volume['target']]==Path(after_volume['source']).read_bytes(),'Public override cannot replace persisted keys')
    try:
        ensure('application_sources' in runtime.meta,'Render with --applications')
        # A semantically different aggregate must be rejected before reaching kubectl.
        target=runtime.directory/'04-applications/payments-configmap.yaml';original=target.read_text()
        try:
            target.write_text(re.sub(r'(?m)^(  PaymentSimulation__ApprovalLimit: ).*$',r'\g<1>"123.45"',original))
            try:verify_rendered(runtime.directory)
            except ValueError:result['checks']['divergent_aggregate_rejected_before_apply']=True
            else:raise RuntimeFailure('Divergent aggregate was accepted')
        finally:target.write_text(original);target.chmod(0o600)
        result['preflight']=runtime.preflight();infra_archive=runtime.build_infrastructure();app_archive=runtime.build_applications();runtime.create(infra_archive);runtime.load_archive(app_archive)
        runtime.apply_phase('00-namespace')
        try:runtime.apply_phase('04-applications')
        except RuntimeFailure as error:
            ensure('Topology bootstrap must Complete' in str(error),'Unexpected initial application gate failure')
            ensure(not runtime.get('deployments')['items'],'Producer was applied before topology gate');result['checks']['producer_phase_denied_before_bootstrap_complete']=True
        else:raise RuntimeFailure('Producer gate allowed missing bootstrap')
        runtime.infrastructure();runtime.applications();runtime.apply_phase('03-diagnostics');runtime.kube('rollout','status','deployment/diagnostic','--timeout=120s')
        initial=pods();result['initial_pods']={owner:value['metadata']['uid'] for owner,value in initial.items()}
        for owner in OWNERS:
            ensure(runtime.kube('exec','deployment/'+owner,'--','id','-u').stdout.strip()=='1654','Service runtime UID differs')
            ensure(runtime.kube('exec','deployment/diagnostic','--','wget','-qO-','http://'+owner+'/health/ready').stdout.strip()=='Healthy','HTTP Service DNS readiness failed')
            reference=runtime.meta['images'][owner];image=json.loads(runtime.run(runtime.docker+['exec',runtime.node['id'],'crictl','inspecti',reference]).stdout);artifact=runtime.image_records[reference]
            ensure(image['status']['id']==artifact['exported_config_digest'] and image['info']['imageSpec']['rootfs']==artifact['rootfs'] and image['info']['imageSpec']['config'].get('Entrypoint')==artifact['entrypoint'],'App runtime image chain differs')
        ensure(runtime.kube('exec','deployment/catalog','--','test','!','-e','/run/secrets/private.pem',check=False).returncode==0,'Catalog received private signing key')
        result['checks']['four_ready_deployments_uid_services_dns_and_artifact_chain']=True;result['checks']['private_signing_key_only_users']=True
        journey=KubeJourney(runtime,config);result['slice']=journey.original_slice();old_admin=journey.admin;
        original_payments={oid:journey.db('payments',f'''SELECT "Payload" FROM "Results" WHERE "OrderId"='{oid}' ''') for oid in result['slice']['orders'].values()};result['checks']['real_registration_purchases_and_notifications']=True
        # Full, explicit re-render/apply forces new refs/Pods, retaining the same credentials, keys and PVCs.
        revisions=[]
        for limit,expected in [('700.00','Approved'),('500.00','Rejected')]:
            before=pods();oldmeta=runtime.meta;env_file=Path(oldmeta['env_file']);newenv=env_file.parent/('limit-'+limit.replace('.','-')+'.env')
            text=re.sub(r'(?m)^PAYMENT_APPROVAL_LIMIT=.*$',"PAYMENT_APPROVAL_LIMIT='"+limit+"'",env_file.read_text());ensure(not newenv.exists(),'Override environment exists');newenv.write_text(text);newenv.chmod(0o600)
            directory=env_file.parent/('rendered-limit-'+limit.replace('.','-'));newmeta=render(SimpleNamespace(env_file=newenv,output=directory,docker_context=oldmeta['docker_context'],applications=True));updated,_=load_environment(newenv,oldmeta['docker_context']);same_credentials(config,updated)
            ensure(newmeta['namespace']==runtime.namespace and newmeta['images']==oldmeta['images'],'Config-only override changed source/image identity')
            journey.close();runtime.directory=directory;runtime.meta=newmeta;runtime.infrastructure();runtime.applications();journey.connect();after=pods()
            ensure(all(before[owner]['metadata']['uid']!=after[owner]['metadata']['uid'] for owner in OWNERS),'Changed config references did not roll service pods')
            actual=runtime.kube('exec','deployment/payments','--','printenv','PaymentSimulation__ApprovalLimit').stdout.strip();ensure(actual==limit,'Effective Pod environment differs from override')
            ensure(journey.http('users','/api/v1/me',token=old_admin)[0]==200 and journey.http('catalog','/api/v1/me/orders',token=old_admin)[0]==200,'Persisted key compatibility lost across rollout')
            for label,original_oid in result['slice']['orders'].items():
                status,order=journey.http('catalog','/api/v1/me/orders/'+original_oid,token=journey.user)
                ensure(status==200 and order['status']==('Approved' if label=='approved' else 'Rejected'),'Config rollout lost an original order')
                ensure(journey.db('payments',f'''SELECT "Payload" FROM "Results" WHERE "OrderId"='{original_oid}' ''')==original_payments[original_oid],'Config rollout changed an original payment')
                if label=='approved':
                    status,library=journey.http('catalog','/api/v1/me/library',token=journey.user)
                    ensure(status==200 and any(x['gameId']==order['gameId'] and x['acquisitionPrice']==Decimal('44.93') for x in library['items']),'Config rollout lost original acquisition snapshot')
                    ensure(journey.db('notifications',f'''SELECT count(*) FROM "NotificationIntents" WHERE "Kind"='PurchaseConfirmation' AND "BusinessId"='{original_oid}' AND "State"='Sent' ''')=='1','Config rollout lost Sent notification')
                    ensure(not any('[E-MAIL SIMULADO]' in line and original_oid in line for line in journey.logs('notifications').splitlines()),'Config rollout re-emitted an already Sent original')
            oid=journey.control(600,expected,'limit-'+limit)
            revisions.append({'revision':newmeta['revision'],'approval_limit':limit,'control_order_id':oid,'expected':expected,'pods':{owner:value['metadata']['uid'] for owner,value in after.items()}})
        stable=pods();generations={owner:runtime.get('deployment',owner)['metadata']['generation'] for owner in OWNERS};runtime.applications();ensure(generations=={owner:runtime.get('deployment',owner)['metadata']['generation'] for owner in OWNERS},'Unchanged package changed Deployment generation');ensure({o:p['metadata']['uid'] for o,p in stable.items()}=={o:p['metadata']['uid'] for o,p in pods().items()},'Unchanged package unexpectedly replaced service Pods')
        journey.private_logs();result['revisions']=revisions;result['images']=runtime.image_records;result['sources']=runtime.meta['sources'];result['checks']['changed_config_rollout_retains_keys_credentials_and_business_data']=True;result['checks']['same_final_package_idempotent_and_private_logs']=True;result['status']='passed'
    except Exception as error:result['error_type']=type(error).__name__;print('Application proof failed: '+type(error).__name__,file=sys.stderr);raise
    finally:
        if journey:journey.close()
        try:result['cleanup']=runtime.cleanup()
        finally:
            result['cluster']=runtime.name;result['namespace']=runtime.namespace
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':result['status'],'checks':list(result['checks']),'cleanup_complete':result.get('cleanup',{}).get('complete',False)}))

if __name__=='__main__':main()
