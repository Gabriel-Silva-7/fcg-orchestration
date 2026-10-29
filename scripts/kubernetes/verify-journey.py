#!/usr/bin/env python3
"""Complete Kind journeys, durable original purchase recovery and supervised access lifecycle."""
import argparse
import base64
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import urllib.request
import urllib.error
import uuid
from render import load_environment,ROOT,OWNERS
from runtime import Runtime,ensure,poll
from port_forward import PortForward
from journey import KubeJourney


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--rendered',type=Path,required=True);parser.add_argument('--tools',type=Path,required=True);parser.add_argument('--dotnet',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    ensure(not args.output.exists(),'Evidence already exists');runtime=Runtime(args.rendered,args.tools);up_attempted=False;result={'status':'failed','checks':{},'journeys':[]};journey=None;supervisor=None
    config,_=load_environment(Path(runtime.meta['env_file']),runtime.meta['docker_context'])
    def active(owner):
        return [p for p in runtime.get('pods','-l','app='+owner)['items'] if not p['metadata'].get('deletionTimestamp')]
    def ready_replacement(owner,uid):
        return poll(lambda:active(owner),lambda items:len(items)==1 and items[0]['metadata']['uid']!=uid and any(c['type']=='Ready' and c['status']=='True' for c in items[0]['status'].get('conditions',[])),owner+' new actual Ready pod',seconds=180)[0]
    def mgmt(port,path,method='GET',body=None):
        env=config['services']['rabbitmq']['environment'];request=urllib.request.Request('http://127.0.0.1:'+str(port)+path,data=json.dumps(body).encode() if body is not None else None,method=method,headers={'Content-Type':'application/json','Authorization':'Basic '+base64.b64encode((env['RABBITMQ_DEFAULT_USER']+':'+env['RABBITMQ_DEFAULT_PASS']).encode()).decode()})
        try:
            with urllib.request.urlopen(request,timeout=5) as response:return json.loads(response.read())
        except (OSError,urllib.error.URLError):return None
    def peek(port):
        values=mgmt(port,'/api/queues/%2Ffcg/fcg-payments-orders/get','POST',{'count':1,'ackmode':'ack_requeue_true','encoding':'auto','truncate':1000000});ensure(len(values)==1,'Expected original queued real order');return values[0]
    def counts():
        return {'users':journey.db('users','SELECT count(*) FROM "Users"'),'catalog':journey.db('catalog','SELECT (SELECT count(*) FROM "Orders")||\':\'||(SELECT count(*) FROM "LibraryEntries")'),'payments':journey.db('payments','SELECT count(*) FROM payment_decisions'),'notifications':journey.db('notifications','SELECT (SELECT count(*) FROM "NotificationIntents")||\':\'||(SELECT count(*) FROM "PaymentObservations")')}
    def owned_children_gone(history):
        for item in history:
            response=runtime.run(['ps','-p',str(item['pid']),'-o','command='],check=False)
            ensure(not (response.returncode==0 and str(runtime.kubeconfig) in response.stdout and 'port-forward' in response.stdout),'Owned forward remained orphaned')
    try:
        result['preflight']=runtime.preflight()
        up_attempted=True
        runtime.run([sys.executable,str(ROOT/'scripts/kubernetes/deploy.py'),'up','--rendered',str(runtime.directory),'--tools',str(args.tools)],timeout=300);runtime.attach_owned()
        result['checks']['documented_persistent_up_command_deploys_ready_stack']=True
        # Exercise the documented CLI, including its own process/forward lifecycle, not an in-memory substitute.
        for index in (1,2):
            output=runtime.directory/('full-journey-'+str(index)+'.json')
            runtime.run(['sh',str(ROOT/'scripts/e2e.sh'),'--target','k8s','--rendered',str(runtime.directory),'--tools',str(args.tools),'--output',str(output)],timeout=180)
            data=json.loads(output.read_text());ensure(data['status']=='passed' and data['mode']=='complete-four-real-services-kubernetes','Full CLI journey failed');result['journeys'].append(data)
        ensure(result['journeys'][0]['run_id']!=result['journeys'][1]['run_id'] and result['journeys'][0]['images']==result['journeys'][1]['images'],'Consecutive journeys reused IDs or changed runtime images')
        result['checks']['two_complete_cli_journeys_same_sources_images_distinct_ids']=True
        journey=KubeJourney(runtime,config);env=config['services']['users']['environment'];admin=journey.login(env['AdminBootstrap__Email'],env['AdminBootstrap__Password']);uid,user=journey.register('kind-recovery');journey.authorization_ready(user);journey.authorization_ready(admin);journey.notification('Welcome',uid)
        runtime.kube('scale','deployment/payments','--replicas=0');poll(lambda:runtime.get('pods','-l','app=payments')['items'],lambda pods:not pods,'Payments pods completely removed',seconds=90)
        code,game=journey.http('catalog','/api/v1/games','POST',{'title':'Kind recovery '+uuid.uuid4().hex,'basePrice':59.90},admin);ensure(code==201,'Recovery game creation failed')
        code,_=journey.http('catalog','/api/v1/games/'+game['id']+'/promotions','POST',{'discountPercentage':25,'startsAt':'2020-01-01T00:00:00Z','endsAt':'2090-01-01T00:00:00Z'},admin);ensure(code==201,'Recovery promotion failed')
        code,order=journey.http('catalog','/api/v1/me/library','POST',{'gameId':game['id']},user);ensure(code==202 and order['price']==Decimal('44.93'),'Original recovery order snapshot failed');oid=str(uuid.UUID(order['orderId']));result['recovery_order']={'order_id':oid,'phase':'accepted-with-payments-pods-absent'}
        pvc=runtime.get('pvc','rabbitmq-data')['metadata']['uid']
        with PortForward(runtime,'rabbitmq',15672) as forward:
            poll(lambda:mgmt(forward.port,'/api/overview'),bool,'broker authenticated Management HTTP ready')
            poll(lambda:mgmt(forward.port,'/api/queues/%2Ffcg/fcg-payments-orders').get('messages_ready',0),lambda n:n==1,'accepted original order confirmed in durable broker')
            original=peek(forward.port);ensure(original['properties'].get('delivery_mode')==2,'Original business event was not persistent');ensure(json.loads(original['payload'])['message']['orderId']==oid,'Wrong original order queued')
        old=active('rabbitmq')[0];runtime.kube('delete','pod',old['metadata']['name'],'--wait=true');new=ready_replacement('rabbitmq',old['metadata']['uid'])
        ensure(runtime.get('pvc','rabbitmq-data')['metadata']['uid']==pvc,'Broker PVC changed')
        nodename=runtime.kube('exec','deployment/rabbitmq','--','rabbitmqctl','eval','node().').stdout.strip().strip("'");ensure(nodename=='rabbit@rabbitmq','Broker node identity changed')
        with PortForward(runtime,'rabbitmq',15672) as forward:
            poll(lambda:mgmt(forward.port,'/api/overview'),bool,'replacement broker authenticated Management HTTP ready')
            restored=peek(forward.port);ensure(restored['payload']==original['payload'] and restored['properties']['message_id']==original['properties']['message_id'],'Original business message lost or changed')
        runtime.kube('scale','deployment/payments','--replicas=1');runtime.kube('rollout','status','deployment/payments','--timeout=180s');journey.connect()
        code,completed=poll(lambda:journey.http('catalog','/api/v1/me/orders/'+oid,token=user),lambda x:x[0]==200 and x[1]['status']!='Pending','same accepted order completes after broker recovery')
        ensure(code==200 and completed['status']=='Approved' and completed['price']==Decimal('44.93'),'Recovery re-created or changed original order');journey.notification('PurchaseConfirmation',oid)
        ensure(journey.db('catalog',f'''SELECT count(*) FROM "LibraryEntries" WHERE "OriginOrderId"='{oid}' ''')=='1','Recovery duplicated/lost acquisition')
        result['recovery_order']={'order_id':oid,'broker_node_name':nodename,'broker_pvc_uid':pvc,'message_id':original['properties']['message_id'],'payload_sha256':hashlib.sha256(original['payload'].encode()).hexdigest(),'old_broker_pod_uid':old['metadata']['uid'],'new_broker_pod_uid':new['metadata']['uid']};result['checks']['same_http_order_message_pvc_and_result_after_broker_pod_replacement']=True
        before=counts();pgpvc=runtime.get('pvc','postgres-data')['metadata']['uid'];old=active('postgres')[0];runtime.kube('delete','pod',old['metadata']['name'],'--wait=true');ready_replacement('postgres',old['metadata']['uid'])
        ensure(runtime.get('pvc','postgres-data')['metadata']['uid']==pgpvc,'Postgres PVC changed')
        journey.close()
        for owner in OWNERS:
            old=active(owner)[0];runtime.kube('delete','pod',old['metadata']['name'],'--wait=true');ready_replacement(owner,old['metadata']['uid'])
        journey.connect();ensure(counts()==before,'Database/work rows changed after actual pod replacements');ensure(journey.http('users','/api/v1/me',token=admin)[0]==200 and journey.http('catalog','/api/v1/me/orders/'+oid,token=user)[1]['status']=='Approved','Persisted keys/product data unavailable after replacement')
        result['checks']['postgres_and_four_api_replacements_preserve_keys_counts_and_product']=True;journey.close()
        # Port collision is handled before spawning any child, without closing the foreign listener.
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen();port=listener.getsockname()[1]
        common=[sys.executable,str(ROOT/'scripts/kubernetes/access.py'),'--rendered',str(runtime.directory),'--tools',str(args.tools)]
        try:
            negative=runtime.run(common+['--status-file',str(runtime.directory/'collision.json'),'--users-port',str(port),'--catalog-port','0','--payments-port','0','--notifications-port','0','--rabbitmq-port','0'],check=False,timeout=30)
            ensure(negative.returncode==1 and 'occupied' in negative.stderr,'Port collision was not rejected visibly')
            with socket.create_connection(('127.0.0.1',port)) as client:
                connection,_=listener.accept();connection.sendall(b'owner-preserved');ensure(client.recv(64)==b'owner-preserved','Foreign listener disturbed');connection.close()
        finally:listener.close()
        result['checks']['occupied_port_refused_without_disturbing_owner']=True
        status_file=runtime.directory/'supervised-access.json';command=common+['--status-file',str(status_file),'--users-port','0','--catalog-port','0','--payments-port','0','--notifications-port','0','--rabbitmq-port','0']
        supervisor=subprocess.Popen(command,env=runtime.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        def status():
            value=json.loads(status_file.read_text()) if status_file.exists() else {}
            ensure(value.get('state')!='failed','Supervisor failed within its bounded recovery policy')
            return value
        started=poll(status,lambda data:data.get('state')=='ready','supervised HTTP and authenticated Management readiness',seconds=120)
        oldpid=started['children']['users'];oldport=started['ports']['users'];old=active('users')[0];runtime.kube('delete','pod',old['metadata']['name'],'--wait=true');ready_replacement('users',old['metadata']['uid'])
        recovered=poll(status,lambda data:data.get('state')=='ready' and data['children'].get('users')!=oldpid,'same loopback port supervised reconnection',seconds=120);ensure(recovered['ports']['users']==oldport,'Supervisor changed local client address')
        supervisor.send_signal(signal.SIGTERM);supervisor.wait(timeout=20);ensure(supervisor.returncode==0,'Supervisor shutdown failed');stopped=status();ensure(stopped['state']=='stopped','Supervisor did not record graceful stop');owned_children_gone(stopped['history']);supervisor.stdout.close();supervisor.stderr.close();supervisor=None
        result['checks']['supervisor_reselects_ready_service_pod_after_replacement_and_leaves_no_orphans']=True
        # Exhaustion is visible and bounded. Restore the deployment afterwards; do not reset data.
        runtime.kube('scale','deployment/notifications','--replicas=0');poll(lambda:runtime.get('pods','-l','app=notifications')['items'],lambda pods:not pods,'Notifications pods completely removed for bounded access failure',seconds=90)
        fail_file=runtime.directory/'bounded-access.json';failed=runtime.run(common+['--status-file',str(fail_file),'--users-port','0','--catalog-port','0','--payments-port','0','--notifications-port','0','--rabbitmq-port','0','--recovery-seconds','5','--max-attempts','2'],check=False,timeout=30)
        ensure(failed.returncode==1 and json.loads(fail_file.read_text())['state']=='failed','Access exhaustion did not fail visibly');owned_children_gone(json.loads(fail_file.read_text())['history'])
        runtime.kube('scale','deployment/notifications','--replicas=1');runtime.kube('rollout','status','deployment/notifications','--timeout=180s');journey.connect();journey.private_logs();result['checks']['bounded_access_failure_visible_no_orphans_and_service_restored']=True
        for owner in OWNERS:ensure(runtime.kube('exec','deployment/diagnostic','--','wget','-qO-','http://'+owner+'/health/ready').stdout.strip()=='Healthy','Service DNS unavailable after recovery')
        result['checks']['four_internal_services_ready_after_all_recovery_cases']=True;result['images']=journey.deployment_evidence();result['sources']=runtime.meta['sources']
        node=runtime.get('nodes')['items'][0]['metadata']['name'];summary=json.loads(runtime.kube('get','--raw','/api/v1/nodes/'+node+'/proxy/stats/summary').stdout)
        result['post_journey_resource_snapshot']={'node_working_set_bytes':summary['node']['memory']['workingSetBytes'],'pods':{pod['podRef']['name']:{'working_set_bytes':pod.get('memory',{}).get('workingSetBytes'),'cpu_nano_cores':pod.get('cpu',{}).get('usageNanoCores')} for pod in summary['pods'] if pod['podRef']['namespace']==runtime.namespace},'scope':'One sample after journey/recovery, not a peak-load or capacity guarantee'}
        result['status']='passed'
    except Exception as error:
        result['error_type']=type(error).__name__
        if journey and 'recovery_order' in result:
            try:
                target=result['recovery_order']['order_id']
                result['failure_diagnostic']={'order_status':journey.db('catalog',f'''SELECT "Status" FROM "Orders" WHERE "Id"='{target}' '''),'payment_decisions':journey.db('payments',f"SELECT count(*) FROM payment_decisions WHERE order_id='{target}'")}
            except Exception:result['failure_diagnostic']={'unavailable':True}
        print('Kind journey failed: '+type(error).__name__,file=sys.stderr);raise
    finally:
        if supervisor:
            if supervisor.poll() is None:
                supervisor.send_signal(signal.SIGTERM)
                try:supervisor.wait(timeout=25)
                except subprocess.TimeoutExpired:supervisor.kill();supervisor.wait(timeout=5)
            supervisor.stdout.close();supervisor.stderr.close()
        if journey:journey.close()
        try:
            if up_attempted and runtime.node is None and runtime.ownership_file.exists():
                recorded=json.loads(runtime.ownership_file.read_text())
                if runtime.run(runtime.docker+['inspect',recorded['node']['id']],check=False).returncode==0:runtime.attach_owned()
            if runtime.node:
                result['cluster_owner']=runtime.node.copy()
                runtime.run([sys.executable,str(ROOT/'scripts/kubernetes/deploy.py'),'down','--rendered',str(runtime.directory),'--tools',str(args.tools),'--delete-owned-cluster'],timeout=150)
                runtime.node=None
                result['checks']['documented_down_command_deletes_only_recorded_cluster']=True
            result['cleanup']=runtime.cleanup()
        finally:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({'status':result['status'],'checks':list(result['checks']),'cleanup_complete':result.get('cleanup',{}).get('complete',False)}))

if __name__=='__main__':main()
