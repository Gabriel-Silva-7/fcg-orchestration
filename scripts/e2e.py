#!/usr/bin/env python3
"""Bounded, real four-service Compose journey. Creates unique records; never resets data."""
import argparse
import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
OWNERS = ('users','catalog','payments','notifications')

class JourneyFailure(Exception): pass

def ensure(condition, message):
    if not condition: raise JourneyFailure(message)

def poll(read, accept, label, seconds=90):
    deadline = time.monotonic()+seconds
    while True:
        value = read()
        if accept(value): return value
        if time.monotonic() >= deadline: raise JourneyFailure('Timeout waiting for '+label)
        time.sleep(.25)

class Journey:
    def __init__(self, args):
        self.args=args; self.run_id=uuid.uuid4().hex
        # Explicit env-file is authoritative; never let inherited shell variables silently select another project/source/secret.
        self.env=os.environ.copy()
        for line in args.env_file.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'): self.env.pop(line.split('=',1)[0].strip(),None)
        self.compose=['docker','--context',args.docker_context,'compose','--env-file',str(args.env_file.resolve()),'-f',str(ROOT/'docker-compose.yml')]
        self.config=json.loads(self.command(['config','--format','json']))
        self.services=self.config['services']; self.project=self.config['name']
        self.ids={name:self.command(['ps','-a','-q',name]).strip() for name in (*OWNERS,'postgres','rabbitmq','rabbitmq-bootstrap')}
        ensure(all(self.ids.values()), 'All four services and infrastructure must exist before E2E')
        self.urls={}
        for owner in OWNERS:
            port=self.command(['port',owner,'8080']).strip()
            ensure(re.fullmatch(r'127\.0\.0\.1:[0-9]+',port) is not None,'Only loopback application ports are supported')
            self.urls[owner]='http://'+port
        self.secrets=[]
        for service in self.services.values():
            for key,value in service.get('environment',{}).items():
                if any(part in key.lower() for part in ('password','connectionstrings')) and value: self.secrets.append(value)

    def command(self, args):
        result=subprocess.run(self.compose+args, env=self.env, capture_output=True, text=True, timeout=30)
        ensure(result.returncode==0,'Compose operation failed: '+args[0])
        return result.stdout

    def http(self, owner, path, method='GET', body=None, token=None):
        headers={'Content-Type':'application/json'}
        if token: headers['Authorization']='Bearer '+token
        request=urllib.request.Request(self.urls[owner]+path,data=json.dumps(body).encode() if body is not None else None,headers=headers,method=method)
        try:
            with urllib.request.urlopen(request, timeout=8) as response: code,data=response.status,response.read()
        except urllib.error.HTTPError as error: code,data=error.code,error.read()
        except (OSError, urllib.error.URLError): return 0,None
        try: payload=json.loads(data, parse_float=Decimal)
        except (json.JSONDecodeError,UnicodeDecodeError): payload=data.decode(errors='replace')
        return code,payload

    def db(self, owner, sql):
        # Read-only assertions through the owner's role; no foreign table reads by applications and no test mutations.
        password=self.config['services']['postgres']['environment']['FCG_'+owner.upper()+'_DB_PASSWORD']
        return self.command(['exec','-T','-e','PGPASSWORD='+password,'postgres','psql','-h','127.0.0.1','-U','fcg_'+owner,'-d','fcg_'+owner,
                             '-v','ON_ERROR_STOP=1','-Atc','BEGIN READ ONLY; '+sql+'; COMMIT']).removeprefix('BEGIN\n').removesuffix('COMMIT\n').strip()

    def logs(self, owner): return self.command(['logs','--no-color',owner])

    def login(self, email, password):
        status, body=self.http('users','/api/v1/auth/login','POST',{'email':email,'password':password})
        ensure(status==200,'Real Users login failed')
        token=body['accessToken']; self.secrets.extend([email,password,token]); return token

    def register(self, suffix):
        email='e2e-'+self.run_id+'-'+suffix+'@example.com'; password=secrets.token_hex(16)+'!Aa1'
        code,body=self.http('users','/api/v1/auth/register','POST',{'name':'E2E '+suffix,'email':email,'password':password})
        ensure(code==201,'Unique real HTTP registration failed')
        self.secrets.extend([email,password]); return body['id'], self.login(email,password)

    def notification(self, kind, business_id):
        ensure(kind in ('Welcome','PurchaseConfirmation'),'Unsupported assertion kind')
        # All interpolated values below are locally generated/canonical GUIDs and fixed enum names.
        business_id=str(uuid.UUID(business_id))
        sql=f'''SELECT count(*) FROM "NotificationIntents" WHERE "Kind"='{kind}' AND "BusinessId"='{business_id}' AND "State"='Sent' '''
        poll(lambda:self.db('notifications',sql),lambda count:count=='1',kind+' persisted Sent')
        logs=poll(lambda:self.logs('notifications'),lambda text:any('[E-MAIL SIMULADO] '+kind in line and business_id in line for line in text.splitlines()),kind+' emitted in console')
        return True

    def execute(self):
        for owner in OWNERS:
            poll(lambda:self.http(owner,'/health/ready')[0],lambda code:code==200,owner+' readiness')
        state=json.loads(subprocess.run(['docker','--context',self.args.docker_context,'inspect',self.ids['rabbitmq-bootstrap']],capture_output=True,text=True,check=True).stdout)[0]['State']
        ensure(state['Status']=='exited' and state['ExitCode']==0,'Topology gate must have completed successfully')
        env=self.services['users']['environment']
        ensure(env['AdminBootstrap__Enabled']=='true','E2E needs an explicitly provisioned demonstration administrator')
        admin=self.login(env['AdminBootstrap__Email'],env['AdminBootstrap__Password'])
        user_id,user=self.register('owner'); other_id,other=self.register('other')
        self.notification('Welcome',user_id); self.notification('Welcome',other_id)
        games={}
        for suffix,price in [('approved',49.90),('rejected',600.00)]:
            code,game=self.http('catalog','/api/v1/games','POST',{'title':'E2E '+self.run_id+' '+suffix,'description':'Isolated real journey','basePrice':price},admin)
            ensure(code==201,'Users-issued administrator token must create real Catalog game')
            games[suffix]=game['id']
        ensure(self.http('catalog','/api/v1/games','POST',{'title':'Denied','basePrice':1},user)[0]==403,'User token must not create games')
        orders={}
        for label,expected,price in [('approved','Approved',Decimal('49.90')),('rejected','Rejected',Decimal('600.00'))]:
            code,accepted=self.http('catalog','/api/v1/me/library','POST',{'gameId':games[label]},user)
            ensure(code==202 and accepted['price']==price and accepted['status']=='Pending','Purchase must atomically accept server snapshot')
            order_id=str(uuid.UUID(accepted['orderId'])); orders[label]=order_id
            code,order=poll(lambda:self.http('catalog','/api/v1/me/orders/'+order_id,token=user),
                            lambda response:response[0]==200 and response[1]['status'] in ('Approved','Rejected'),expected+' order')
            ensure(order['status']==expected and order['price']==price and order['gameId']==games[label] and order['completedAtUtc'] is not None,'Catalog terminal order must match the actual payment')
            ensure(order['rejectionReason']==(None if label=='approved' else 'AmountAboveLimit'),'Financial rejection reason must be canonical')
            ensure(self.http('catalog','/api/v1/me/orders/'+order_id,token=other)[0]==404,'Another user must not observe the order')
            result_payload=self.db('payments',f"SELECT \"Payload\" FROM \"Results\" WHERE \"OrderId\"='{order_id}'")
            ensure(bool(result_payload),'Payments must persist its real result')
            recorded=json.loads(result_payload,parse_float=Decimal)
            ensure(recorded['orderId']==order_id and recorded['userId']==user_id and recorded['gameId']==games[label]
                   and recorded['price']==price and recorded['status']==expected,'Result must originate in the actual Payments process')
            instant=lambda value:datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
            ensure(instant(recorded['processedAtUtc'])==instant(order['completedAtUtc']),'Catalog must preserve Payments completion instant')
            observation=poll(lambda:self.db('notifications',f'''SELECT "Status" FROM "PaymentObservations" WHERE "OrderId"='{order_id}' '''),bool,'Notifications independent payment copy')
            ensure(observation==expected,'Notifications must observe the same actual result')
        self.notification('PurchaseConfirmation',orders['approved'])
        code,library=self.http('catalog','/api/v1/me/library',token=user)
        ensure(code==200 and library['totalCount']==1 and len(library['items'])==1,'Only Approved adds to library')
        entry=library['items'][0]
        ensure(set(entry)=={'gameId','title','acquiredAt','acquisitionPrice'} and entry['gameId']==games['approved'] and entry['acquisitionPrice']==Decimal('49.90'),'Acquisition must preserve legacy response and order price')
        code,own_orders=self.http('catalog','/api/v1/me/orders',token=user)
        ensure(code==200 and own_orders['totalCount']==2,'Owner must see both actual orders')
        ensure(self.db('notifications',f'''SELECT count(*) FROM "NotificationIntents" WHERE "Kind"='PurchaseConfirmation' AND "BusinessId"='{orders['rejected']}' ''')=='0','Rejected must not create confirmation')
        ensure(not any('[E-MAIL SIMULADO] PurchaseConfirmation' in line and orders['rejected'] in line for line in self.logs('notifications').splitlines()),'Rejected must not emit confirmation')
        for path in ('/api/v1/me/library','/api/v1/me/orders'):
            code,empty=self.http('catalog',path,token=other)
            ensure(code==200 and empty['totalCount']==0 and empty['items']==[],'Other owner lists must remain empty')
        # Both consumers committed before this negative assertion; no timing-only absence test.
        for owner in OWNERS:
            logs=self.logs(owner)
            ensure(all(value.lower() not in logs.lower() for value in self.secrets),'Production logs must omit full contact and credentials')
        images={}
        for owner in (*OWNERS,'rabbitmq-bootstrap','postgres','rabbitmq'):
            obj=json.loads(subprocess.run(['docker','--context',self.args.docker_context,'inspect',self.ids[owner]],capture_output=True,text=True,check=True).stdout)[0]
            ensure(obj['Config']['Labels']['com.docker.compose.project']==self.project,'Resource project identity changed')
            images[owner]={'container':obj['Id'],'image':obj['Image'],'reference':obj['Config']['Image']}
        result={'status':'passed','mode':'basic-four-real-services','run_id':self.run_id,'project':self.project,
                'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'user_id':user_id,'other_user_id':other_id,
                'games':games,'orders':orders,'images':images,
                'checks':['all-ready','topology-gate','users-token-catalog-admin','welcome-console-and-sent',
                          'approved-library-confirmation','rejected-without-acquisition-or-confirmation','owner-isolation','private-logs'],
                'limits':'Distributed authorization and remaining inherited capabilities are validated by later increments.'}
        if self.args.output:
            self.args.output.parent.mkdir(parents=True,exist_ok=True)
            with self.args.output.open('x') as stream: stream.write(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'status':'passed','run_id':self.run_id,'project':self.project,'orders':orders},indent=2))

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',choices=['compose'],required=True)
    parser.add_argument('--env-file',type=Path,required=True)
    parser.add_argument('--docker-context',default='orbstack')
    parser.add_argument('--output',type=Path)
    try:
        args=parser.parse_args()
        ensure(args.output is None or not args.output.exists(),'Evidence output already exists; choose a new path to avoid stale results')
        Journey(args).execute()
    except (JourneyFailure, OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # No subprocess output, response payload, config or original exception values in diagnostics.
        message=str(error) if isinstance(error,JourneyFailure) else type(error).__name__
        print('E2E failed: '+message,file=sys.stderr); sys.exit(1)
