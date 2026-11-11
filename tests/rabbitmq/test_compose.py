"""The delivered Compose must run the same one-shot gate, not just raw Docker."""
import json
import subprocess
import unittest
from pathlib import Path
from support import Broker, LABEL, ROOT, wait


class ComposeIntegrationTests(unittest.TestCase):
    def test_delivered_compose_runs_broker_and_completed_bootstrap(self):
        b = Broker()
        self.addCleanup(b.close)
        folder = Path(b.directory.name)
        env_file = folder / "compose.env"
        values = dict(b.values, POSTGRES_PASSWORD="DEMO compose test", POSTGRES_PORT="0", RABBITMQ_PORT="0", RABBITMQ_MANAGEMENT_PORT="0")
        for owner in ("USERS", "CATALOG", "PAYMENTS", "NOTIFICATIONS"):
            values[f"FCG_{owner}_DB_PASSWORD"] = "DEMO compose database"
        env_file.write_text("\n".join(k+"='"+v.replace("'", "\\'")+"'" for k,v in values.items())+"\n")
        env_file.chmod(0o600)
        override = folder / "labels.json"
        override.write_text(json.dumps({"services":{name:{"labels":{LABEL:b.name}} for name in ("rabbitmq","rabbitmq-bootstrap")},
            "volumes":{"rabbitmq_data":{"labels":{LABEL:b.name}}}, "networks":{"default":{"labels":{LABEL:b.name}}}}))
        compose = b.docker + ["compose","--project-name",b.name,"--env-file",str(env_file),"-f",str(ROOT/"docker-compose.yml")]
        clean_env = b.env.copy()
        for key in values: clean_env.pop(key,None)
        # Config JSON contains fixture credentials: inspect only in memory, never print it.
        result = subprocess.run(compose+["config","--format","json"],env=clean_env,capture_output=True,text=True,timeout=20)
        self.assertEqual(0,result.returncode,"Compose must render with explicit test environment")
        config = json.loads(result.stdout)
        self.assertTrue(all(name in config["services"] for name in ("rabbitmq","rabbitmq-bootstrap")), "valid Compose lacks broker/bootstrap")
        self.assertEqual("service_healthy",config["services"]["rabbitmq-bootstrap"]["depends_on"]["rabbitmq"]["condition"])
        compose += ["-f",str(override)]
        def capture():
            for kind,listing in (("container",["ps","-aq"]),("network",["network","ls","-q"]),("volume",["volume","ls","-q"])):
                for identifier in b.run(b.docker+listing+["--filter","label="+LABEL+"="+b.name]).stdout.split():
                    obj=b.inspect(kind,identifier)
                    self.assertEqual(b.name,(obj.get("Config",{}).get("Labels",{}) if kind=="container" else obj["Labels"])["com.docker.compose.project"])
                    identity=b.record(kind,identifier)
                    if kind=="container" and obj["Config"]["Labels"]["com.docker.compose.service"]=="rabbitmq": b.container=identity
        try:
            startup=b.run(compose+["up","-d","--build","rabbitmq-bootstrap"],env=clean_env,timeout=600)
            (b.evidence/(b.name+"-compose.txt")).write_text(startup.stdout+startup.stderr)
            capture()
            # Docker wait also handles a one-shot that exited before we attach.
            bootstrap = next(identifier for identifier,item in b.resources["container"].items()
                             if item["labels"]["com.docker.compose.service"] == "rabbitmq-bootstrap")
            finished=b.run(b.docker+["wait",bootstrap],env=clean_env,timeout=90)
            self.assertEqual("0",finished.stdout.strip(),"bootstrap container must exit successfully")
            ports=b.resources["container"][b.container]["ports"]
            self.assertEqual("127.0.0.1",ports["15672/tcp"][0]["HostIp"])
            b.http="http://127.0.0.1:"+ports["15672/tcp"][0]["HostPort"]
            b.port=ports["5672/tcp"][0]["HostPort"]
            b.values["RABBITMQ_MANAGEMENT_URL"]=b.http
            wait(lambda:b.api("GET","/api/overview",allow=(0,503))[0]==200,"Compose broker unavailable")
            self.assertEqual(200,b.api("GET","/api/queues/%2Ffcg/fcg-catalog-payments")[0])
            for identifier in b.resources["container"]:
                if identifier!=b.container:
                    state=b.run(b.docker+["inspect","--format","{{.State.Status}} {{.State.ExitCode}}",identifier]).stdout.strip()
                    self.assertEqual("exited 0",state)
                    logs=b.run(b.docker+["logs",identifier]).stdout
                    self.assertIn("rabbitmq bootstrap ready",logs)
        finally:
            capture()
