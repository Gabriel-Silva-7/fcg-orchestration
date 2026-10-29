"""A single owned, bounded loopback port-forward. No global process cleanup."""
import os
import re
import select
import subprocess
import time
from runtime import ensure

class PortForward:
    def __init__(self,runtime,service,remote_port):
        self.process=subprocess.Popen(runtime.kubectl+['port-forward','--address','127.0.0.1','service/'+service,':'+str(remote_port)],env=runtime.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.port=None;data=b'';deadline=time.monotonic()+20
        try:
            while self.port is None:
                ensure(self.process.poll() is None,'Owned port-forward exited before listening')
                ensure(time.monotonic()<deadline,'Owned port-forward startup deadline exceeded')
                if select.select([self.process.stdout],[],[],.1)[0]:
                    data+=os.read(self.process.stdout.fileno(),4096)
                    found=re.search(rb'Forwarding from 127\.0\.0\.1:([0-9]+)',data)
                    if found:self.port=int(found.group(1))
        except Exception:self.close();raise
    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:self.process.kill();self.process.wait(timeout=5)
        self.process.stdout.close();self.process.stderr.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()
