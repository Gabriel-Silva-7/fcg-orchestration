"""Real product HTTP assertions against four Services with owned port-forwards."""
from decimal import Decimal
import json
import hashlib
import tarfile
from types import SimpleNamespace
import sys
import urllib.parse
import uuid
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from e2e import Journey, OWNERS, poll, ensure
from port_forward import PortForward

class KubeJourney(Journey):
    def __init__(self,runtime,config):
        self.runtime=runtime;self.config=config;self.services=config['services'];self.run_id=uuid.uuid4().hex;self.accounts={};self.secrets=[];self.forwards=[];self.urls={};self.project=runtime.name;self.execution_mode='complete-four-real-services-kubernetes';self.validation_limits='Owned local Kind; no HA/volume-loss guarantee. Recovery and forward lifecycle are separate recorded checks.';self.args=SimpleNamespace(output=None,docker_context=runtime.meta['docker_context'])
        for service in self.services.values():
            for key,value in service.get('environment',{}).items():
                if value and any(part in key.lower() for part in ('password','connectionstrings','email')):self.secrets.append(value)
        self.connect()
    def connect(self):
        self.close()
        try:
            for owner in OWNERS:
                forward=PortForward(self.runtime,owner,80);self.forwards.append(forward);self.urls[owner]='http://127.0.0.1:'+str(forward.port)
                poll(lambda:self.http(owner,'/health/ready')[0],lambda code:code==200,owner+' forwarded real readiness')
        except Exception:self.close();raise
    def close(self):
        for forward in reversed(self.forwards):forward.close()
        self.forwards=[]
    def db(self,owner,sql):
        response=self.runtime.kube('exec','deployment/postgres','--','bash','-c','export PGPASSWORD="${!1}"; shift; exec psql "$@"','bash','FCG_'+owner.upper()+'_DB_PASSWORD','-h','postgres','-U','fcg_'+owner,'-d','fcg_'+owner,'-XAt','-v','ON_ERROR_STOP=1','-c','BEGIN READ ONLY; '+sql+'; COMMIT')
        return response.stdout.removeprefix('BEGIN\n').removesuffix('COMMIT\n').strip()
    def logs(self,owner):
        active=[pod for pod in self.runtime.get('pods','-l','app='+owner)['items'] if not pod['metadata'].get('deletionTimestamp') and pod['status'].get('phase')=='Running']
        ensure(len(active)==1,'Expected one current Running pod for log correlation')
        return self.runtime.kube('logs',active[0]['metadata']['name']).stdout
    def topology_gate(self):
        namespace=self.runtime.get('namespace',self.runtime.namespace)
        ensure(namespace['metadata']['labels'].get('io.fcg.run')==self.runtime.meta['run_id'],'Namespace belongs to another environment')
        job=self.runtime.get('job',self.runtime.meta['bootstrap_job'])
        ensure(any(x['type']=='Complete' and x['status']=='True' for x in job.get('status',{}).get('conditions',[])),'Topology Job must have completed')

    def deployment_evidence(self):
        expected={}
        for archive in ('infrastructure-images.tar','application-images.tar'):
            with tarfile.open(self.runtime.directory/archive) as saved:
                for item in json.load(saved.extractfile('manifest.json')):
                    content=saved.extractfile(item['Config']).read();configuration=json.loads(content)
                    for tag in item['RepoTags']:expected[tag]={'digest':'sha256:'+hashlib.sha256(content).hexdigest(),'config':configuration}
        node_name=self.runtime.get('nodes')['items'][0]['metadata']['name']
        node=json.loads(self.runtime.run(self.runtime.docker+['inspect',node_name]).stdout)[0]
        ensure(node['Config']['Labels'].get('io.x-k8s.kind.cluster')==self.runtime.name,'Docker/Kind node environment differs')
        images={}
        for owner in (*OWNERS,'postgres','rabbitmq','diagnostic','bootstrap'):
            label='rabbitmq-bootstrap' if owner=='bootstrap' else owner
            pods=self.runtime.get('pods','-l','app='+label)['items'];active=[pod for pod in pods if not pod['metadata'].get('deletionTimestamp')]
            ensure(len(active)==1,'Unexpected workload count while recording images')
            pod=active[0];reference=self.runtime.meta['images'][owner]
            ensure(pod['spec']['containers'][0]['image']==reference,'Pod reference differs from prepared source')
            if owner in OWNERS:
                ensure(pod['metadata']['annotations']['io.fcg.source-sha']==self.runtime.meta['sources'][owner]['sha'] and pod['metadata']['annotations']['io.fcg.render-revision']==self.runtime.meta['revision'],'App source/configuration revision is mixed')
            artifact=expected[reference];actual=json.loads(self.runtime.run(self.runtime.docker+['exec',node['Id'],'crictl','inspecti',reference]).stdout)
            ensure(actual['status']['id']==artifact['digest'] and actual['info']['imageSpec']['rootfs']==artifact['config']['rootfs'],'Runtime image differs from exported platform artifact')
            images[owner]={'pod_uid':pod['metadata']['uid'],'reference':reference,'pod_image_id':pod['status']['containerStatuses'][0]['imageID'],'exported_config_digest':artifact['digest'],'runtime_config_digest':actual['status']['id']}
        return images

    def original_slice(self):
        env=self.services['users']['environment'];self.admin=self.login(env['AdminBootstrap__Email'],env['AdminBootstrap__Password']);uid,user=self.register('kubernetes')
        self.user=user;self.authorization_ready(self.admin);self.authorization_ready(user);self.notification('Welcome',uid);orders={}
        for label,price,expected in [('approved',59.90,'Approved'),('rejected',600,'Rejected')]:
            status,game=self.http('catalog','/api/v1/games','POST',{'title':'Kind '+self.run_id+' '+label,'basePrice':price},self.admin);ensure(status==201,'Real Users administrator failed to create Catalog game')
            if label=='approved':
                status,_=self.http('catalog','/api/v1/games/'+game['id']+'/promotions','POST',{'discountPercentage':25,'startsAt':'2020-01-01T00:00:00Z','endsAt':'2090-01-01T00:00:00Z'},self.admin);ensure(status==201,'Real promotion failed')
            status,order=self.http('catalog','/api/v1/me/library','POST',{'gameId':game['id']},user);ensure(status==202,'Real HTTP purchase was not accepted')
            oid=str(uuid.UUID(order['orderId']));orders[label]=oid
            status,completed=poll(lambda:self.http('catalog','/api/v1/me/orders/'+oid,token=user),lambda x:x[0]==200 and x[1]['status']!='Pending','Kind original '+expected)
            selected=Decimal('44.93') if label=='approved' else Decimal('600')
            ensure(status==200 and completed['status']==expected and completed['price']==selected,'Order outcome/price differs from actual purchase')
            result=json.loads(self.db('payments',f'''SELECT "Payload" FROM "Results" WHERE "OrderId"='{oid}' '''),parse_float=Decimal)
            ensure(result['status']==expected and result['price']==selected,'Payments differs from original snapshot')
            poll(lambda:self.db('notifications',f'''SELECT "Status" FROM "PaymentObservations" WHERE "OrderId"='{oid}' '''),lambda value:value==expected,'independent Kind Notifications result')
            if expected=='Approved':self.notification('PurchaseConfirmation',oid)
            else:ensure(self.db('notifications',f'''SELECT count(*) FROM "NotificationIntents" WHERE "Kind"='PurchaseConfirmation' AND "BusinessId"='{oid}' ''')=='0','Rejected created notification')
        status,library=self.http('catalog','/api/v1/me/library',token=user)
        ensure(status==200 and library['totalCount']==1 and library['items'][0]['acquisitionPrice']==Decimal('44.93'),'Only Approved must grant game at44.93')
        return {'user_id':uid,'orders':orders,'checks':['real-users-jwt-catalog','welcome-console','promotion44.93','approved-library-confirmation','rejected-no-library-no-confirmation']}
    def control(self,price,expected,label):
        status,game=self.http('catalog','/api/v1/games','POST',{'title':'Kind '+self.run_id+' '+label,'basePrice':price},self.admin);ensure(status==201,'Override control game failed')
        status,order=self.http('catalog','/api/v1/me/library','POST',{'gameId':game['id']},self.user);ensure(status==202,'Override control purchase failed')
        oid=str(uuid.UUID(order['orderId']));status,body=poll(lambda:self.http('catalog','/api/v1/me/orders/'+oid,token=self.user),lambda x:x[0]==200 and x[1]['status']!='Pending','effective config '+expected)
        ensure(status==200 and body['status']==expected,'Effective override policy was not observed')
        return oid
    def private_logs(self):
        for owner in OWNERS:
            logs=self.logs(owner);ensure(all(secret.lower() not in logs.lower() and urllib.parse.quote(secret,safe='').lower() not in logs.lower() for secret in self.secrets),'Private data in Kind logs: '+owner)
