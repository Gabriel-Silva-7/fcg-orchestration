#!/usr/bin/env python3
"""Owned, disposable four-service recovery proof. Requires a fresh prepare-local environment."""
import argparse
import base64
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from e2e import Journey, JourneyFailure, OWNERS, ROOT, ensure, poll


class Recovery(Journey):
    def __init__(self, args):
        self.args = args; self.run_id = uuid.uuid4().hex; self.accounts = {}; self.secrets = []
        self.env = os.environ.copy()
        for line in args.env_file.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'): self.env.pop(line.split('=', 1)[0].strip(), None)
        self.compose = ['docker', '--context', args.docker_context, 'compose', '--env-file', str(args.env_file.resolve()), '-f', str(ROOT/'docker-compose.yml')]
        self.config = json.loads(self.command(['config', '--format', 'json'])); self.services = self.config['services']; self.project = self.config['name']
        ensure(self.project.startswith('fcg-demo-') and len(self.project) == 21, 'Use a unique prepare-local environment')
        self.docker = ['docker', '--context', args.docker_context]; self.ids = {}; self.urls = {}; self.resources = {}; self.infrastructure = {}; self.checks = {}; self.orders = {}
        self.result = {'status': 'failed', 'project': self.project, 'run_id': self.run_id, 'checks': self.checks, 'orders': self.orders, 'fault_fixtures_explicit': args.allow_owned_fault_fixtures}
        for service in self.services.values():
            for key, value in service.get('environment', {}).items():
                if any(x in key.lower() for x in ('password', 'connectionstrings')) and value: self.secrets.append(value)
        for resource, key in [('container', 'com.docker.compose.project'), ('network', 'com.docker.compose.project'), ('volume', 'com.docker.compose.project')]:
            command = ['ps', '-aq'] if resource == 'container' else [resource, 'ls', '-q']
            ensure(not self.raw(self.docker+command+['--filter', 'label='+key+'='+self.project]).stdout.strip(), 'Recovery requires an unused project; no existing data may be changed')

    def raw(self, args, env=None, check=True, timeout=420):
        try: result = subprocess.run(args, env=env or self.env, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired: raise JourneyFailure('Owned recovery subprocess exceeded deadline') from None
        ensure(not check or result.returncode == 0, 'Recovery subprocess failed: '+Path(args[0]).name)
        return result

    def command(self, args): return self.raw(self.compose+args).stdout

    def capture(self):
        for owner in (*OWNERS, 'postgres', 'rabbitmq', 'rabbitmq-bootstrap'):
            cid = self.command(['ps', '-a', '-q', owner]).strip()
            if not cid: continue
            obj = json.loads(self.raw(self.docker+['inspect', cid]).stdout)[0]
            ensure(obj['Config']['Labels'].get('com.docker.compose.project') == self.project, 'Owned container identity changed')
            self.ids[owner] = obj['Id']; self.resources[obj['Id']] = {'owner': owner, 'created': obj['Created'], 'image': obj['Image']}
            if owner in OWNERS and obj['State']['Running']:
                port = self.command(['port', owner, '8080']).strip(); ensure(port.startswith('127.0.0.1:'), 'Expected loopback mapping')
                self.urls[owner] = 'http://'+port

        for kind in ('network','volume'):
            names=self.raw(self.docker+[kind,'ls','-q','--filter','label=com.docker.compose.project='+self.project]).stdout.split()
            for name in names:
                obj=json.loads(self.raw(self.docker+[kind,'inspect',name]).stdout)[0]
                ensure(obj['Labels'].get('com.docker.compose.project')==self.project,'Owned infrastructure label changed')
                identity=obj['Id'] if kind=='network' else obj['Name']
                created=obj['Created'] if kind=='network' else obj['CreatedAt']
                entry={'kind':kind,'name':obj['Name'],'created':created}
                ensure(identity not in self.infrastructure or self.infrastructure[identity]==entry,'Owned infrastructure identity changed')
                self.infrastructure[identity]=entry

    def start(self, *owners):
        self.command(['up', '-d', *owners]); self.capture()
        for owner in owners:
            if owner in OWNERS: poll(lambda:self.http(owner, '/health/ready')[0], lambda x:x == 200, owner+' recovery readiness')

    def mq(self, path, method='GET', body=None):
        port = self.command(['port', 'rabbitmq', '15672']).strip()
        environment = self.services['rabbitmq']['environment']
        authorization = base64.b64encode((environment['RABBITMQ_DEFAULT_USER']+':'+environment['RABBITMQ_DEFAULT_PASS']).encode()).decode()
        request = urllib.request.Request('http://'+port+path, data=json.dumps(body).encode() if body is not None else None, method=method,
            headers={'Authorization':'Basic '+authorization, 'Content-Type':'application/json'})
        with urllib.request.urlopen(request, timeout=8) as response:
            raw=response.read(); return json.loads(raw) if raw else None

    def queue(self, name):
        try: return self.mq('/api/queues/%2Ffcg/'+name)
        except urllib.error.HTTPError as error:
            if error.code == 404 and name.endswith('_error'): return {'messages_ready':0}
            raise

    def peek(self, name):
        values=self.mq('/api/queues/%2Ffcg/'+name+'/get', 'POST', {'count':1, 'ackmode':'ack_requeue_true', 'encoding':'auto', 'truncate':1000000})
        ensure(len(values) == 1, 'Expected a queued original message'); return values[0]

    def publish(self, queue, envelope):
        # Explicit delivery fixture: new transport identity, unchanged business payload unless the case says otherwise.
        mid=str(uuid.uuid4()); copied={**envelope, 'messageId':mid}
        owner='payments' if queue=='fcg-payments-orders' else 'notifications'
        credentials=self.services[owner]['environment']; environment={**self.env,
            'AMQP_HOST':'127.0.0.1', 'AMQP_PORT':self.command(['port','rabbitmq','5672']).strip().split(':')[-1],
            'AMQP_VHOST':'/fcg', 'AMQP_USERNAME':credentials['RabbitMq__Username'], 'AMQP_PASSWORD':credentials['RabbitMq__Password'],
            'EXCHANGE':'', 'ROUTING_KEY':queue, 'MESSAGE_BODY':json.dumps(copied), 'MESSAGE_ID':mid,
            'CORRELATION_ID':copied['message']['orderId'], 'CONTENT_TYPE':'application/vnd.masstransit+json'}
        output=self.raw([str(self.args.dotnet),str(ROOT/'tests/rabbitmq/Probes/bin/Release/net10.0/RabbitMq.Probes.dll'),'publish-confirm'], env=environment)
        ensure(json.loads(output.stdout)['confirmed'], 'AMQP delivery must be confirmed'); return mid

    def fault_sql(self, owner, query):
        ensure(self.args.allow_owned_fault_fixtures, 'SQL fault fixtures require the explicit test flag')
        password=self.services['postgres']['environment']['FCG_'+owner.upper()+'_DB_PASSWORD']
        return self.command(['exec','-T','-e','PGPASSWORD='+password,'postgres','psql','-h','127.0.0.1','-U','fcg_'+owner,'-d','fcg_'+owner,'-v','ON_ERROR_STOP=1','-Atc',query]).strip()

    def cli(self, owner, verb, *args, expected=0):
        dll={'catalog':'FCG.Catalog.Api.dll','payments':'FCG.Payments.dll','notifications':'FCG.Notifications.dll'}[owner]
        r=self.raw(self.compose+['exec','-T',owner,'dotnet',dll,verb,*args],check=False)
        ensure(r.returncode==expected and not r.stderr, 'Local recovery command returned an unexpected state')
        ensure(all(s not in r.stdout for s in self.secrets), 'CLI leaked private configuration'); return json.loads(r.stdout)

    def accepted(self, admin, user, suffix, price=59.90, promotion=False):
        code,game=self.http('catalog','/api/v1/games','POST',{'title':'Recovery '+self.run_id+' '+suffix,'basePrice':price},admin)
        ensure(code==201, 'Real administrator must create recovery game')
        if promotion:
            code,_=self.http('catalog','/api/v1/games/'+game['id']+'/promotions','POST',{'discountPercentage':25,'startsAt':'2020-01-01T00:00:00Z','endsAt':'2090-01-01T00:00:00Z'},admin)
            ensure(code==201, 'Real promotion must be accepted')
        code,order=self.http('catalog','/api/v1/me/library','POST',{'gameId':game['id']},user)
        ensure(code==202 and order['status']=='Pending', 'Original HTTP purchase must be accepted once')
        self.orders[suffix]=order['orderId']; return order

    def terminal(self, user, order, status):
        code,value=poll(lambda:self.http('catalog','/api/v1/me/orders/'+order['orderId'],token=user),lambda x:x[0]==200 and x[1]['status']!='Pending', 'same original order terminal')
        ensure(code==200 and value['status']==status, 'Original order has wrong terminal state'); return value

    def execute(self):
        sources=json.loads((self.args.env_file.parent/'sources.json').read_text())
        for owner,source in sources['services'].items():
            ensure(self.raw(['git','-C',source['path'],'rev-parse','HEAD']).stdout.strip()==source['sha'], 'Prepared source HEAD changed')
            ensure(not self.raw(['git','-C',source['path'],'status','--porcelain']).stdout.strip(), 'Prepared source became dirty')
            ensure(str(Path(self.services[owner]['build']['context']).resolve())==source['path'], 'Prepared source/context differ')
            ensure(self.services[owner]['image'].endswith(':'+source['sha']), 'Prepared image reference/source differ')
        self.result['sources']=sources
        self.command(['build']); self.start('users')
        poll(lambda:self.raw(self.docker+['inspect','--format','{{.State.ExitCode}}',self.ids['rabbitmq-bootstrap']]).stdout.strip(), lambda x:x=='0', 'topology gate')
        user_id,user=self.register('durability')
        for queue in ('fcg-catalog-identity','fcg-notifications-identity'):
            poll(lambda:self.queue(queue).get('messages_ready',0),lambda x:x>=1, 'identity retained before consumer start')
        self.checks['registration_queued_before_consumers']=True
        self.start('catalog'); environment=self.services['users']['environment']
        admin=self.login(environment['AdminBootstrap__Email'],environment['AdminBootstrap__Password'])
        self.authorization_ready(admin); self.authorization_ready(user)
        order=self.accepted(admin,user,'durability',promotion=True)
        ensure(order['price']==Decimal('44.93'), 'Purchase snapshot must be44.93')
        poll(lambda:self.queue('fcg-payments-orders').get('messages_ready',0),lambda x:x==1, 'original order queued before Payments')
        original=self.peek('fcg-payments-orders'); envelope=json.loads(original['payload'])
        ensure(envelope['message']['orderId']==order['orderId'], 'Broker must hold original HTTP order')
        before_hash=hashlib.sha256(original['payload'].encode()).hexdigest()
        previous=self.ids['rabbitmq']; old=json.loads(self.raw(self.docker+['inspect',previous]).stdout)[0]
        volume=next(x['Name'] for x in old['Mounts'] if x['Destination']=='/var/lib/rabbitmq')
        self.command(['up','-d','--no-deps','--force-recreate','rabbitmq']); self.capture()
        ensure(self.ids['rabbitmq']!=previous, 'Broker container must actually be recreated')
        poll(lambda:self.raw(self.docker+['inspect','--format','{{.State.Health.Status}}',self.ids['rabbitmq']]).stdout.strip(),lambda x:x=='healthy','recreated durable broker')
        new=json.loads(self.raw(self.docker+['inspect',self.ids['rabbitmq']]).stdout)[0]
        ensure(volume==next(x['Name'] for x in new['Mounts'] if x['Destination']=='/var/lib/rabbitmq'), 'RabbitMQ named volume changed')
        retained=self.peek('fcg-payments-orders')
        ensure(before_hash==hashlib.sha256(retained['payload'].encode()).hexdigest() and original['properties']['message_id']==retained['properties']['message_id'], 'Original broker payload or transport identity was lost')
        self.checks['broker_recreated_same_volume_original_message_retained']={'before':previous,'after':self.ids['rabbitmq'],'volume':volume,'payload_sha256':before_hash,'message_id':original['properties']['message_id']}
        self.start('payments','notifications'); self.terminal(user,order,'Approved')
        self.notification('Welcome',user_id); self.notification('PurchaseConfirmation',order['orderId'])
        result=self.db('payments',f'''SELECT "Payload" FROM "Results" WHERE "OrderId"='{order['orderId']}' ''')
        ensure(json.loads(result,parse_float=Decimal)['price']==Decimal('44.93'), 'Original persisted result must preserve snapshot')
        self.checks['same_http_order_completes_after_broker_recreation']=True
        # Change real Payments policy, prove it with a new control purchase, then redeliver the ORIGINAL order.
        override=self.args.output/'zero-policy.json'; override.write_text(json.dumps({'services':{'payments':{'environment':{'PaymentSimulation__ApprovalLimit':'0'}}}}))
        self.raw(self.compose+['-f',str(override),'up','-d','--no-deps','payments']); self.capture()
        poll(lambda:self.http('payments','/health/ready')[0],lambda x:x==200,'zero-limit Payments')
        control=self.accepted(admin,user,'zero-policy-control',price=1); self.terminal(user,control,'Rejected')
        new_id=self.publish('fcg-payments-orders',envelope)
        # Transport inbox proves the equivalent delivery was actually consumed; matching old state alone is insufficient.
        poll(lambda:self.db('payments',f'''SELECT count(*) FROM "InboxState" WHERE "MessageId"='{new_id}' AND "Delivered" IS NOT NULL '''),lambda x:x=='1','equivalent original delivery under zero limit')
        ensure(self.db('payments',f'''SELECT "Payload" FROM "Results" WHERE "OrderId"='{order['orderId']}' ''')==result, 'Changed policy recalculated original result')
        self.checks['new_policy_control_rejected_original_replay_unchanged']=True
        # A broken own schema is a controlled transport fault, distinct from a committed business conflict.
        ensure(self.args.allow_owned_fault_fixtures, 'Explicit disposable fault flag required')
        self.fault_sql('payments','ALTER TABLE "Results" RENAME COLUMN "Payload" TO "UnavailablePayload"')
        error_id=self.publish('fcg-payments-orders',envelope)
        poll(lambda:self.queue('fcg-payments-orders_error').get('messages_ready',0),lambda x:x==1,'exhausted retries in durable error queue')
        self.fault_sql('payments','ALTER TABLE "Results" RENAME COLUMN "UnavailablePayload" TO "Payload"')
        credentials=self.services['payments']['environment']; mqenv={**self.env,'AMQP_HOST':'127.0.0.1','AMQP_PORT':self.command(['port','rabbitmq','5672']).strip().split(':')[-1], 'AMQP_VHOST':'/fcg','AMQP_USERNAME':credentials['RabbitMq__Username'],'AMQP_PASSWORD':credentials['RabbitMq__Password']}
        tool=[str(self.args.dotnet),str(ROOT/'tools/FCG.Messaging.Operations/bin/Release/net10.0/FCG.Messaging.Operations.dll'),'error-redrive','--queue','fcg-payments-orders','--message-id',error_id]
        ensure(json.loads(self.raw(tool+['--dry-run'],env=mqenv).stdout)['found'], 'Error redrive preview must find exact transport identity')
        ensure(json.loads(self.raw(tool+['--apply'],env=mqenv).stdout)['confirmed'], 'Error redrive must confirm before ack')
        poll(lambda:self.db('payments',f'''SELECT count(*) FROM "InboxState" WHERE "MessageId"='{error_id}' AND "Delivered" IS NOT NULL '''),lambda x:x=='1','redriven original message')
        ensure(self.db('payments',f'''SELECT "Payload" FROM "Results" WHERE "OrderId"='{order['orderId']}' ''')==result, 'Error redrive changed original decision')
        self.checks['real_error_retry_repair_redrive_exact_message_id']=True
        # Separate owner-authorized inconsistent restore fixture in Payments; no new HTTP order.
        oid=str(uuid.UUID(order['orderId']))
        self.fault_sql('payments',f"UPDATE payment_decisions SET price=price+1 WHERE order_id='{oid}'")
        self.publish('fcg-payments-orders',envelope)
        conflict=poll(lambda:self.db('payments',f"SELECT \"Id\" FROM \"MessageConflicts\" WHERE \"BusinessId\"='{oid}'"),bool,'divergent original request diagnostic')
        ensure(self.cli('payments','recover-orders','replay','--id',conflict,expected=4)['reason']=='DivergentOrderRequest','Inconsistent payment winner must remain quarantined')
        self.fault_sql('payments',f"UPDATE payment_decisions SET price=price-1 WHERE order_id='{oid}'")
        ensure(self.cli('payments','recover-orders','replay','--id',conflict)['outcome']=='ReadyToStage','Restored original winner must be eligible')
        ensure(self.cli('payments','recover-orders','replay','--id',conflict,'--apply')['outcome']=='StagedOriginalResult','Payments local replay must stage its original result')
        ensure(self.cli('payments','recover-orders','replay','--id',conflict,'--apply')['outcome']=='AlreadyResolved','Resolved payment replay must be idempotent')
        ensure(self.db('payments',f"SELECT \"Payload\" FROM \"Results\" WHERE \"OrderId\"='{oid}'")==result,'Payment owner restore/replay changed original result')
        self.checks['payments_local_replay_requires_original_winner_not_new_simulation']=True
        # Restore normal policy explicitly; suspend Payments before accepting a distinct real purchase.
        self.command(['up','-d','--no-deps','payments']); self.capture()
        poll(lambda:self.http('payments','/health/ready')[0],lambda x:x==200,'normal policy restored')
        self.command(['stop','payments'])
        pending=self.accepted(admin,user,'catalog-owner-restore',price=19.90); pid=str(uuid.UUID(pending['orderId']))
        backup=self.db('catalog',f"SELECT row_to_json(o) FROM \"Orders\" o WHERE \"Id\"='{pid}'")
        ensure(json.loads(backup)['Status']=='Pending','Owner fixture must back up the original pending HTTP order')
        self.fault_sql('catalog',f"DELETE FROM \"Orders\" WHERE \"Id\"='{pid}'")
        self.command(['start','payments']); self.capture()
        conflict=poll(lambda:self.db('catalog',f"SELECT \"Id\" FROM \"MessageConflicts\" WHERE \"BusinessId\"='{pid}' AND \"Reason\"='OrderNotFound'"),bool,'real payment quarantined after owner order restore fault')
        ensure(self.cli('catalog','recover-payments','replay','--id',conflict,expected=4)['reason']=='OrderNotFound','Preview cannot recreate missing order')
        ensure(self.db('catalog','SELECT count(*) FROM \"PaymentRecoveryAttempts\"')=='0','Catalog preview wrote history')
        ensure(self.cli('catalog','recover-payments','replay','--id',conflict,'--apply',expected=4)['reason']=='OrderNotFound','Apply cannot recreate missing order')
        ensure(self.db('catalog',f"SELECT count(*) FROM \"Orders\" WHERE \"Id\"='{pid}'")=='0','Replay invented an HTTP order')
        # Exact row copy from this case's pre-fault read, not a new order or modified diagnostic payload/hash.
        escaped=backup.replace("'","''")
        self.fault_sql('catalog',f"INSERT INTO \"Orders\" SELECT * FROM json_populate_record(NULL::\"Orders\",'{escaped}'::json)")
        ensure(self.cli('catalog','recover-payments','replay','--id',conflict)['outcome']=='Approved','Restored original pending row must predict approval')
        ensure(self.cli('catalog','recover-payments','replay','--id',conflict,'--apply')['outcome']=='Approved','Atomic application should complete the original order')
        ensure(self.cli('catalog','recover-payments','replay','--id',conflict,'--apply')['outcome']=='AlreadyResolved','Repeated Catalog replay must be idempotent')
        self.terminal(user,pending,'Approved'); self.notification('PurchaseConfirmation',pid)
        ensure(self.db('catalog',f"SELECT count(*) FROM \"LibraryEntries\" WHERE \"OriginOrderId\"='{pid}'")=='1','Recovery must create exactly one acquisition for the original order')
        self.checks['catalog_original_http_order_owner_restore_and_atomic_local_replay']=True
        # Notifications retains the first real payment and never selects a fabricated contradictory terminal.
        payment=json.loads(result); intent=poll(lambda:self.db('notifications',f"SELECT \"Id\" FROM \"NotificationIntents\" WHERE \"Kind\"='PurchaseConfirmation' AND \"BusinessId\"='{oid}' AND \"State\"='Sent'"),bool,'original confirmation sent')
        ensure(self.cli('notifications','recover-notifications','resume','--id',intent,'--apply')['outcome']=='AlreadySent','Resume must not re-emit Sent')
        changed={**payment,'status':'Rejected','reason':'AmountAboveLimit'}
        self.publish('fcg-notifications-payments',{'messageType':['urn:message:FCG.Contracts:PaymentProcessedEvent'],'message':changed,'headers':{}})
        conflict=poll(lambda:self.db('notifications',f"SELECT \"Id\" FROM \"MessageConflicts\" WHERE \"BusinessId\"='{oid}' AND \"Reason\"='DivergentPaymentResult'"),bool,'contradictory fixture quarantined')
        ensure(self.cli('notifications','recover-notifications','replay','--id',conflict,expected=4)['outcome']=='Quarantined','Preview must preserve terminal observation')
        ensure(self.cli('notifications','recover-notifications','replay','--id',conflict,'--apply',expected=4)['reason']=='DivergentPaymentResult','Replay cannot choose a contradictory terminal')
        ensure(self.db('notifications',f"SELECT \"Status\" FROM \"PaymentObservations\" WHERE \"OrderId\"='{oid}'")=='Approved','Notification recovery changed original observation')
        self.checks['notifications_operator_preserves_sent_and_original_terminal']=True
        for owner in OWNERS:
            logs=self.logs(owner)
            ensure(all(s.lower() not in logs.lower() and urllib.parse.quote(s,safe='').lower() not in logs.lower() for s in self.secrets),'Private data in production logs: '+owner)
        self.result['status']='passed'

    def cleanup(self):
        self.capture()
        current=self.raw(self.docker+['ps','-aq','--filter','label=com.docker.compose.project='+self.project]).stdout.split()
        for cid in current:
            obj=json.loads(self.raw(self.docker+['inspect',cid]).stdout)[0]
            ensure(obj['Id'] in self.resources and self.resources[obj['Id']]['created']==obj['Created'], 'Cleanup container identity mismatch')
        self.command(['down','--volumes','--remove-orphans'])
        for kind,cmd in [('container',['ps','-aq']),('network',['network','ls','-q']),('volume',['volume','ls','-q'])]:
            ensure(not self.raw(self.docker+cmd+['--filter','label=com.docker.compose.project='+self.project]).stdout.strip(), 'Owned cleanup incomplete: '+kind)
        self.result['cleanup_complete']=True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file',type=Path,required=True); parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--dotnet',type=Path,required=True); parser.add_argument('--docker-context',default='orbstack')
    parser.add_argument('--allow-owned-fault-fixtures',action='store_true',required=True)
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=False); proof=None
    try:
        proof=Recovery(args); proof.execute()
    except Exception as error:
        if proof: proof.result['error_type']=type(error).__name__
        print('Recovery proof failed: '+type(error).__name__,file=sys.stderr)
        raise
    finally:
        if proof:
            try: proof.cleanup()
            finally:
                proof.result['resources']=proof.resources
                proof.result['infrastructure']=proof.infrastructure
                (args.output/'RECOVERY.json').write_text(json.dumps(proof.result,indent=2)+'\n')
                print(json.dumps({'status':proof.result['status'],'checks':list(proof.checks),'cleanup_complete':proof.result.get('cleanup_complete',False)}))

if __name__=='__main__': main()
