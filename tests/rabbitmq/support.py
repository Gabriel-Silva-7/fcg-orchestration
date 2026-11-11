"""Owned RabbitMQ fixture; secrets travel via environment, never CLI or evidence."""
import base64
import http.client
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "rabbitmq:4.3.6-management@sha256:54cb013d76cd1fd6c0a0708f34062ad61c452bb6f016b434c6c33f1a765d2b58"
LABEL = "io.fcg.rabbitmq.test-run"
OWNERS = ("USERS", "CATALOG", "PAYMENTS", "NOTIFICATIONS")
EVENTS = ("UserCreatedEvent", "UserStateChangedEvent", "UserStateSnapshotEvent", "OrderPlacedEvent", "PaymentProcessedEvent")
ENDPOINTS = ("fcg-catalog-identity", "fcg-notifications-identity", "fcg-payments-orders", "fcg-catalog-payments", "fcg-notifications-payments")
SUBSCRIPTIONS = {name: ENDPOINTS[:2] for name in EVENTS[:3]}
SUBSCRIPTIONS.update(OrderPlacedEvent=(ENDPOINTS[2],), PaymentProcessedEvent=ENDPOINTS[3:])
DOTNET = os.environ.get("DOTNET", "dotnet")
BOOTSTRAP = ROOT / "scripts/rabbitmq/bootstrap/bin/Release/net10.0/RabbitMq.Bootstrap.dll"
PROBE = ROOT / "tests/rabbitmq/Probes/bin/Release/net10.0/RabbitMq.Probes.dll"


def segment(value):
    return urllib.parse.quote(value, safe="")


def wait(predicate, message, seconds=75):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.25)
    raise AssertionError(message)


class Broker:
    def __init__(self, permit_transient=False):
        self.permit_transient = permit_transient
        self.name = "fcg-rabbit-test-" + uuid.uuid4().hex
        self.directory = tempfile.TemporaryDirectory(prefix=self.name)
        self.evidence = Path(os.environ.get("FCG_RABBITMQ_EVIDENCE_DIR", self.directory.name))
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.docker = ["docker", "--context", os.environ.get("DOCKER_CONTEXT", "orbstack")]
        self.env = os.environ.copy()
        # All contract values are explicit. No personal .env or exported credential can leak in.
        self.values = {"RABBITMQ_BOOTSTRAP_USER": "fcg_bootstrap", "RABBITMQ_BOOTSTRAP_PASSWORD": secrets.token_urlsafe(24),
                       "RABBITMQ_BOOTSTRAP_TIMEOUT_SECONDS": "30"}
        for owner in OWNERS:
            self.values[f"FCG_{owner}_RABBITMQ_USER"] = "fcg_" + owner.lower()
            self.values[f"FCG_{owner}_RABBITMQ_PASSWORD"] = secrets.token_urlsafe(24) + " quote' slash\\ dollar$ space"
        self.env.update(self.values)
        self.secrets = [v for k, v in self.values.items() if "PASSWORD" in k]
        self.resources = {"container": {}, "network": {}, "volume": {}}
        self.container = self.volume = self.network = None
        self.http = None

    def run(self, args, check=True, env=None, timeout=120):
        result = subprocess.run(args, capture_output=True, text=True, env=env or self.env, timeout=timeout, cwd=ROOT)
        output = result.stdout + result.stderr
        if any(secret in output for secret in self.secrets):
            raise AssertionError("command leaked a fixture credential (output suppressed)")
        if check and result.returncode:
            raise AssertionError(f"command exited {result.returncode}: {output[-2500:]}")
        return result

    def inspect(self, kind, identifier, check=True):
        # Explicit projections never bring Config.Env/passwords into command output.
        common = '\"Created\":{{json .Created}},'
        if kind == "container":
            projection = '{"Id":{{json .Id}},' + common + '"Config":{"Labels":{{json .Config.Labels}}},"NetworkSettings":{"Ports":{{json .NetworkSettings.Ports}}}}'
        elif kind == "network":
            projection = '{"Id":{{json .Id}},' + common + '"Labels":{{json .Labels}}}'
        else:
            projection = '{"Name":{{json .Name}},"CreatedAt":{{json .CreatedAt}},"Labels":{{json .Labels}}}'
        result = self.run(self.docker + [kind, "inspect", "--format", projection, identifier], check=check)
        return json.loads(result.stdout) if result.returncode == 0 else None

    def record(self, kind, identifier):
        obj = self.inspect(kind, identifier)
        labels = obj.get("Config", {}).get("Labels", {}) if kind == "container" else obj.get("Labels", {})
        assert labels.get(LABEL) == self.name, "resource does not belong to this fixture"
        identity = obj.get("Id", obj.get("Name"))
        self.resources[kind][identity] = {"id": identity, "created": obj.get("Created", obj.get("CreatedAt")), "labels": labels,
                                         "ports": obj.get("NetworkSettings", {}).get("Ports", {})}
        self.save()
        return identity

    def save(self):
        (self.evidence / (self.name + "-resources.json")).write_text(json.dumps(self.resources, indent=2))

    def start(self):
        self.network = self.run(self.docker + ["network", "create", "--label", LABEL + "=" + self.name, self.name]).stdout.strip()
        self.record("network", self.network)
        self.volume = self.run(self.docker + ["volume", "create", "--label", LABEL + "=" + self.name, self.name]).stdout.strip()
        self.record("volume", self.volume)
        self.create_container()

    def create_container(self):
        env = self.env.copy()
        env.update(RABBITMQ_DEFAULT_USER=self.values["RABBITMQ_BOOTSTRAP_USER"],
                   RABBITMQ_DEFAULT_PASS=self.values["RABBITMQ_BOOTSTRAP_PASSWORD"])
        args = self.docker + ["run", "-d", "--name", self.name, "--label", LABEL + "=" + self.name,
                              "--hostname", "rabbitmq", "--network", self.network, "--network-alias", "rabbitmq",
                              "-p", "127.0.0.1::5672", "-p", "127.0.0.1::15672", "--mount", "type=volume,source=" + self.volume + ",target=/var/lib/rabbitmq",
                              "--env", "RABBITMQ_DEFAULT_USER", "--env", "RABBITMQ_DEFAULT_PASS", "--env", "RABBITMQ_NODENAME=rabbit@rabbitmq",
                              "--env", "RABBITMQ_MNESIA_BASE=/var/lib/rabbitmq/mnesia", IMAGE]
        if self.permit_transient:
            config = Path(self.directory.name) / "legacy-test.conf"
            config.write_text("deprecated_features.permit.transient_nonexcl_queues = true\n")
            args[-1:-1] = ["--mount", "type=bind,source=" + str(config) + ",target=/etc/rabbitmq/conf.d/99-test-legacy.conf,readonly"]
        self.container = self.run(args, env=env).stdout.strip()
        self.record("container", self.container)
        ports = self.resources["container"][self.container]["ports"]
        for key in ("5672/tcp", "15672/tcp"):
            assert all(p["HostIp"] == "127.0.0.1" and int(p["HostPort"]) > 0 for p in ports[key])
        self.http = "http://127.0.0.1:" + ports["15672/tcp"][0]["HostPort"]
        self.port = ports["5672/tcp"][0]["HostPort"]
        self.values["RABBITMQ_MANAGEMENT_URL"] = self.http
        wait(lambda: self.api("GET", "/api/overview", allow=(0, 500, 502, 503, 504))[0] == 200, "authenticated broker not available")
        identity = self.run(self.docker + ["exec", self.container, "sh", "-c", "id rabbitmq; stat -c '%u:%g' /var/lib/rabbitmq; rabbitmq-diagnostics -q status"]).stdout
        assert "rabbit@rabbitmq" in identity and "/var/lib/rabbitmq/mnesia/rabbit@rabbitmq" in identity
        (self.evidence / (self.name + "-identity.txt")).write_text(identity)

    def api(self, method, path, body=None, allow=(), owner=None):
        raw = None if body is None else json.dumps(body).encode()
        request = urllib.request.Request(self.http + path, data=raw, method=method)
        auth = (self.values["RABBITMQ_BOOTSTRAP_USER"] + ":" + self.values["RABBITMQ_BOOTSTRAP_PASSWORD"] if owner is None
                else self.values[f"FCG_{owner}_RABBITMQ_USER"] + ":" + self.values[f"FCG_{owner}_RABBITMQ_PASSWORD"])
        request.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
        request.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=4) as response:
                data = response.read()
                return response.status, json.loads(data) if data else None
        except urllib.error.HTTPError as exc:
            code = exc.code
            exc.close()
            if code in allow:
                return code, None
            raise AssertionError(f"Management HTTP {method} failed with {exc.code} at {path}") from None
        except (urllib.error.URLError, TimeoutError, http.client.RemoteDisconnected, ConnectionResetError):
            if 0 in allow:
                return 0, None
            raise

    def bootstrap(self, overrides=None):
        env = self.env.copy()
        env.update(self.values)
        env.update(overrides or {})
        return self.run([DOTNET, str(BOOTSTRAP)], check=False, env=env, timeout=45)

    def probe(self, command, owner="PAYMENTS", **overrides):
        env = self.env.copy()
        env.update(AMQP_HOST="127.0.0.1", AMQP_PORT=self.port, AMQP_VHOST="/fcg",
                   AMQP_USERNAME=self.values[f"FCG_{owner}_RABBITMQ_USER"], AMQP_PASSWORD=self.values[f"FCG_{owner}_RABBITMQ_PASSWORD"])
        env.update({k: str(v) for k, v in overrides.items()})
        return self.run([DOTNET, str(PROBE), command], env=env, check=False, timeout=45)

    def require_bootstrap(self):
        result = self.bootstrap()
        assert result.returncode == 0 and "rabbitmq bootstrap ready" in result.stdout, result.stdout + result.stderr

    def remove(self, kind, identifier):
        saved = self.resources[kind][identifier]
        obj = self.inspect(kind, identifier, check=False)
        if obj is None:
            return
        labels = obj.get("Config", {}).get("Labels", {}) if kind == "container" else obj.get("Labels", {})
        assert labels.get(LABEL) == self.name and obj.get("Id", obj.get("Name")) == saved["id"]
        assert obj.get("Created", obj.get("CreatedAt")) == saved["created"], "identity changed; refusing cleanup"
        self.run(self.docker + [kind, "rm"] + (["-f"] if kind == "container" else []) + [identifier])

    def recreate(self):
        old = self.container
        self.run(self.docker + ["stop", "--time", "20", self.container])
        self.remove("container", self.container)
        self.create_container()
        assert self.container != old
        mounts = json.loads(self.run(self.docker + ["inspect", "--format", "{{json .Mounts}}", self.container]).stdout)
        assert any(m["Name"] == self.volume and m["Destination"] == "/var/lib/rabbitmq" for m in mounts)

    def close(self):
        try:
            if self.container:
                self.run(self.docker + ["logs", self.container], check=False)
        finally:
            self.cleanup()

    def capture(self):
        # Recover objects created before a timeout/exception could register their ID.
        # The per-run random label is the discovery boundary; metadata is recorded
        # before remove() checks the immutable identity and creation time again.
        for kind, listing in (("container", ["ps", "-aq"]), ("network", ["network", "ls", "-q"]), ("volume", ["volume", "ls", "-q"])):
            for identifier in self.run(self.docker + listing + ["--filter", "label=" + LABEL + "=" + self.name]).stdout.split():
                obj = self.inspect(kind, identifier)
                identity = obj.get("Id", obj.get("Name"))
                if identity not in self.resources[kind]: self.record(kind, identifier)

    def cleanup(self):
        try:
            self.capture()
            for kind in ("container", "network", "volume"):
                for identifier in self.resources[kind]:
                    self.remove(kind, identifier)
            (self.evidence / (self.name + "-cleanup.txt")).write_text("Only recorded IDs with unchanged ownership and creation metadata removed.\n")
        finally:
            self.directory.cleanup()
