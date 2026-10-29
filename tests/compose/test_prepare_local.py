"""Real Git/RSA/filesystem checks for private preparation and overwrite refusal."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
OWNERS=('users','catalog','payments','notifications')

class LocalPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='fcg-prepare-test-')
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)
        self.paths={}
        for owner in OWNERS:
            path=self.folder/(owner+" source $ ' quoted");path.mkdir()
            (path/'Dockerfile').write_text('FROM scratch\nLABEL io.fcg.prepare-test='+self.folder.name+'\n')
            self.git(path,'init','-q')
            self.git(path,'remote','add','origin','https://example.invalid/'+owner+'.git')
            self.git(path,'add','Dockerfile')
            self.git(path,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture')
            self.paths[owner]=path
        self.output=self.folder/'private environment'

    def git(self,path,*args):
        return subprocess.run(['git','-C',str(path),*args],capture_output=True,text=True,check=True).stdout.strip()

    def invoke(self):
        args=['python3',str(ROOT/'scripts/prepare-local.py'),'--output',str(self.output)]
        for owner,path in self.paths.items():args+=['--'+owner,str(path)]
        return subprocess.run(args,capture_output=True,text=True,timeout=45)

    def test_clean_sources_keys_private_env_and_refusal_to_overwrite_existing_environment(self):
        run=self.invoke();self.assertEqual(0,run.returncode,run.stderr)
        manifest=json.loads((self.output/'sources.json').read_text())
        for owner,path in self.paths.items():
            self.assertEqual(self.git(path,'rev-parse','HEAD'),manifest['services'][owner]['sha'])
        self.assertEqual(0o700,stat.S_IMODE(self.output.stat().st_mode))
        self.assertEqual(0o600,stat.S_IMODE((self.output/'demo.env').stat().st_mode))
        env=(self.output/'demo.env').read_text()
        self.assertNotIn('ADMIN_BOOTSTRAP_PASSWORD=',run.stdout)
        key=self.output/'private.pem';original=hashlib.sha256(key.read_bytes()).hexdigest()
        public=subprocess.run(['openssl','pkey','-in',str(key),'-pubout'],capture_output=True,check=True).stdout
        self.assertEqual(public,(self.output/'public.pem').read_bytes())
        again=self.invoke();self.assertNotEqual(0,again.returncode)
        self.assertEqual(original,hashlib.sha256(key.read_bytes()).hexdigest())
        self.assertEqual(env,(self.output/'demo.env').read_text())
        # Real Compose parser must preserve quoted source paths including dollar and apostrophe.
        clean=os.environ.copy()
        for line in env.splitlines():clean.pop(line.split('=',1)[0],None)
        config=subprocess.run(['docker','--context','orbstack','compose','--env-file',str(self.output/'demo.env'),'-f',str(ROOT/'docker-compose.yml'),'config','--format','json'],capture_output=True,text=True,env=clean)
        self.assertEqual(0,config.returncode,'Prepared configuration must render without exposing it')
        services=json.loads(config.stdout)['services']
        # config serializes dollars as $$ for safe re-interpolation; exercise an actual build as well.
        self.assertEqual(str(self.paths['users'].resolve()).replace('$','$$'),services['users']['build']['context'])
        image=services['users']['image']
        def remove_owned_image():
            inspected=subprocess.run(['docker','--context','orbstack','image','inspect',image],capture_output=True,text=True)
            if inspected.returncode==0:
                labels=json.loads(inspected.stdout)[0]['Config']['Labels']
                self.assertEqual(self.folder.name,labels['io.fcg.prepare-test'])
                subprocess.run(['docker','--context','orbstack','image','rm',image],capture_output=True,check=True)
        self.addCleanup(remove_owned_image)
        built=subprocess.run(['docker','--context','orbstack','compose','--env-file',str(self.output/'demo.env'),'-f',str(ROOT/'docker-compose.yml'),'build','users'],capture_output=True,text=True,env=clean,timeout=60)
        self.assertEqual(0,built.returncode,'Actual build must resolve the quoted source path')
        private_password=services['users']['environment']['AdminBootstrap__Password']
        self.assertGreater(len(private_password),20)
        self.assertNotIn(private_password,run.stdout+run.stderr+(self.output/'sources.json').read_text())

    def test_existing_journey_report_is_never_overwritten_or_reused_as_a_new_success(self):
        report=self.folder/'existing.json'; report.write_text('{"status":"passed","run_id":"previous"}\n')
        run=subprocess.run(['sh',str(ROOT/'scripts/e2e.sh'),'--target','compose','--env-file',str(self.folder/'missing.env'),
                            '--output',str(report)],capture_output=True,text=True,timeout=10)
        self.assertNotEqual(0,run.returncode)
        self.assertIn('already exists',run.stderr)
        self.assertEqual('{"status":"passed","run_id":"previous"}\n',report.read_text())

    def test_dirty_source_is_refused_before_creating_keys_or_environment(self):
        (self.paths['catalog']/'Dockerfile').write_text('FROM changed\n')
        run=self.invoke();self.assertNotEqual(0,run.returncode)
        self.assertIn('must be clean',run.stderr)
        self.assertFalse(self.output.exists())

if __name__=='__main__':unittest.main()
