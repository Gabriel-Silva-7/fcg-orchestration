#!/usr/bin/env python3
"""Supervise owned loopback Service forwards; fail visibly after bounded recovery."""
import argparse
import base64
import json
from pathlib import Path
import signal
import socket
import sys
import time
import urllib.error
import urllib.request
import uuid
from render import verify_rendered,load_environment,OWNERS
from runtime import Runtime,ensure,RuntimeFailure
from port_forward import PortForward

class Access:
    def __init__(self,runtime,config,ports,status_file,recovery_seconds=90,max_attempts=5):
        self.runtime=runtime;self.config=config;self.ports=ports.copy();self.status_file=status_file;self.recovery_seconds=recovery_seconds;self.max_attempts=max_attempts
        self.forwards={};self.history=[];self.outages={};self.attempts={};self.stopping=False;self.run_id=uuid.uuid4().hex;self.published_state=None
        self.report={'run_id':self.run_id,'namespace':runtime.namespace,'context':runtime.context,'state':'starting','ports':{},'children':{},'history':self.history}
    def preflight(self):
        ensure(not self.status_file.exists(),'Access status file already exists')
        ensure(len([port for port in self.ports.values() if port])==len(set(port for port in self.ports.values() if port)),'Requested ports overlap')
        held=[]
        try:
            for service,port in self.ports.items():
                if not port:continue
                sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM);held.append(sock)
                try:sock.bind(('127.0.0.1',port))
                except OSError:raise RuntimeFailure('Loopback port already occupied: '+service) from None
        finally:
            for sock in held:sock.close()
        namespace=self.runtime.get('namespace',self.runtime.namespace)
        ensure(namespace['metadata']['labels'].get('io.fcg.run')==self.runtime.meta['run_id'],'Access namespace belongs to another environment')
    def ready(self,service):
        port=self.ports[service]
        if service=='rabbitmq':
            env=self.config['services']['rabbitmq']['environment'];credential=(env['RABBITMQ_DEFAULT_USER']+':'+env['RABBITMQ_DEFAULT_PASS']).encode()
            request=urllib.request.Request('http://127.0.0.1:'+str(port)+'/api/overview',headers={'Authorization':'Basic '+base64.b64encode(credential).decode()})
        else:request=urllib.request.Request('http://127.0.0.1:'+str(port)+'/health/ready')
        try:
            with urllib.request.urlopen(request,timeout=2) as response:return response.status==200
        except (OSError,urllib.error.URLError):return False
    def publish(self,state):
        self.report.update({'state':state,'ports':self.ports.copy(),'children':{owner:forward.process.pid for owner,forward in self.forwards.items()},'attempts':self.attempts.copy()})
        if state!=self.published_state:
            print(json.dumps({'state':state,'namespace':self.runtime.namespace,'ports':self.ports}),flush=True);self.published_state=state
        self.status_file.parent.mkdir(parents=True,exist_ok=True)
        temporary=self.status_file.with_name(self.status_file.name+'.'+self.run_id+'.tmp');temporary.write_text(json.dumps(self.report,indent=2)+'\n');temporary.chmod(0o600);temporary.replace(self.status_file)
    def stop(self,*_):self.stopping=True
    def close(self):
        for forward in self.forwards.values():forward.close()
        self.forwards={}
    def run(self):
        self.preflight();self.publish('starting')
        try:
            while not self.stopping:
                all_ready=True
                for service in self.ports:
                    if self.stopping:break
                    forward=self.forwards.get(service);now=time.monotonic()
                    if forward is None or forward.process.poll() is not None:
                        self.outages.setdefault(service,now);self.attempts[service]=self.attempts.get(service,0)+1
                        ensure(self.attempts[service]<=self.max_attempts and now-self.outages[service]<=self.recovery_seconds,'Service forward recovery budget exhausted: '+service)
                        if forward:forward.close();self.forwards.pop(service)
                        try:
                            forward=PortForward(self.runtime,service,15672 if service=='rabbitmq' else 80,self.ports[service],min(20,max(.1,self.recovery_seconds-(now-self.outages[service]))));self.forwards[service]=forward;self.ports[service]=forward.port
                            self.history.append({'service':service,'pid':forward.process.pid,'pod_uid':forward.pod_uid,'port':forward.port,'event':'reconnected' if any(item['service']==service for item in self.history) else 'started'})
                        except RuntimeFailure:
                            all_ready=False;continue
                    if self.ready(service):self.outages.pop(service,None);self.attempts[service]=0
                    else:
                        all_ready=False;self.outages.setdefault(service,now)
                        ensure(now-self.outages[service]<=self.recovery_seconds,'Service readiness recovery budget exhausted: '+service)
                self.publish('ready' if all_ready else 'recovering')
                time.sleep(.25)
            self.report['reason']='signal'
        except Exception:
            self.report['reason']='bounded_failure';self.publish('failed');raise
        finally:
            self.close();self.publish('stopped' if self.stopping else 'failed')


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--rendered',type=Path,required=True);parser.add_argument('--tools',type=Path,required=True);parser.add_argument('--status-file',type=Path,required=True)
    for owner,port in [('users',8081),('catalog',8082),('payments',8083),('notifications',8084),('rabbitmq',15672)]:parser.add_argument('--'+owner+'-port',type=int,default=port)
    parser.add_argument('--recovery-seconds',type=int,default=90);parser.add_argument('--max-attempts',type=int,default=5)
    args=parser.parse_args();ensure(all(0<=getattr(args,o+'_port')<=65535 for o in (*OWNERS,'rabbitmq')),'Invalid loopback port');ensure(1<=args.recovery_seconds<=180 and 1<=args.max_attempts<=10,'Invalid bounded recovery policy')
    verify_rendered(args.rendered);runtime=Runtime(args.rendered,args.tools);config,_=load_environment(Path(runtime.meta['env_file']),runtime.meta['docker_context'])
    access=Access(runtime,config,{o:getattr(args,o+'_port') for o in (*OWNERS,'rabbitmq')},args.status_file,args.recovery_seconds,args.max_attempts)
    for signum in (signal.SIGINT,signal.SIGTERM):signal.signal(signum,access.stop)
    access.run()

if __name__=='__main__':
    try:main()
    except Exception as error:print('Supervised access failed: '+(str(error) if isinstance(error,RuntimeFailure) else type(error).__name__),file=sys.stderr);sys.exit(1)
