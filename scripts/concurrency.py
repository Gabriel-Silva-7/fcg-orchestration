#!/usr/bin/env python3
"""Four-service concurrency proof with observable PostgreSQL barriers in an unused project."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import datetime
import json
import os
from pathlib import Path
import select
import subprocess
import sys
import time
import uuid
import urllib.parse
from e2e import OWNERS, ROOT, ensure, poll, JourneyFailure
from recovery import Recovery


class DatabaseGate:
    def __init__(self, proof, owner, statement):
        password=proof.services['postgres']['environment']['FCG_'+owner.upper()+'_DB_PASSWORD']
        self.process=subprocess.Popen(proof.compose+['exec','-T','-e','PGPASSWORD='+password,'postgres','psql','-h','127.0.0.1','-U','fcg_'+owner,'-d','fcg_'+owner,'-qAt','-v','ON_ERROR_STOP=1'],env=proof.env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.pending=b''; self.released=False
        try: self.send("BEGIN; SET LOCAL lock_timeout='5s'; "+statement+';')
        except Exception: self.close(); raise

    def send(self, statement):
        marker=uuid.uuid4().hex; self.process.stdin.write((statement+" SELECT '"+marker+"';\n").encode()); self.process.stdin.flush()
        deadline=time.monotonic()+6
        while marker.encode()+b'\n' not in self.pending:
            ensure(self.process.poll() is None,'Owned SQL barrier process failed')
            ensure(time.monotonic()<deadline,'Owned SQL barrier deadline exceeded')
            if select.select([self.process.stdout],[],[],.1)[0]: self.pending+=os.read(self.process.stdout.fileno(),4096)
        self.pending=self.pending.split(marker.encode()+b'\n',1)[1]

    def release(self):
        if not self.released: self.send('COMMIT;'); self.released=True
        self.close()

    def close(self):
        if self.process.poll() is None:
            self.process.stdin.close()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        for stream in (self.process.stdout,self.process.stderr):stream.close()

    def __enter__(self):return self
    def __exit__(self,*_):self.release()


class Concurrency(Recovery):
    def __init__(self,args):
        super().__init__(args);self.auxiliary=[];self.result['mode']='four-real-services-controlled-concurrency'

    def setup(self):
        sources=json.loads((self.args.env_file.parent/'sources.json').read_text())
        for owner,source in sources['services'].items():
            ensure(self.raw(['git','-C',source['path'],'rev-parse','HEAD']).stdout.strip()==source['sha'],'Prepared source changed')
            ensure(not self.raw(['git','-C',source['path'],'status','--porcelain']).stdout.strip(),'Prepared source dirty')
            ensure(str(Path(self.services[owner]['build']['context']).resolve())==source['path'],'Source/context mismatch')
        self.result['sources']=sources;self.command(['build']);self.start(*OWNERS)
        environment=self.services['users']['environment'];admin=self.login(environment['AdminBootstrap__Email'],environment['AdminBootstrap__Password'])
        uid,user=self.register('concurrency');self.user_id=uid;self.authorization_ready(admin);self.authorization_ready(user);self.notification('Welcome',uid)
        return admin,uid,user

    def game(self,admin,suffix,promotion=True,expires=None):
        status,game=self.http('catalog','/api/v1/games','POST',{'title':'Race '+self.run_id+' '+suffix,'basePrice':59.90},admin)
        ensure(status==201,'Race game creation failed')
        if promotion:
            status,_=self.http('catalog','/api/v1/games/'+game['id']+'/promotions','POST',{'discountPercentage':25,'startsAt':'2020-01-01T00:00:00Z','endsAt':expires or '2090-01-01T00:00:00Z'},admin)
            ensure(status==201,'Race promotion creation failed')
        return game['id']

    def purchase(self,user,gid):return self.http('catalog','/api/v1/me/library','POST',{'gameId':gid},user)

    def blocked(self,owner,minimum,key=None):
        # Inspect the exact relation/advisory identity, not unrelated background waits.
        selectors=[]
        if owner=='catalog':selectors.append("(l.locktype='relation' AND l.relation='\"Orders\"'::regclass)")
        if key is not None:
            ensure(all(c.isalnum() or c in ':-' for c in key),'Invalid generated gate key')
            hashed="hashtextextended('"+key+"',0)"
            selectors.append("(l.locktype='advisory' AND l.objsubid=1 AND l.classid=(("+hashed+">>32)&4294967295)::oid AND l.objid=("+hashed+"&4294967295)::oid)")
        ensure(bool(selectors),'Expected an explicit gate identity')
        query="SELECT count(DISTINCT a.pid) FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid WHERE a.datname=current_database() AND a.usename=current_user AND a.pid<>pg_backend_pid() AND a.wait_event_type='Lock' AND NOT l.granted AND ("+' OR '.join(selectors)+")"
        return poll(lambda:int(self.db(owner,query)),lambda n:n>=minimum,'exact '+owner+' lock waiters',seconds=4)

    def assert_effect(self,user,order,status,price=Decimal('44.93')):
        value=self.terminal(user,order,status);ensure(value['price']==price,'Catalog changed selected price')
        oid=str(uuid.UUID(order['orderId']))
        payload=poll(lambda:self.db('payments',f"SELECT \"Payload\" FROM \"Results\" WHERE \"OrderId\"='{oid}'"),bool,'actual Payments result')
        result=json.loads(payload,parse_float=Decimal);ensure(result['price']==price and result['status']==status,'Payments result disagrees with original snapshot')
        observation=poll(lambda:self.db('notifications',f"SELECT \"Status\" FROM \"PaymentObservations\" WHERE \"OrderId\"='{oid}'"),bool,'independent Notifications terminal')
        ensure(observation==status,'Consumers disagree on terminal')
        count=self.db('catalog',f"SELECT count(*) FROM \"LibraryEntries\" WHERE \"OriginOrderId\"='{oid}'")
        ensure(count==('1' if status=='Approved' else '0'),'Terminal and library disagree')
        if status=='Approved':self.notification('PurchaseConfirmation',oid)
        else:ensure(self.db('notifications',f"SELECT count(*) FROM \"NotificationIntents\" WHERE \"Kind\"='PurchaseConfirmation' AND \"BusinessId\"='{oid}'")=='0','Rejected created confirmation')
        return result

    def simultaneous(self,admin,user):
        self.command(['stop','payments']);gid=self.game(admin,'simultaneous')
        with ThreadPoolExecutor(max_workers=2) as pool:
            with DatabaseGate(self,'catalog','LOCK TABLE "Orders" IN SHARE MODE'):
                first=pool.submit(self.purchase,user,gid);second=pool.submit(self.purchase,user,gid)
                observed=self.blocked('catalog',2,'pair:'+self.user_id+':'+gid)
            replies=[first.result(),second.result()]
        accepted=[x[1] for x in replies if x[0]==202];ensure(len(accepted)==1,'Simultaneous requests must accept exactly one order')
        denied=[x for x in replies if x[0]!=202];ensure(len(denied)==1 and denied[0][0]==409 and denied[0][1]['code']=='order_already_pending','Concurrent pending purchase must be classified from actual state')
        order=accepted[0];self.orders['simultaneous']=order['orderId']
        ensure(self.db('catalog',f"SELECT count(*) FROM \"Orders\" WHERE \"GameId\"='{gid}'")=='1','Duplicate open order escaped restriction')
        self.command(['start','payments']);self.capture();self.assert_effect(user,order,'Approved')
        status,problem=self.purchase(user,gid);ensure(status==409 and problem['code']=='game_already_acquired','Approval-winning request must be classified acquired')
        self.checks['simultaneous_one_open_order_then_acquired']={'observed_lock_waiters':observed,'order_id':order['orderId']}

    def snapshot(self,admin,user,expire=False):
        expires=(datetime.datetime.now(datetime.timezone.utc)+datetime.timedelta(seconds=4)).isoformat(timespec='microseconds').replace('+00:00','Z') if expire else None
        gid=self.game(admin,'expiry' if expire else 'edit-delete',expires=expires)
        with ThreadPoolExecutor(max_workers=1) as pool:
            with DatabaseGate(self,'catalog','LOCK TABLE "Orders" IN SHARE MODE'):
                response=pool.submit(self.purchase,user,gid);self.blocked('catalog',1)
                if expire:
                    poll(lambda:self.http('catalog','/api/v1/games/'+gid),lambda x:x[0]==200 and x[1]['currentPrice']==Decimal('59.90'),'promotion expires after selected read',seconds=5)
                else:
                    code,_=self.http('catalog','/api/v1/games/'+gid,'PUT',{'title':'Edited after selection','basePrice':99},admin);ensure(code==200,'Concurrent edit failed')
                    ensure(self.http('catalog','/api/v1/games/'+gid,'DELETE',token=admin)[0]==204,'Concurrent deactivation failed')
            code,order=response.result()
        ensure(code==202 and order['price']==Decimal('44.93'),'Price must remain selected44.93 before commit')
        self.orders['expiry' if expire else 'edit-delete']=order['orderId'];self.assert_effect(user,order,'Approved')
        self.checks['snapshot_survives_'+('natural_expiry' if expire else 'edit_and_deactivation')+'_before_commit']=True

    def queue(self,name):
        import urllib.error
        try:return super().queue(name)
        except urllib.error.HTTPError as error:
            if error.code==404 and name.startswith('fcg-race-'):return {'consumers':0}
            raise

    def worker(self,limit,suffix):
        queue='fcg-race-'+self.run_id+'-'+suffix;environment={**self.services['payments']['environment'],'Queues__Orders':queue,'PaymentSimulation__ApprovalLimit':str(limit),'PaymentSimulation__Delay':'00:00:00','Database__MigrateOnStartup':'false'}
        args=self.docker+['run','-d','--network',self.project+'_default','--label','com.docker.compose.project='+self.project,'--label','io.fcg.concurrency.run='+self.run_id,'--label','com.docker.compose.service=payments-race-'+suffix,'--label','com.docker.compose.oneoff=True']
        for key,value in environment.items():args+=['-e',key+'='+str(value)]
        cid=self.raw(args+[self.services['payments']['image']]).stdout.strip();obj=json.loads(self.raw(self.docker+['inspect',cid]).stdout)[0]
        self.auxiliary.append(cid);self.resources[obj['Id']]={'owner':'payments-'+suffix,'created':obj['Created'],'image':obj['Image']}
        poll(lambda:self.queue(queue).get('consumers',0),lambda n:n==1,'exclusive policy worker '+suffix)
        return queue

    def send_queue(self,queue,envelope):
        # Reuse the original confirmed AMQP helper with this fixture's explicit owner credentials.
        mid=str(uuid.uuid4());credentials=self.services['payments']['environment'];environment={**self.env,'AMQP_HOST':'127.0.0.1','AMQP_PORT':self.command(['port','rabbitmq','5672']).strip().split(':')[-1],'AMQP_VHOST':'/fcg','AMQP_USERNAME':credentials['RabbitMq__Username'],'AMQP_PASSWORD':credentials['RabbitMq__Password'],'EXCHANGE':'','ROUTING_KEY':queue,'MESSAGE_BODY':json.dumps({**envelope,'messageId':mid}),'MESSAGE_ID':mid,'CORRELATION_ID':envelope['message']['orderId'],'CONTENT_TYPE':'application/vnd.masstransit+json'}
        self.raw([str(self.args.dotnet),str(ROOT/'tests/rabbitmq/Probes/bin/Release/net10.0/RabbitMq.Probes.dll'),'publish-confirm'],env=environment);return mid

    def policy_race(self,admin,user,first_limit):
        self.command(['stop','payments']);order=self.accepted(admin,user,'policy-race-'+str(first_limit),promotion=True);oid=str(uuid.UUID(order['orderId']))
        poll(lambda:self.queue('fcg-payments-orders').get('messages_ready',0),lambda n:n==1,'original policy-race order queued')
        envelope=json.loads(self.peek('fcg-payments-orders')['payload']);ensure(envelope['message']['orderId']==oid,'Wrong original order selected')
        queues=[self.worker(first_limit,str(first_limit)+'-first'),self.worker(0 if first_limit==500 else 500,str(first_limit)+'-second')]
        with DatabaseGate(self,'payments',"SELECT pg_advisory_xact_lock(hashtextextended('order:"+oid+"',0))"):
            ids=[self.send_queue(queues[0],envelope)];self.blocked('payments',1,'order:'+oid)
            ids.append(self.send_queue(queues[1],envelope));waiters=self.blocked('payments',2,'order:'+oid)
        for mid in ids:poll(lambda:self.db('payments',f"SELECT count(*) FROM \"InboxState\" WHERE \"MessageId\"='{mid}' AND \"Delivered\" IS NOT NULL"),lambda n:n=='1','both policy workers completed')
        stored=self.db('payments',f"SELECT row_to_json(d) FROM payment_decisions d WHERE order_id='{oid}'");decision=json.loads(stored)
        expected='Approved' if Decimal(str(decision['approval_limit']))==500 else 'Rejected'
        ensure(decision['status']==expected and Decimal(str(decision['approval_limit'])) in (0,500),'Winner policy/status inconsistent')
        result=self.assert_effect(user,order,expected)
        ensure(self.db('payments',f"SELECT count(*) FROM payment_decisions WHERE order_id='{oid}'")=='1','First decision was duplicated')
        self.checks['two_real_policies_first_committed_winner_'+str(first_limit)]={'order_id':oid,'status':expected,'approval_limit':decision['approval_limit'],'observed_lock_waiters':waiters}
        self.stop_workers();self.restart_main_payments()
        original_mid=str(uuid.UUID(envelope['messageId']))
        poll(lambda:self.db('payments',f"SELECT count(*) FROM \"InboxState\" WHERE \"MessageId\"='{original_mid}' AND \"Delivered\" IS NOT NULL"),lambda n:n=='1','normal host commits same queued original order')
        ensure(json.loads(self.db('payments',f"SELECT \"Payload\" FROM \"Results\" WHERE \"OrderId\"='{oid}'"),parse_float=Decimal)==result,'Normal policy changed race winner')
        contradictory={**result,'price':float(result['price']),'status':'Rejected' if expected=='Approved' else 'Approved'}
        if contradictory['status']=='Rejected':contradictory['reason']='AmountAboveLimit'
        else:contradictory.pop('reason',None)
        fake={'messageType':['urn:message:FCG.Contracts:PaymentProcessedEvent'],'message':contradictory,'headers':{}}
        for queue in ('fcg-catalog-payments','fcg-notifications-payments'):self.send_queue(queue,fake)
        for owner in ('catalog','notifications'):
            poll(lambda:self.db(owner,f"SELECT count(*) FROM \"MessageConflicts\" WHERE \"BusinessId\"='{oid}' AND \"Reason\"='DivergentPaymentResult'"),lambda n:n=='1','contradictory result quarantined '+owner)
        self.assert_effect(user,order,expected);self.checks['contradictory_terminal_preserves_both_consumers_'+expected]=True

    def restart_main_payments(self):
        # Runtime restart of the existing owned host. Do not re-run the strict topology bootstrap
        # against the extra endpoint queues created deliberately by these disposable race workers.
        cid=self.ids['payments'];obj=json.loads(self.raw(self.docker+['inspect',cid]).stdout)[0]
        ensure(obj['Config']['Labels'].get('com.docker.compose.project')==self.project and self.resources[cid]['created']==obj['Created'],'Main Payments ownership changed')
        self.raw(self.docker+['start',cid]);self.capture()
        poll(lambda:self.http('payments','/health/ready')[0],lambda n:n==200,'same normal Payments host restart')

    def stop_workers(self):
        for cid in self.auxiliary:
            obj=json.loads(self.raw(self.docker+['inspect',cid]).stdout)[0]
            ensure(obj['Config']['Labels'].get('io.fcg.concurrency.run')==self.run_id and self.resources[cid]['created']==obj['Created'],'Auxiliary worker ownership changed')
            self.raw(self.docker+['rm','-f',cid])
        self.auxiliary=[]

    def execute(self):
        admin,uid,user=self.setup();self.simultaneous(admin,user);self.snapshot(admin,user);self.snapshot(admin,user,expire=True);self.policy_race(admin,user,500);self.policy_race(admin,user,0)
        winners={self.checks['two_real_policies_first_committed_winner_'+str(limit)]['status'] for limit in (500,0)}
        ensure(winners=={'Approved','Rejected'},'Controlled policy races must exercise both winners')
        for owner in OWNERS:
            logs=self.logs(owner);ensure(all(secret.lower() not in logs.lower() and urllib.parse.quote(secret,safe='').lower() not in logs.lower() for secret in self.secrets),'Private logs in concurrency proof')
        self.result['status']='passed'

    def command(self,args):
        result=self.raw(self.compose+args,check=False)
        if result.returncode:
            diagnostic=result.stderr
            for secret in getattr(self,'secrets',[]): diagnostic=diagnostic.replace(secret,'<redacted>')
            if hasattr(self,'result'):(self.args.output/'COMMAND-FAILURE.log').write_text(diagnostic)
            raise JourneyFailure('Owned Compose command failed: '+args[0])
        return result.stdout

    def cleanup(self):
        self.stop_workers();super().cleanup()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--env-file',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--dotnet',type=Path,required=True);parser.add_argument('--docker-context',default='orbstack')
    args=parser.parse_args();args.allow_owned_fault_fixtures=False;args.output.mkdir(parents=True,exist_ok=False);proof=None
    try:proof=Concurrency(args);proof.execute()
    except Exception as error:
        if proof:proof.result['error_type']=type(error).__name__
        print('Concurrency proof failed: '+type(error).__name__,file=sys.stderr);raise
    finally:
        if proof:
            try:proof.cleanup()
            finally:
                proof.result['resources']=proof.resources;proof.result['infrastructure']=proof.infrastructure
                (args.output/'CONCURRENCY.json').write_text(json.dumps(proof.result,indent=2)+'\n');print(json.dumps({'status':proof.result['status'],'checks':list(proof.checks),'cleanup_complete':proof.result.get('cleanup_complete',False)}))

if __name__=='__main__':main()
