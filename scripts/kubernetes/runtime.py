"""Private Kind runtime, explicit context and bounded operations. Never change global kubeconfig."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tarfile
import time


class RuntimeFailure(Exception):pass

def ensure(condition,message):
    if not condition:raise RuntimeFailure(message)


def poll(read,accept,label,seconds=120):
    end=time.monotonic()+seconds
    while True:
        value=read()
        if accept(value):return value
        if time.monotonic()>=end:raise RuntimeFailure('Timeout: '+label)
        time.sleep(.25)


class Runtime:
    def __init__(self,rendered,tools):
        self.directory=rendered.resolve();self.meta=json.loads((self.directory/'RENDER.json').read_text());self.tools=tools.resolve()
        self.root=Path(self.meta['root']);self.name=self.meta['cluster'];self.namespace=self.meta['namespace'];self.context=self.meta['context']
        self.kubeconfig=self.directory/'kubeconfig';self.docker=['docker','--context',self.meta['docker_context']]
        self.env={**os.environ,'DOCKER_CONTEXT':self.meta['docker_context'],'KIND_EXPERIMENTAL_PROVIDER':'docker','KUBECONFIG':str(self.kubeconfig)}
        self.kind=[str(self.tools/'kind')];self.kubectl=[str(self.tools/'kubectl'),'--kubeconfig',str(self.kubeconfig),'--context',self.context,'--namespace',self.namespace]
        self.node=None;self.volume_records={};self.network_record=None;self.image_records={}
        self.personal=Path.home()/'.kube/config';self.personal_hash=hashlib.sha256(self.personal.read_bytes()).hexdigest() if self.personal.exists() else None
        self.baseline_containers=self.run(self.docker+['ps','-aq']).stdout.split()
        self.baseline_volumes=self.run(self.docker+['volume','ls','-q']).stdout.split()
        self.baseline_kind_network=self.run(self.docker+['network','ls','-q','--filter','name=^kind$']).stdout.strip()
        self.ownership_file=self.directory/'OWNERSHIP.json'

    def run(self,args,input=None,check=True,timeout=180):
        try:r=subprocess.run(args,input=input,capture_output=True,text=True,env=self.env,timeout=timeout)
        except subprocess.TimeoutExpired:raise RuntimeFailure('Owned operation exceeded deadline') from None
        if check and r.returncode:
            # Stderr may contain API bodies/configuration. Keep it private; never copy to public evidence.
            (self.directory/'LAST-ERROR.private.log').write_text(r.stderr);(self.directory/'LAST-ERROR.private.log').chmod(0o600)
            raise RuntimeFailure('Owned operation failed: '+Path(args[0]).name)
        return r

    def kube(self,*args,input=None,check=True,timeout=180):return self.run(self.kubectl+list(args),input,check,timeout)
    def get(self,*args):return json.loads(self.kube('get',*args,'-o','json').stdout)

    def preflight(self):
        ensure(not self.kubeconfig.exists(),'Refusing to overwrite a kubeconfig')
        ensure(self.name not in self.run(self.kind+['get','clusters']).stdout.split(),'Cluster name already exists')
        info=json.loads(self.run(self.docker+['info','--format','{{json .}}']).stdout)
        ensure(info['Architecture']=='aarch64' or info['Architecture']=='arm64','Exercised node platform is linux/arm64')
        ensure(info['MemTotal']>=7*1024**3,'This workload needs the exercised memory budget')
        for cid in self.run(self.docker+['ps','-q']).stdout.split():
            obj=json.loads(self.run(self.docker+['inspect',cid]).stdout)[0];project=(obj['Config'].get('Labels') or {}).get('com.docker.compose.project','')
            ensure(not project.startswith('fcg-demo-'),'Stop/clean the owned full Compose proof before creating Kind')
        return {'architecture':info['Architecture'],'memory_bytes':info['MemTotal'],'cpus':info['NCPU']}

    def build_infrastructure(self):
        for owner,source in self.meta['image_sources'].items():
            self.run(self.docker+['pull',source]);self.run(self.docker+['tag',source,self.meta['images'][owner]])
        self.run(self.docker+['build','-f',str(self.root/'scripts/rabbitmq/bootstrap/Dockerfile'),'-t',self.meta['images']['bootstrap'],str(self.root)],timeout=300)
        references=[self.meta['images'][name] for name in ('postgres','rabbitmq','bootstrap','diagnostic')];archive=self.directory/'infrastructure-images.tar'
        self.run(self.docker+['image','save','--platform','linux/arm64','-o',str(archive),*references],timeout=180)
        with tarfile.open(archive) as saved:
            manifest=json.load(saved.extractfile('manifest.json'))
            for item in manifest:
                content=saved.extractfile(item['Config']).read();digest='sha256:'+hashlib.sha256(content).hexdigest()
                for reference in item['RepoTags']:
                    if reference not in references:continue
                    source=json.loads(self.run(self.docker+['image','inspect',reference]).stdout)[0]
                    config=json.loads(content);ensure(config['architecture']=='arm64' and config['os']=='linux','Exported image platform differs')
                    self.image_records[reference]={'docker_index_id':source['Id'],'exported_config_digest':digest,'rootfs':config['rootfs'],'entrypoint':config['config'].get('Entrypoint'),'source':self.meta['image_sources'].get(next((key for key in ('postgres','rabbitmq','diagnostic') if self.meta['images'][key]==reference),''),'orchestration-bootstrap')}
        ensure(set(self.image_records)==set(references),'Exported image references incomplete')
        return archive

    def create(self,archive):
        config=self.directory/'kind-config.json';config.write_text(json.dumps({'kind':'Cluster','apiVersion':'kind.x-k8s.io/v1alpha4','networking':{'apiServerAddress':'127.0.0.1'},'nodes':[{'role':'control-plane'}]}));config.chmod(0o600)
        # Record even a failed/partial creation so cleanup can verify any owned node.
        try:self.run(self.kind+['create','cluster','--name',self.name,'--image',self.meta['node_image'],'--kubeconfig',str(self.kubeconfig),'--config',str(config),'--wait','120s'],timeout=180)
        finally:self.capture_node()
        self.kubeconfig.chmod(0o600)
        self.run(self.kind+['load','image-archive',str(archive),'--name',self.name],timeout=180)
        self.kube('wait','--for=condition=Ready','nodes','--all','--timeout=120s')
        versions=json.loads(self.kube('version','-o','json').stdout)
        ensure(versions['clientVersion']['gitVersion']=='v1.37.0' and versions['serverVersion']['gitVersion']=='v1.37.0','Client/server version differs from exercised combination')
        return versions

    def capture_node(self):
        ids=self.run(self.docker+['ps','-aq','--filter','label=io.x-k8s.kind.cluster='+self.name]).stdout.split()
        if not ids:return
        ensure(len(ids)==1,'Expected one owned control-plane node')
        obj=json.loads(self.run(self.docker+['inspect',ids[0]]).stdout)[0]
        ensure(obj['Id'] not in self.baseline_containers,'Node predates this execution')
        self.node={'id':obj['Id'],'created':obj['Created'],'name':obj['Name'].lstrip('/'),'image':obj['Image']}
        for mount in obj['Mounts']:
            if mount['Type']=='volume':
                volume=json.loads(self.run(self.docker+['volume','inspect',mount['Name']]).stdout)[0]
                self.volume_records[mount['Name']]={'created':volume['CreatedAt'],'preexisting':mount['Name'] in self.baseline_volumes}
        networks=obj['NetworkSettings']['Networks']
        if 'kind' in networks:
            network=json.loads(self.run(self.docker+['network','inspect',networks['kind']['NetworkID']]).stdout)[0]
            self.network_record={'id':network['Id'],'created':network['Created'],'preexisting':bool(self.baseline_kind_network)}
        self.ownership_file.write_text(json.dumps({'node':self.node,'volumes':self.volume_records,'network':self.network_record,'baseline_containers':self.baseline_containers},indent=2)+'\n');self.ownership_file.chmod(0o600)

    def apply_phase(self,name):
        directory=self.directory/name
        ensure(directory.is_dir(),'Rendered phase missing')
        if name!='00-namespace':self.kube('apply','--dry-run=server','-f',str(directory))
        self.kube('apply','-f',str(directory))

    def infrastructure(self):
        self.apply_phase('00-namespace');self.apply_phase('01-infrastructure')
        for owner in ('postgres','rabbitmq'):self.kube('rollout','status','deployment/'+owner,'--timeout=180s')
        self.kube('wait','--for=jsonpath={.status.phase}=Bound','pvc','--all','--timeout=60s')
        self.apply_phase('02-bootstrap');self.kube('wait','--for=condition=complete','job/'+self.meta['bootstrap_job'],'--timeout=120s')

    def cleanup(self):
        if self.node:
            current=json.loads(self.run(self.docker+['inspect',self.node['id']]).stdout)[0]
            ensure(current['Created']==self.node['created'] and current['Config']['Labels'].get('io.x-k8s.kind.cluster')==self.name,'Owned node identity changed')
            self.run(self.kind+['delete','cluster','--name',self.name],timeout=120)
            ensure(not self.run(self.docker+['ps','-aq','--filter','label=io.x-k8s.kind.cluster='+self.name]).stdout.strip(),'Owned node still exists')
            for name,record in self.volume_records.items():
                if record['preexisting']:continue
                result=self.run(self.docker+['volume','inspect',name],check=False)
                if not result.returncode:
                    ensure(json.loads(result.stdout)[0]['CreatedAt']==record['created'],'Owned volume identity changed')
                    ensure(not self.run(self.docker+['ps','-aq','--filter','volume='+name]).stdout.strip(),'Volume is now in use')
                    self.run(self.docker+['volume','rm',name])
            if self.network_record and not self.network_record['preexisting']:
                result=self.run(self.docker+['network','inspect',self.network_record['id']],check=False)
                if not result.returncode:
                    network=json.loads(result.stdout)[0];ensure(network['Created']==self.network_record['created'] and not network['Containers'],'Shared network acquired other owners')
                    self.run(self.docker+['network','rm',self.network_record['id']])
        current=set(self.run(self.docker+['ps','-aq']).stdout.split());ensure(set(self.baseline_containers)<=current,'A preexisting container disappeared')
        personal_hash=hashlib.sha256(self.personal.read_bytes()).hexdigest() if self.personal.exists() else None
        ensure(personal_hash==self.personal_hash,'Personal kubeconfig changed')
        return {'complete':True,'personal_kubeconfig_unchanged':True,'preexisting_containers_preserved':True,'node':self.node,'volumes':self.volume_records,'network':self.network_record}
