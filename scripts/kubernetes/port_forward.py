"""Resolve a Service to its current Ready Pod and own a bounded loopback forward."""
import json
import os
import re
import select
import subprocess
import threading
import time
from runtime import ensure

class PortForward:
    def __init__(self,runtime,service,remote_port,local_port=0,startup_seconds=20):
        self.process=None;self.readers=[];self.port=None;deadline=time.monotonic()+startup_seconds
        try:
            # Native service forwarding may pick a Running Pod whose application is not ready yet.
            # Resolve the Service selector/targetPort explicitly and ignore terminating Pods.
            selected=None
            while selected is None:
                ensure(time.monotonic()<deadline,'Service has no current Ready Pod within startup budget')
                result=runtime.kube('get','service',service,'-o','json','--request-timeout=3s',check=False,timeout=5)
                if result.returncode:time.sleep(.2);continue
                resource=json.loads(result.stdout);selector=resource['spec'].get('selector',{});ensure(bool(selector),'Forward requires a selector-backed Service')
                ports=[item for item in resource['spec']['ports'] if item['port']==remote_port];ensure(len(ports)==1,'Requested Service port is ambiguous')
                result=runtime.kube('get','pods','-l',','.join(key+'='+value for key,value in sorted(selector.items())),'-o','json','--request-timeout=3s',check=False,timeout=5)
                if result.returncode:time.sleep(.2);continue
                pods=[pod for pod in json.loads(result.stdout)['items'] if not pod['metadata'].get('deletionTimestamp') and pod['status'].get('phase')=='Running' and any(c['type']=='Ready' and c['status']=='True' for c in pod['status'].get('conditions',[]))]
                if not pods:time.sleep(.2);continue
                selected=max(pods,key=lambda pod:pod['metadata']['creationTimestamp']);target=ports[0].get('targetPort',remote_port)
                if isinstance(target,str):
                    targets=[port['containerPort'] for container in selected['spec']['containers'] for port in container.get('ports',[]) if port.get('name')==target]
                    ensure(len(targets)==1,'Named Service targetPort is ambiguous');target=targets[0]
            self.pod_uid=selected['metadata']['uid'];self.pod_name=selected['metadata']['name']
            self.process=subprocess.Popen(runtime.kubectl+['port-forward','--address','127.0.0.1','pod/'+self.pod_name,str(local_port)+':'+str(target)],env=runtime.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            data=b''
            while self.port is None:
                ensure(self.process.poll() is None,'Owned port-forward exited before listening')
                ensure(time.monotonic()<deadline,'Owned port-forward startup deadline exceeded')
                if select.select([self.process.stdout],[],[],.1)[0]:
                    data+=os.read(self.process.stdout.fileno(),4096);found=re.search(rb'Forwarding from 127\.0\.0\.1:([0-9]+)',data)
                    if found:self.port=int(found.group(1))
            for stream in (self.process.stdout,self.process.stderr):
                def drain(source):
                    for _ in iter(source.readline,b''):pass
                thread=threading.Thread(target=drain,args=(stream,),daemon=True);thread.start();self.readers.append(thread)
        except Exception:self.close();raise
    def close(self):
        if self.process is None:return
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        for reader in self.readers:reader.join(timeout=2)
        self.process.stdout.close();self.process.stderr.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
