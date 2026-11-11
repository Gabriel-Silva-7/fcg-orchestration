#!/usr/bin/env python3
"""Real identity/contact ordering proof with preserved original events in owned holding queues."""
import argparse
import base64
import datetime
import hashlib
import json
from pathlib import Path
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from e2e import OWNERS, ROOT, ensure, poll
from recovery import Recovery

KINDS=('UserCreatedEvent','UserStateChangedEvent','UserStateSnapshotEvent')

class IdentityProof(Recovery):
    def __init__(self,args):
        super().__init__(args);self.secrets.extend(['Delayed contact','Later contact','E2E delayed-contact','E2E accepted-before-delete']);self.holding={};self.acks={};self.pending={};self.metrics={};self.result['latency_seconds']=self.metrics;self.result['mode']='four-real-services-identity-contact-ordering'

    def setup(self):
        sources=json.loads((self.args.env_file.parent/'sources.json').read_text())
        for owner,source in sources['services'].items():
            ensure(self.raw(['git','-C',source['path'],'rev-parse','HEAD']).stdout.strip()==source['sha'],'Prepared source changed')
            ensure(not self.raw(['git','-C',source['path'],'status','--porcelain']).stdout.strip(),'Prepared source dirty')
        self.result['sources']=sources;self.command(['build']);self.start(*OWNERS)
        environment=self.services['users']['environment'];first=self.login(environment['AdminBootstrap__Email'],environment['AdminBootstrap__Password'])
        first_id=self.http('users','/api/v1/me',token=first)[1]['id']
        self.authorization_ready(first)
        second_email='operator-'+self.run_id+'@example.com';second_password=secrets.token_hex(20)+'!Aa1';self.secrets.extend([second_email,second_password])
        private=self.args.env_file.parent/'second-admin.json'
        private.write_text(json.dumps({'services':{'users':{'environment':{'AdminBootstrap__Email':second_email,'AdminBootstrap__Password':second_password}}}}));private.chmod(0o600)
        self.raw(self.compose+['-f',str(private),'up','-d','--no-deps','users']);self.capture();poll(lambda:self.http('users','/health/ready')[0],lambda x:x==200,'second administrator bootstrap')
        second=self.login(second_email,second_password);self.authorization_ready(second)
        self.command(['up','-d','--no-deps','users']);self.capture();poll(lambda:self.http('users','/health/ready')[0],lambda x:x==200,'normal Users configuration restored')
        # These consumers own business transactions, not a MassTransit EF inbox.
        initial=int(self.db('users','SELECT count(*) FROM "Users"'))
        for owner in ('catalog','notifications'):
            queue='fcg-'+owner+'-identity'
            count=poll(lambda:self.queue(queue).get('message_stats',{}).get('ack',0),lambda n:n>=initial,'initial identity acknowledgements')
            self.acks[queue]=count
        return first_id,first,second

    def suspend(self):
        for owner in ('catalog','notifications'):
            queue='fcg-identity-hold-'+self.run_id+'-'+owner;self.holding[owner]=queue
            self.mq('/api/queues/%2Ffcg/'+queue,'PUT',{'durable':True,'auto_delete':False,'arguments':{}})
            target='fcg-'+owner+'-identity'
            for kind in KINDS:
                source=urllib.parse.quote('FCG.Contracts:'+kind,safe='')
                self.mq('/api/bindings/%2Ffcg/e/'+source+'/q/'+queue,'POST',{'routing_key':'','arguments':{}})
                path='/api/bindings/%2Ffcg/e/'+source+'/e/'+target
                edges=self.mq(path);ensure(len(edges)==1 and edges[0]['routing_key']=='' and edges[0]['arguments']=={},'Expected exact managed identity binding')
                self.mq(path+'/'+urllib.parse.quote(edges[0]['properties_key'],safe=''),'DELETE')

    def restore(self):
        for owner,queue in self.holding.items():
            target='fcg-'+owner+'-identity'
            for kind in KINDS:
                source=urllib.parse.quote('FCG.Contracts:'+kind,safe='')
                self.mq('/api/bindings/%2Ffcg/e/'+source+'/e/'+target,'POST',{'routing_key':'','arguments':{}})
                path='/api/bindings/%2Ffcg/e/'+source+'/q/'+queue
                for edge in self.mq(path):self.mq(path+'/'+urllib.parse.quote(edge['properties_key'],safe=''),'DELETE')

    def original(self,owner,uid,version,kind):
        def read():
            entries=self.mq('/api/queues/%2Ffcg/'+self.holding[owner]+'/get','POST',{'count':100,'ackmode':'ack_requeue_true','encoding':'auto','truncate':1000000})
            found=[]
            for entry in entries:
                message=json.loads(entry['payload']);body=message['message']
                if body.get('userId')==uid and body.get('eventVersion')==version and 'urn:message:FCG.Contracts:'+kind in message['messageType']:found.append(message)
            return found
        values=poll(read,bool,'original '+kind+' version'+str(version));return values[0]

    def release(self,queue,envelope,duplicate=False):
        owner='catalog' if queue.startswith('fcg-catalog') else 'notifications';credentials=self.services[owner]['environment']
        mid=str(uuid.uuid4()) if duplicate else str(uuid.UUID(envelope['messageId']));copied={**envelope,'messageId':mid}
        environment={**self.env,'AMQP_HOST':'127.0.0.1','AMQP_PORT':self.command(['port','rabbitmq','5672']).strip().split(':')[-1],'AMQP_VHOST':'/fcg','AMQP_USERNAME':credentials['RabbitMq__Username'],'AMQP_PASSWORD':credentials['RabbitMq__Password'],'EXCHANGE':'','ROUTING_KEY':queue,'MESSAGE_BODY':json.dumps(copied),'MESSAGE_ID':mid,'CORRELATION_ID':str(envelope.get('correlationId') or copied['message'].get('userId') or copied['message']['orderId']),'CONTENT_TYPE':'application/vnd.masstransit+json'}
        response=self.raw([str(self.args.dotnet),str(ROOT/'tests/rabbitmq/Probes/bin/Release/net10.0/RabbitMq.Probes.dll'),'publish-confirm'],env=environment)
        ensure(json.loads(response.stdout)['confirmed'],'Original event release must be AMQP confirmed')
        ensure(queue in self.acks,'Expected acknowledgement baseline before explicit release')
        self.acks[queue]+=1;self.pending[mid]=(queue,self.acks[queue]);return mid

    def consumed(self,owner,mid):
        queue,expected=self.pending.pop(mid)
        poll(lambda:self.queue(queue),lambda state:state.get('message_stats',{}).get('ack',0)>=expected and state.get('messages_ready',0)==0 and state.get('messages_unacknowledged',0)==0,'confirmed serial release acknowledged '+owner)

    def normal_acks(self):
        for owner in ('catalog','notifications'):
            queue='fcg-'+owner+'-identity';self.acks[queue]+=1;expected=self.acks[queue]
            poll(lambda:self.queue(queue).get('message_stats',{}).get('ack',0),lambda count:count>=expected,'normal authoritative event acknowledged '+owner)

    def release_identity(self,owner,message,duplicate=False):
        mid=self.release('fcg-'+owner+'-identity',message,duplicate);self.consumed(owner,mid)

    def record(self,admin,uid):
        status,page=self.http('users','/api/v1/admin/users',token=admin);ensure(status==200,'Administrator must read current concurrency version')
        matches=[x for x in page['items'] if x['id']==uid];ensure(len(matches)==1,'Expected existing identity in own administrative page');return matches[0]

    def status(self,admin,uid,active):
        current=self.record(admin,uid)
        code,body=self.http('users','/api/v1/admin/users/'+uid+'/status','PATCH',{'isActive':active,'version':current['version']},admin)
        ensure(code==200,'Authorized status transition failed');return body

    def reconcile(self,uid):
        r=self.raw(self.compose+['exec','-T','users','dotnet','FCG.Users.Api.dll','reconcile-identities','--user-id',uid])
        ensure(not r.stderr and json.loads(r.stdout)['staged']==1,'Owner snapshot must be durably staged')

    def expired_fixture(self,token):
        header,payload,_=token.split('.');decode=lambda s:json.loads(base64.urlsafe_b64decode(s+'='*(-len(s)%4)))
        claims=decode(payload);ensure(claims['exp']-claims['nbf']==900,'Real Users token must preserve15-minute policy')
        now=int(time.time());claims.update({'exp':now-60,'nbf':now-900,'iat':now-900})
        encoded=base64.urlsafe_b64encode(json.dumps(claims,separators=(',',':')).encode()).decode().rstrip('=');message=(header+'.'+encoded).encode()
        key=next(volume['source'] for volume in self.services['users']['volumes'] if volume['target']=='/run/secrets/private.pem')
        # Explicit signed lifetime fixture; the real Users API did not emit this expired token.
        result=subprocess.run(['openssl','dgst','-sha256','-sign',key],input=message,capture_output=True,timeout=10);ensure(result.returncode==0,'Owned RS256 fixture signing failed')
        signed=message.decode()+'.'+base64.urlsafe_b64encode(result.stdout).decode().rstrip('=');self.secrets.append(signed);return signed

    def unknown(self,token):
        request=urllib.request.Request(self.urls['catalog']+'/api/v1/me/orders',headers={'Authorization':'Bearer '+token})
        try:urllib.request.urlopen(request,timeout=8);raise RuntimeError('Unknown projection authorized')
        except urllib.error.HTTPError as error:
            problem=json.loads(error.read());ensure(error.code==503 and error.headers.get('Retry-After')=='1' and problem['code']=='auth_state_unavailable','Unknown projection must fail closed with retry guidance')

    def projection(self,owner,uid,state,version):
        table='UserAuthorizationStates' if owner=='catalog' else 'UserContacts'
        poll(lambda:self.db(owner,f'''SELECT "State"||':'||"EventVersion" FROM "{table}" WHERE "UserId"='{uid}' '''),lambda value:value==state+':'+str(version),'projection '+owner+' '+state+str(version))

    def resume_payments(self):
        cid=self.ids['payments'];obj=json.loads(self.raw(self.docker+['inspect',cid]).stdout)[0]
        ensure(obj['Config']['Labels'].get('com.docker.compose.project')==self.project and self.resources[cid]['created']==obj['Created'],'Payments identity changed')
        self.raw(self.docker+['start',cid]);self.capture();poll(lambda:self.http('payments','/health/ready')[0],lambda n:n==200,'same Payments runtime resumed')

    def healthy_transition(self,admin,uid,token,action,label):
        before=time.monotonic()
        if action=='delete':ensure(self.http('users','/api/v1/admin/users/'+uid,'DELETE',token=admin)[0]==204,'Authorized delete failed')
        else:self.status(admin,uid,action=='unblock')
        expected=200 if action=='unblock' else 401
        poll(lambda:self.http('catalog','/api/v1/me/orders',token=token)[0],lambda code:code==expected,'healthy '+label+' '+action,seconds=5)
        elapsed=time.monotonic()-before;ensure(elapsed<=5,'Healthy local projection exceeded five-second objective');self.metrics[label+'-'+action]=round(elapsed,4)
        ensure(self.http('users','/api/v1/me',token=token)[0]==expected,'Users current authority differs')
        self.normal_acks()

    def terminal(self,admin,uid,token,old):
        current=self.record(admin,uid);code,problem=self.http('users','/api/v1/admin/users/'+uid+'/status','PATCH',{'isActive':True,'version':current['version']},admin)
        ensure(code==409 and problem['code']=='user_deleted','Deleted identity must not be restored by HTTP')
        for owner in ('catalog','notifications'):
            before=self.db(owner,('SELECT "EventVersion" FROM "UserAuthorizationStates"' if owner=='catalog' else 'SELECT "EventVersion" FROM "UserContacts"')+f''' WHERE "UserId"='{uid}' ''')
            self.release_identity(owner,old,True);self.projection(owner,uid,'Deleted',int(before))
        ensure(self.http('catalog','/api/v1/me/orders',token=token)[0]==401,'Stale event restored deleted authorization')

    def execute(self):
        admin_id,admin,operator=self.setup();self.suspend();uid,user=self.register('delayed-contact')
        self.unknown(user);expired=self.expired_fixture(user);ensure(self.http('catalog','/api/v1/me/orders',token=expired)[0]==401,'Expired fixture must precede unknown projection')
        created=self.original('catalog',uid,1,'UserCreatedEvent');self.release_identity('catalog',created);self.authorization_ready(user)
        ensure(self.http('catalog','/api/v1/me/orders',token=expired)[0]==401,'Expired fixture authorized known projection');self.checks['unknown_503_retry_then_original_created_convergence_and_expired_fixture']=True
        self.command(['stop','payments']);order=self.accepted(admin,user,'before-block',promotion=True);oid=str(uuid.UUID(order['orderId']))
        email2='delayed-'+self.run_id+'@example.com';self.secrets.append(email2)
        ensure(self.http('users','/api/v1/me','PATCH',{'name':'Delayed contact','email':email2},user)[0]==200,'Real profile change failed')
        changed2=self.original('catalog',uid,2,'UserStateChangedEvent');start=time.monotonic();self.status(operator,uid,False)
        ensure(self.http('users','/api/v1/me',token=user)[0]==401 and self.http('catalog','/api/v1/me/orders',token=user)[0]==200,'Controlled delay must expose documented stale authorization window')
        poll(lambda:time.monotonic()-start,lambda elapsed:elapsed>5,'explicitly withheld projection exceeds normal target',seconds=6)
        ensure(self.http('catalog','/api/v1/me/orders',token=user)[0]==200,'Withheld event unexpectedly reached Catalog')
        self.result['controlled_stale_window_seconds']=round(time.monotonic()-start,4)
        blocked=self.original('catalog',uid,3,'UserStateChangedEvent');self.release_identity('catalog',blocked);self.projection('catalog',uid,'Blocked',3)
        for old in (changed2,created):self.release_identity('catalog',old,True)
        ensure(self.http('catalog','/api/v1/me/orders',token=user)[0]==401,'Old event undid new block')
        ensure(self.http('catalog','/api/v1/me/library','POST',{'gameId':order['gameId']},user)[0]==401,'Known blocked identity started a new purchase')
        self.resume_payments()
        poll(lambda:self.db('catalog',f'''SELECT "Status" FROM "Orders" WHERE "Id"='{oid}' '''),lambda s:s=='Approved','accepted order completes after block')
        poll(lambda:self.db('notifications',f'''SELECT "State" FROM "NotificationIntents" WHERE "Kind"='PurchaseConfirmation' AND "BusinessId"='{oid}' '''),lambda s:s=='PendingContact','real approved payment waits for contact')
        ensure(self.db('notifications',f'''SELECT count(*) FROM "NotificationIntents" WHERE "BusinessId"='{oid}' AND "RecipientEmail" IS NULL AND "SentAtUtc" IS NULL''')=='1','Contact-free intent emitted early')
        ensure(self.db('catalog',f'''SELECT count(*) FROM "LibraryEntries" WHERE "OriginOrderId"='{oid}' ''')=='1','Accepted order did not grant its original game after block')
        self.orders['before-block']=oid;self.checks['accepted_order_finishes_while_http_blocked_without_contact']=True
        self.reconcile(uid);snapshot=self.original('notifications',uid,3,'UserStateSnapshotEvent');self.release_identity('notifications',snapshot);self.notification('PurchaseConfirmation',oid)
        ensure(self.db('notifications',f'''SELECT count(*) FROM "NotificationIntents" WHERE "Kind"='Welcome' AND "BusinessId"='{uid}' ''')=='0','Recovery snapshot invented Welcome')
        self.release_identity('notifications',created);self.notification('Welcome',uid);self.release_identity('notifications',snapshot,True)
        self.projection('notifications',uid,'Blocked',3)
        ensure(self.db('notifications',f'''SELECT "RecipientEmail" FROM "NotificationIntents" WHERE "Kind"='PurchaseConfirmation' AND "BusinessId"='{oid}' ''')==email2,'Late contact did not prepare current recipient')
        ensure(any('Para=d***@example.com' in line and oid in line for line in self.logs('notifications').splitlines()),'Console did not mask late recipient')
        self.status(operator,uid,True);active=self.original('catalog',uid,4,'UserStateChangedEvent')
        for owner in ('catalog','notifications'):self.release_identity(owner,active)
        email3='later-'+self.run_id+'@example.com';self.secrets.append(email3)
        ensure(self.http('users','/api/v1/me','PATCH',{'name':'Later contact','email':email3},user)[0]==200,'Second real profile update failed')
        changed5=self.original('catalog',uid,5,'UserStateChangedEvent')
        for owner in ('catalog','notifications'):self.release_identity(owner,changed5)
        payment=json.loads(self.db('payments',f'''SELECT "Payload" FROM "Results" WHERE "OrderId"='{oid}' '''))
        self.acks['fcg-notifications-payments']=poll(lambda:self.queue('fcg-notifications-payments').get('message_stats',{}).get('ack',0),lambda n:n>=1,'original payment acknowledgement')
        mid=self.release('fcg-notifications-payments',{'messageId':str(uuid.uuid4()),'messageType':['urn:message:FCG.Contracts:PaymentProcessedEvent'],'message':payment,'headers':{}});self.consumed('notifications',mid)
        ensure(self.db('notifications',f'''SELECT "RecipientEmail" FROM "NotificationIntents" WHERE "Kind"='PurchaseConfirmation' AND "BusinessId"='{oid}' ''')==email2,'Sent recipient changed after profile update')
        ensure(self.db('notifications',f'''SELECT count(*) FROM "NotificationIntents" WHERE "Kind"='Welcome' AND "BusinessId"='{uid}' ''')=='1','Old Created or snapshot duplicated Welcome')
        self.checks['snapshot_no_welcome_late_created_once_current_contact_then_frozen_sent']=True
        self.restore()
        for action in ('block','unblock','delete'):self.healthy_transition(operator,uid,user,action,'user')
        self.terminal(operator,uid,user,active)
        forged={**snapshot,'messageId':str(uuid.uuid4()),'message':{**snapshot['message'],'eventId':str(uuid.uuid4()),'eventVersion':9,'state':'Active','occurredAtUtc':datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')}}
        for owner in ('catalog','notifications'):
            self.release_identity(owner,forged)
            reason='DeletedStateIsTerminal' if owner=='catalog' else 'DeletedIdentityCannotBeRestored'
            poll(lambda:self.db(owner,f'''SELECT count(*) FROM "MessageConflicts" WHERE "BusinessId"='{uid}' AND "Reason"='{reason}' '''),lambda n:n=='1','invalid higher version restoration quarantined '+owner)
            self.projection(owner,uid,'Deleted',8)
        self.checks['normal_user_transitions_target_and_terminal_deletion']=True
        # Capture an actual authoritative snapshot of the untouched administrator, without delaying delivery.
        source=urllib.parse.quote('FCG.Contracts:UserStateSnapshotEvent',safe='');target=self.holding['catalog']
        path='/api/bindings/%2Ffcg/e/'+source+'/q/'+target
        self.mq(path,'POST',{'routing_key':'','arguments':{}});self.reconcile(admin_id)
        admin_old=self.original('catalog',admin_id,1,'UserStateSnapshotEvent');self.normal_acks()
        for edge in self.mq(path):self.mq(path+'/'+urllib.parse.quote(edge['properties_key'],safe=''),'DELETE')
        for action in ('block','unblock','delete'):self.healthy_transition(operator,admin_id,admin,action,'administrator')
        self.terminal(operator,admin_id,admin,admin_old)
        self.checks['real_second_bootstrap_admin_controls_first_admin_without_self_mutation']=True
        # Separate deterministic case: a different accepted purchase finishes only AFTER deletion.
        deleted_uid,deleted_token=self.register('accepted-before-delete');self.authorization_ready(deleted_token);self.notification('Welcome',deleted_uid);self.normal_acks()
        self.command(['stop','payments']);deletion_order=self.accepted(operator,deleted_token,'before-deletion',promotion=True);deleted_oid=str(uuid.UUID(deletion_order['orderId']))
        ensure(self.http('users','/api/v1/admin/users/'+deleted_uid,'DELETE',token=operator)[0]==204,'Delete of accepted purchaser failed')
        poll(lambda:self.http('catalog','/api/v1/me/orders',token=deleted_token)[0],lambda code:code==401,'deleted purchaser projection');self.normal_acks()
        self.resume_payments()
        poll(lambda:self.db('catalog',f"SELECT \"Status\" FROM \"Orders\" WHERE \"Id\"='{deleted_oid}'"),lambda state:state=='Approved','accepted order finishes after deletion')
        ensure(self.db('catalog',f"SELECT count(*) FROM \"LibraryEntries\" WHERE \"OriginOrderId\"='{deleted_oid}'")=='1','Deleted purchaser lost accepted acquisition')
        self.notification('PurchaseConfirmation',deleted_oid);self.checks['accepted_order_finishes_after_user_deletion']=True
        ensure(self.http('catalog','/api/v1/games')[0]==200,'Public catalog unavailable after identity deletion')
        for owner in OWNERS:
            logs=self.logs(owner);ensure(all(s.lower() not in logs.lower() and urllib.parse.quote(s,safe='').lower() not in logs.lower() for s in self.secrets),'Private data in production logs')
        emissions=[line for line in self.logs('notifications').splitlines() if '[E-MAIL SIMULADO] PurchaseConfirmation' in line and oid in line]
        ensure(len(emissions)==1,'Healthy repeated payment or contact update emitted the same confirmation again')
        ensure(self.db('notifications',f'''SELECT count(*) FROM \"NotificationAttempts\" a JOIN \"NotificationIntents\" i ON a.\"IntentId\"=i.\"Id\" WHERE i.\"Kind\"='PurchaseConfirmation' AND i.\"BusinessId\"='{oid}' ''')=='1','Healthy duplicate produced another delivery attempt')
        self.result['status']='passed'


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--env-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--dotnet',type=Path,required=True);parser.add_argument('--docker-context',default='orbstack')
    args=parser.parse_args();args.allow_owned_fault_fixtures=False;args.output.mkdir(parents=True,exist_ok=False);proof=None
    try:proof=IdentityProof(args);proof.execute()
    except Exception as error:
        if proof:proof.result['error_type']=type(error).__name__
        print('Identity proof failed: '+type(error).__name__,file=sys.stderr);raise
    finally:
        if proof:
            try:proof.cleanup()
            finally:
                proof.result['resources']=proof.resources;proof.result['infrastructure']=proof.infrastructure
                (args.output/'IDENTITY.json').write_text(json.dumps(proof.result,indent=2)+'\n');print(json.dumps({'status':proof.result['status'],'checks':list(proof.checks),'cleanup_complete':proof.result.get('cleanup_complete',False)}))

if __name__=='__main__':main()
