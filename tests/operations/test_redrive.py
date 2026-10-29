"""Transport recovery against an owned real broker. No queue purge or auto-ack."""
import base64
import functools
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault('DOTNET', os.environ.get('DOTNET_HOST_PATH', 'dotnet'))
sys.path.insert(0, str(ROOT/'tests/rabbitmq'))
from support import Broker, DOTNET, LABEL, segment

TOOL = ROOT/'tools/FCG.Messaging.Operations/bin/Release/net10.0/FCG.Messaging.Operations.dll'
QUEUE = 'fcg-catalog-payments'

@functools.cache
def build_fixture():
    for project in ['scripts/rabbitmq/bootstrap/RabbitMq.Bootstrap.csproj','tests/rabbitmq/Probes/RabbitMq.Probes.csproj', 'tools/FCG.Messaging.Operations/FCG.Messaging.Operations.csproj']:
        for args in [['restore',project,'--locked-mode'],['build',project,'-c','Release','--no-restore']]:
            result=subprocess.run([DOTNET,*args],cwd=ROOT,capture_output=True,text=True,timeout=60)
            if result.returncode: raise AssertionError('Recovery fixture build failed: '+result.stderr[-1500:]+result.stdout[-1500:])


class RedriveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_fixture()

    def setUp(self):
        self.b=Broker();self.addCleanup(self.b.close);self.b.start();self.b.require_bootstrap()
        self.b.api('PUT','/api/queues/%2Ffcg/'+QUEUE+'_error',{'durable':True,'auto_delete':False,'arguments':{}})

    def publish(self, message_id, payload):
        result=self.b.probe('publish-confirm',owner='CATALOG',EXCHANGE='',ROUTING_KEY=QUEUE+'_error',MESSAGE_ID=message_id,MESSAGE_BODY=payload,MESSAGE_HEADER="PRIVATE-HEADER-FIXTURE")
        self.assertEqual(0,result.returncode)

    def tool(self,message_id,apply=None,limit=None,**overrides):
        env=self.b.env.copy();env.update(AMQP_HOST='127.0.0.1',AMQP_PORT=self.b.port,AMQP_VHOST='/fcg',
            AMQP_USERNAME=self.b.values['FCG_CATALOG_RABBITMQ_USER'],AMQP_PASSWORD=self.b.values['FCG_CATALOG_RABBITMQ_PASSWORD'])
        env.update(overrides)
        arguments=['error-redrive','--queue',QUEUE,'--message-id',message_id]
        if apply is not None:arguments.append('--apply' if apply else '--dry-run')
        if limit is not None:arguments+=['--scan-limit',str(limit)]
        image=os.environ.get('FCG_OPERATIONS_IMAGE')
        if image:
            env.update(AMQP_HOST='rabbitmq',AMQP_PORT='5672')
            command=self.b.docker+['run','--rm','--label',LABEL+'='+self.b.name,'--network',self.b.network]
            for name in ('AMQP_HOST','AMQP_PORT','AMQP_VHOST','AMQP_USERNAME','AMQP_PASSWORD'):command+=['--env',name]
            command += [image,*arguments]
        else:command=[DOTNET,str(TOOL),*arguments]
        return self.b.run(command,check=False,env=env,timeout=40)

    def count(self,queue):
        _,body=self.b.api('POST','/api/queues/%2Ffcg/'+segment(queue)+'/get',{'count':100,'ackmode':'ack_requeue_true','encoding':'auto','truncate':50000})
        return len(body)

    def test_dry_run_then_apply_preserves_selected_message_and_requeues_unselected(self):
        other,target=str(uuid.uuid4()),str(uuid.uuid4())
        self.publish(other,'UNSELECTED-FIXTURE');self.publish(target,'PRIVATE-PAYLOAD-FIXTURE')
        limited=self.tool(target,limit=1);self.assertEqual(3,limited.returncode);self.assertEqual(1,json.loads(limited.stdout)['examined'])
        self.assertEqual(2,self.count(QUEUE+'_error'))
        dry=self.tool(target);self.assertEqual(0,dry.returncode)
        report=json.loads(dry.stdout);self.assertTrue(report['dryRun']);self.assertTrue(report['found'])
        self.assertNotIn('PRIVATE-PAYLOAD-FIXTURE',dry.stdout+dry.stderr)
        self.assertEqual(2,self.count(QUEUE+'_error'));self.assertEqual(0,self.count(QUEUE))
        applied=self.tool(target,True);self.assertEqual(0,applied.returncode)
        report=json.loads(applied.stdout);self.assertFalse(report['dryRun']);self.assertTrue(report['confirmed']);self.assertEqual(target,report['messageId'])
        self.assertEqual(1,self.count(QUEUE+'_error'));self.assertEqual(1,self.count(QUEUE))
        result=self.b.probe('get-ack',owner='CATALOG',QUEUE=QUEUE);self.assertEqual(0,result.returncode)
        message=json.loads(result.stdout);self.assertEqual(target,message['messageId']);self.assertEqual('PRIVATE-PAYLOAD-FIXTURE',message['body'])
        self.assertEqual(base64.b64encode(b'PRIVATE-HEADER-FIXTURE').decode(),message['headers']['fixture'])
        self.assertNotIn('PRIVATE-HEADER-FIXTURE',applied.stdout+applied.stderr)
        self.assertTrue(message['persistent']);self.assertEqual('11111111-1111-1111-1111-111111111111',message['correlationId'])
        self.assertEqual(3,self.tool(target,True).returncode)
        self.assertEqual(1,self.count(QUEUE+'_error'))

    def test_missing_original_route_never_acknowledges_or_drops_error_message(self):
        target=str(uuid.uuid4());self.publish(target,'PRIVATE-PAYLOAD-FIXTURE')
        self.b.api('DELETE','/api/queues/%2Ffcg/'+QUEUE)
        result=self.tool(target,True);self.assertEqual(1,result.returncode)
        self.assertEqual(1,self.count(QUEUE+'_error'));self.assertNotIn('PRIVATE-PAYLOAD-FIXTURE',result.stdout+result.stderr)

    def test_absent_error_queue_reports_not_found_without_creating_it(self):
        self.b.api('DELETE','/api/queues/%2Ffcg/'+QUEUE+'_error')
        result=self.tool(str(uuid.uuid4()));self.assertEqual(3,result.returncode)
        self.assertFalse(json.loads(result.stdout)['found'])
        status,_=self.b.api('GET','/api/queues/%2Ffcg/'+QUEUE+'_error',allow=(404,))
        self.assertEqual(404,status);self.assertEqual(0,self.count(QUEUE))

    def test_wrong_credentials_and_absent_id_leave_messages_untouched(self):
        target=str(uuid.uuid4());self.publish(target,'PRIVATE-PAYLOAD-FIXTURE')
        self.assertEqual(3,self.tool(str(uuid.uuid4())).returncode)
        result=self.tool(target,True,AMQP_PASSWORD='INCORRECT-PRIVATE-PASSWORD');self.assertEqual(1,result.returncode)
        self.assertNotIn('INCORRECT-PRIVATE-PASSWORD',result.stdout+result.stderr)
        self.assertEqual(1,self.count(QUEUE+'_error'));self.assertEqual(0,self.count(QUEUE))

class RedriveArgumentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_fixture()

    def test_invalid_arguments_are_safe_without_loading_credentials(self):
        for args in [[],['error-redrive','--queue','arbitrary','--message-id',str(uuid.uuid4())],
                     ['error-redrive','--queue',QUEUE,'--message-id','PRIVATE-INVALID-ID'],
                     ['error-redrive','--queue',QUEUE,'--message-id',str(uuid.uuid4()),'--dry-run','--apply']]:
            result=subprocess.run([DOTNET,str(TOOL),*args],capture_output=True,text=True,timeout=10)
            self.assertEqual(2,result.returncode);self.assertNotIn('PRIVATE-INVALID-ID',result.stdout+result.stderr)
