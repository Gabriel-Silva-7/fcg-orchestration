#!/usr/bin/env python3
"""Verify real Kind infrastructure, isolation and persisted data in an owned disposable cluster."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request
import uuid
from render import load_environment, OWNERS, ROOT
from runtime import Runtime, ensure, poll
from port_forward import PortForward


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--rendered',type=Path,required=True);parser.add_argument('--tools',type=Path,required=True);parser.add_argument('--dotnet',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    ensure(not args.output.exists(),'Evidence already exists');runtime=Runtime(args.rendered,args.tools);result={'status':'failed','checks':{},'scope':'infrastructure-only-not-four-service-journey'}
    config,_=load_environment(Path(runtime.meta['env_file']),runtime.meta['docker_context'])
    def pod(owner):
        values=runtime.get('pods','-l','app='+owner)['items'];ensure(len(values)==1,'Expected one owned pod');return values[0]
    def replacement(owner,old_uid):
        values=poll(lambda:runtime.get('pods','-l','app='+owner)['items'],lambda items:len(items)==1 and items[0]['metadata']['uid']!=old_uid and any(c['type']=='Ready' and c['status']=='True' for c in items[0]['status'].get('conditions',[])),owner+' replacement pod actually Ready',seconds=180)
        return values[0]
    def db(owner,target,query,check=True):
        variable='FCG_'+owner.upper()+'_DB_PASSWORD'
        return runtime.kube('exec','deployment/postgres','--','bash','-c','export PGPASSWORD="${!1}"; shift; exec psql "$@"','bash',variable,'-h','postgres','-U','fcg_'+owner,'-d',target,'-XAt','-v','ON_ERROR_STOP=1','-c',query,check=check)
    def probe(command,port,**extra):
        settings=config['services']['payments']['environment'];env={**runtime.env,'DOTNET_ROOT':str(args.dotnet.parent),'AMQP_HOST':'127.0.0.1','AMQP_PORT':str(port),'AMQP_VHOST':'/fcg','AMQP_USERNAME':settings['RabbitMq__Username'],'AMQP_PASSWORD':settings['RabbitMq__Password'],**extra}
        response=subprocess.run([str(args.dotnet),str(ROOT/'tests/rabbitmq/Probes/bin/Release/net10.0/RabbitMq.Probes.dll'),command],env=env,capture_output=True,text=True,timeout=30)
        ensure(response.returncode==0 and not response.stderr,'Owned AMQP probe failed');return json.loads(response.stdout)
    try:
        result['preflight']=runtime.preflight()
        for command in ('restore','build'):
            options=['--locked-mode'] if command=='restore' else ['-c','Release','--no-restore']
            runtime.run([str(args.dotnet),command,str(ROOT/'tests/rabbitmq/Probes/RabbitMq.Probes.csproj'),*options])
        archive=runtime.build_infrastructure();versions=runtime.create(archive);runtime.infrastructure()
        result['versions']={'client':versions['clientVersion']['gitVersion'],'server':versions['serverVersion']['gitVersion']}
        storage=runtime.get('storageclass')['items'];ensure(any(x.get('volumeBindingMode')=='WaitForFirstConsumer' for x in storage),'Expected delayed-binding storage class')
        pvcs={x['metadata']['name']:{'uid':x['metadata']['uid'],'volume':x['spec']['volumeName']} for x in runtime.get('pvc')['items']}
        ensure(set(pvcs)=={'postgres-data','rabbitmq-data'},'Persistent claims incomplete')
        ensure({x['metadata']['name'] for x in runtime.get('deployments')['items']}=={'postgres','rabbitmq'},'A producer was applied before the topology gate')
        result['checks']['infra_deployments_pvc_bound_and_bootstrap_gate']=True
        for owner in OWNERS:
            ensure(db(owner,'fcg_'+owner,'SELECT current_user').stdout.strip()=='fcg_'+owner,'Own role cannot connect through Service DNS')
            for target in ['fcg_'+x for x in OWNERS if x!=owner]+['postgres','template1']:
                ensure(db(owner,target,'SELECT 1',False).returncode!=0,'Cross-database login allowed')
        result['checks']['four_own_roles_and_twenty_forbidden_connections']=True
        marker=uuid.uuid4().hex;db('users','fcg_users',"CREATE TABLE k8s_infrastructure_probe(value text PRIMARY KEY); INSERT INTO k8s_infrastructure_probe VALUES ('"+marker+"')")
        old=pod('postgres');runtime.kube('delete','pod',old['metadata']['name'],'--wait=true')
        runtime.kube('rollout','status','deployment/postgres','--timeout=180s');new=replacement('postgres',old['metadata']['uid'])
        ensure(old['metadata']['uid']!=new['metadata']['uid'],'Postgres pod was not replaced')
        ensure(runtime.get('pvc','postgres-data')['metadata']['uid']==pvcs['postgres-data']['uid'],'Postgres PVC changed')
        ensure(db('users','fcg_users','SELECT value FROM k8s_infrastructure_probe').stdout.strip()==marker,'Own diagnostic record lost across pod replacement')
        result['checks']['postgres_same_pvc_and_record_after_real_pod_replacement']={'before':old['metadata']['uid'],'after':new['metadata']['uid'],'pvc_uid':pvcs['postgres-data']['uid']}
        body=json.dumps({'kind':'infrastructure-marker','id':uuid.uuid4().hex});mid=str(uuid.uuid4())
        with PortForward(runtime,'rabbitmq',5672) as forward:
            ensure(probe('publish-confirm',forward.port,MESSAGE_ID=mid,MESSAGE_BODY=body,EXCHANGE='FCG.Contracts:OrderPlacedEvent')['confirmed'],'Persistent message was not confirmed')
            ensure(probe('counts',forward.port)['queues']['fcg-payments-orders']==1,'Confirmed message missing before replacement')
        old=pod('rabbitmq');runtime.kube('delete','pod',old['metadata']['name'],'--wait=true')
        runtime.kube('rollout','status','deployment/rabbitmq','--timeout=180s');new=replacement('rabbitmq',old['metadata']['uid'])
        ensure(old['metadata']['uid']!=new['metadata']['uid'],'Broker pod was not replaced')
        ensure(runtime.get('pvc','rabbitmq-data')['metadata']['uid']==pvcs['rabbitmq-data']['uid'],'Broker PVC changed')
        nodename=runtime.kube('exec','deployment/rabbitmq','--','rabbitmqctl','eval','node().').stdout.strip().strip("'")
        result['broker_node_name']=nodename;ensure(nodename=='rabbit@rabbitmq','Stable broker node identity changed')
        with PortForward(runtime,'rabbitmq',5672) as forward:
            ensure(probe('counts',forward.port)['queues']['fcg-payments-orders']==1,'Persistent message disappeared after replacement')
            actual=probe('get-ack',forward.port,QUEUE='fcg-payments-orders')
            ensure(actual['body']==body and actual['messageId']==mid and actual['persistent'],'Retained AMQP body/properties differ')
        result['checks']['broker_same_pvc_node_and_confirmed_original_message']={'before':old['metadata']['uid'],'after':new['metadata']['uid'],'pvc_uid':pvcs['rabbitmq-data']['uid'],'message_id':mid,'body_sha256':hashlib.sha256(body.encode()).hexdigest()}
        # Reapply exactly the same final package; never overwrite it with source placeholders.
        runtime.apply_phase('01-infrastructure');runtime.apply_phase('02-bootstrap')
        for name,record in pvcs.items():ensure(runtime.get('pvc',name)['metadata']['uid']==record['uid'],'Idempotent apply changed persistent claim')
        runtime.apply_phase('03-diagnostics');runtime.kube('rollout','status','deployment/diagnostic','--timeout=120s')
        for service in ('postgres','rabbitmq'):
            dns=runtime.kube('exec','deployment/diagnostic','--','nslookup','-type=A',service+'.'+runtime.namespace+'.svc.cluster.local.',check=False)
            result.setdefault('dns',{})[service]={'exit_code':dns.returncode,'output':dns.stdout}
            ensure(dns.returncode==0 and runtime.get('service',service)['spec']['clusterIP'] in dns.stdout,'DNS did not resolve the actual Service address')
        runtime.kube('exec','deployment/diagnostic','--','wget','-q','--spider','http://rabbitmq:15672')
        result['checks']['isolated_diagnostic_deployment_dns_and_http_service']=True
        observed={}
        for owner in ('postgres','rabbitmq','diagnostic','bootstrap'):
            p=pod('rabbitmq-bootstrap' if owner=='bootstrap' else owner);reference=runtime.meta['images'][owner];image=json.loads(runtime.run(runtime.docker+['exec',runtime.node['id'],'crictl','inspecti',reference]).stdout)
            record=runtime.image_records[reference]
            ensure(image['status']['id']==record['exported_config_digest'],'Runtime image/config digest differs from exported platform')
            ensure(image['info']['imageSpec']['rootfs']==record['rootfs'] and image['info']['imageSpec']['config'].get('Entrypoint')==record['entrypoint'],'Runtime rootfs/entrypoint differs')
            observed[owner]={'pod_uid':p['metadata']['uid'],'pod_image_id':p['status']['containerStatuses'][0]['imageID'],'runtime_config_digest':image['status']['id']}
        result['runtime_images']=observed;result['images']=runtime.image_records;result['pvcs']=pvcs
        result['checks']['source_exported_config_to_kind_runtime_chain']=True
        result['broker_process_uid']=runtime.kube('exec','deployment/rabbitmq','--','sh','-c',"awk '/^Uid:/ {print $2}' /proc/1/status").stdout.strip()
        ensure(result['broker_process_uid']!='0','Broker process did not drop root as its entrypoint specifies')
        result['checks']['supported_broker_volume_permissions_and_process_uid']=True
        result['status']='passed'
    except Exception as error:
        result['error_type']=type(error).__name__;print('Infrastructure proof failed: '+type(error).__name__,file=sys.stderr);raise
    finally:
        try:result['cleanup']=runtime.cleanup()
        finally:
            result['namespace']=runtime.namespace;result['cluster']=runtime.name;result['source_manifest']=runtime.meta
            args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({'status':result['status'],'checks':list(result['checks']),'cleanup_complete':result.get('cleanup',{}).get('complete',False)}))

if __name__=='__main__':main()
