#!/usr/bin/env python3
"""Real Compose tests. Docker context defaults to orbstack; no third-party modules."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
ROLES = ("fcg_users", "fcg_catalog", "fcg_payments", "fcg_notifications")
KEYS = ("POSTGRES_PASSWORD",) + tuple(f"FCG_{name[4:].upper()}_DB_PASSWORD" for name in ROLES)
PASSWORDS = dict(zip(KEYS, ("DEMO bootstrap only", "DEMO quote' slash\\ dollar$ space",
                            "DEMO catalog password", "DEMO payments password", "DEMO notifications password")))
LABEL = "io.fcg.postgres.test-run"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


class Environment:
    def __init__(self, context, evidence):
        self.name = "fcg-pg-test-" + uuid.uuid4().hex
        self.docker = ["docker", "--context", context]
        self.tmp = tempfile.TemporaryDirectory(prefix="fcg-postgres-test-")
        self.directory = Path(self.tmp.name)
        self.evidence = Path(evidence) if evidence else self.directory
        self.evidence.mkdir(parents=True, exist_ok=True)
        self.env_file = self.directory / "test.env"
        self.override = self.directory / "override.json"
        self.override.write_text(json.dumps({
            "services": {"postgres": {"labels": {LABEL: self.name}}},
            "volumes": {"postgres_data": {"labels": {LABEL: self.name}}},
            "networks": {"default": {"labels": {LABEL: self.name}}},
        }))
        self.base = self.docker + ["compose", "--project-name", self.name,
                                   "--env-file", str(self.env_file), "-f", str(ROOT / "docker-compose.yml"),
                                   "-f", str(self.override)]
        self.resources = {"container": {}, "volume": {}, "network": {}}
        self.container = None
        self.env = os.environ.copy()
        for key in KEYS + ("POSTGRES_PORT", "COMPOSE_FILE", "COMPOSE_PROJECT_NAME", "COMPOSE_PROFILES"):
            self.env.pop(key, None)
        self.passwords = PASSWORDS.copy()
        self.write_env()

    def write_env(self, missing=None, empty=None):
        values = dict(self.passwords, POSTGRES_PORT="0")
        lines = []
        for key, value in values.items():
            if key == missing:
                continue
            if key == empty:
                value = ""
            lines.append(key + "='" + value.replace("'", "\\'") + "'")
        self.env_file.write_text("\n".join(lines) + "\n")
        self.env_file.chmod(0o600)

    def command(self, args, *, data=None, password=None, check=True):
        env = self.env.copy()
        if password is not None:
            env["PGPASSWORD"] = password
        result = subprocess.run(args, input=data, text=True, capture_output=True, env=env, timeout=120)
        if check and result.returncode:
            # Never print argv/environment: credentials may be carried by the client.
            raise AssertionError(f"command failed ({result.returncode}): {self.redact(result.stderr)}")
        return result

    def redact(self, value):
        for password in (*PASSWORDS.values(), *self.passwords.values()):
            value = value.replace(password, "[REDACTED]")
        return value

    def compose(self, *args, check=True):
        return self.command(self.base + list(args), check=check)

    def capture(self):
        containers = []
        for kind, listing in (("container", ["ps", "-aq"]),
                              ("volume", ["volume", "ls", "-q"]),
                              ("network", ["network", "ls", "-q"])):
            found = self.command(self.docker + listing + ["--filter", f"label={LABEL}={self.name}"]).stdout.split()
            for identifier in found:
                obj = json.loads(self.command(self.docker + [kind, "inspect", identifier]).stdout)[0]
                labels = obj.get("Config", {}).get("Labels", {}) if kind == "container" else obj.get("Labels", {})
                require(labels.get(LABEL) == self.name and labels.get("com.docker.compose.project") == self.name,
                        "resource ownership does not match this test")
                resource_id = obj.get("Id", obj.get("Name"))
                # Persist only identity metadata, never Config.Env or container secrets.
                self.resources[kind][resource_id] = {"id": resource_id, "labels": labels,
                                                    "created": obj.get("Created", obj.get("CreatedAt")),
                                                    "ports": obj.get("NetworkSettings", {}).get("Ports", {})}
                if kind == "container":
                    containers.append(resource_id)
        (self.evidence / (self.name + "-resources.json")).write_text(json.dumps(self.resources, indent=2))
        # Cleanup must still work when .env is missing/invalid. Do not parse Compose here.
        self.container = containers[0] if containers else None

    def start(self, recreate=False):
        try:
            self.compose("up", "-d", *(["--force-recreate"] if recreate else []), "postgres")
        finally:
            self.capture()

    def sql(self, sql, role="fcg_bootstrap", database="postgres", password=None, check=True):
        require(self.container, "test container not captured")
        args = self.docker + ["exec", "-i", "--env", "PGPASSWORD", self.container,
                              "psql", "-X", "--no-password", "-h", "postgres", "-U", role, "-d", database,
                              "--set", "ON_ERROR_STOP=1", "-Atq"]
        if password is None:
            password = PASSWORDS["POSTGRES_PASSWORD"]
        return self.command(args, data=sql, password=password, check=check)

    def wait(self, predicate, message):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.4)
        raise AssertionError(message)

    def accessible(self):
        self.wait(lambda: self.sql("SELECT 1", check=False).stdout.strip() == "1", "PostgreSQL TCP is not accessible")

    def health(self):
        result = self.command(self.docker + ["exec", self.container, "/opt/fcg/postgres/healthcheck.sh"], check=False)
        return result.returncode == 0

    def state(self):
        return self.command(self.docker + ["inspect", "--format", "{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                                           self.container]).stdout.strip()

    def login(self, role, database=None, password=None, sql="SELECT 1", check=True):
        key = f"FCG_{role[4:].upper()}_DB_PASSWORD"
        return self.sql(sql, role, database or role, PASSWORDS[key] if password is None else password, check)

    def cleanup(self):
        self.capture()
        # Delete only recorded IDs whose two ownership labels still match. No compose down/prune.
        for kind in ("container", "network", "volume"):
            for identifier, saved in self.resources[kind].items():
                result = self.command(self.docker + [kind, "inspect", identifier], check=False)
                if result.returncode:
                    continue
                obj = json.loads(result.stdout)[0]
                labels = obj.get("Config", {}).get("Labels", {}) if kind == "container" else obj.get("Labels", {})
                current_id = obj.get("Id", obj.get("Name"))
                created = obj.get("Created", obj.get("CreatedAt"))
                require(current_id == saved["id"] and created == saved["created"] and
                        labels.get(LABEL) == self.name and labels.get("com.docker.compose.project") == self.name,
                        "refusing cleanup: resource identity/ownership changed")
                self.command(self.docker + [kind, "rm"] + (["-f"] if kind == "container" else []) + [identifier])
        (self.evidence / (self.name + "-cleanup.txt")).write_text("Removed only captured resources with matching identity and ownership labels.\n")
        self.tmp.cleanup()


def provision(e):
    e.accessible()
    print("PASS precondition: PostgreSQL TCP SELECT 1 = 1", flush=True)
    bindings = e.resources["container"][e.container]["ports"].get("5432/tcp", [])
    require(bindings and all(port["HostIp"] == "127.0.0.1" and int(port["HostPort"]) > 0 for port in bindings),
            "test port must be allocated on loopback only")
    roles = e.sql("SELECT rolname FROM pg_roles WHERE rolname IN ('fcg_users','fcg_catalog','fcg_payments','fcg_notifications') ORDER BY rolname").stdout.split()
    require(set(roles) == set(ROLES), "four application roles must exist; PostgreSQL is accessible but provisioning is absent/incomplete")
    e.wait(e.health, "provisioning health did not become ready")
    e.wait(lambda: e.state() == "healthy", "Compose health did not become healthy")
    for role in ROLES:
        flags = e.sql(f"SELECT rolcanlogin AND NOT (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls) FROM pg_roles WHERE rolname='{role}'").stdout.strip()
        require(flags == "t", f"unsafe flags on {role}")
        owner = e.sql(f"SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname='{role}'").stdout.strip()
        require(owner == role, f"wrong owner for {role}")
        data = e.login(role, sql="CREATE TABLE probe(id integer PRIMARY KEY, value text); INSERT INTO probe VALUES (1, 'persistido'); SELECT value FROM probe WHERE id=1;").stdout.strip()
        require(data == "persistido", f"owner DDL/DML failed for {role}")
    print("PASS provision: four restricted owners, authenticated DDL/DML", flush=True)


def isolation(e):
    require(e.sql("SELECT count(*) FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname LIKE 'fcg_%' AND r.rolname <> 'fcg_bootstrap'").stdout.strip() == "0", "application role has membership")
    for role in ROLES:
        for database in (*ROLES, "postgres", "template1", "template0"):
            allowed = e.sql(f"SELECT has_database_privilege('{role}', '{database}', 'CONNECT')").stdout.strip()
            require(allowed == ("t" if database == role else "f"), f"CONNECT ACL incorrect for {role}/{database}")
            if database != role:
                result = e.login(role, database, check=False)
                require(result.returncode != 0 and ("permission denied" in result.stderr or "not currently accepting" in result.stderr), f"cross-database access was not denied for {role}/{database}")
        for statement in ("CREATE DATABASE forbidden", "CREATE ROLE forbidden"):
            result = e.login(role, sql=statement, check=False)
            require(result.returncode != 0 and "permission denied" in result.stderr, f"unsafe administrative access for {role}")
    public = e.sql("SELECT count(*) FROM pg_database d, LATERAL aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a WHERE a.grantee=0 AND a.privilege_type='CONNECT'").stdout.strip()
    require(public == "0", "PUBLIC can CONNECT")
    print("PASS isolation: all cross/admin/template connections and DDL privileges denied", flush=True)


def passwords(e):
    require(e.login("fcg_users").stdout.strip() == "1", "literal password failed")
    wrong = e.login("fcg_users", password="DEMO wrong password", check=False)
    require(wrong.returncode != 0 and "password authentication failed" in wrong.stderr, "wrong password accepted")
    print("PASS passwords: quote/backslash/dollar/space literal authenticates; wrong password rejected", flush=True)


def persistence(e):
    volumes = set(e.resources["volume"])
    e.start(recreate=True)
    e.accessible()
    e.wait(e.health, "readiness failed after container recreation")
    e.passwords["FCG_USERS_DB_PASSWORD"] = "DEMO changed password"
    e.write_env()
    e.start(recreate=True)
    e.accessible()
    e.wait(e.health, "readiness failed after env password change")
    require(set(e.resources["volume"]) == volumes, "volume changed")
    for role in ROLES:
        require(e.login(role, sql="SELECT value FROM probe WHERE id=1").stdout.strip() == "persistido", f"data lost in {role}")
    new = e.login("fcg_users", password=e.passwords["FCG_USERS_DB_PASSWORD"], check=False)
    require(new.returncode != 0 and "password authentication failed" in new.stderr, "env unexpectedly rotated password")
    print("PASS persistence: same volume/data; original credential survives env change", flush=True)


def empty(e):
    for key in KEYS:
        for mode in ("missing", "empty"):
            e.write_env(**{mode: key})
            result = e.compose("config", "--quiet", check=False)
            require(result.returncode != 0 and key in result.stderr, f"{key} {mode} did not fail fast")
            # Resource capture/cleanup must not rely on a currently valid Compose env.
            try:
                e.capture()
            finally:
                e.write_env()
        # Exercise the wrapper itself against the already initialized volume.
        result = e.command(e.docker + ["exec", "--env", key + "=", e.container,
                                      "/opt/fcg/postgres/entrypoint.sh", "postgres"], check=False)
        require(result.returncode != 0 and key in result.stderr, f"runtime empty {key} did not fail fast")
    e.write_env()
    print("PASS empty: five credentials missing/empty fail Compose and runtime validation", flush=True)


def partial(e):
    e.sql("DROP DATABASE fcg_notifications; DROP ROLE fcg_notifications;")
    require(not e.health(), "partial provisioning incorrectly ready")
    e.start(recreate=True)
    e.accessible()
    require(e.command(e.docker + ["exec", e.container, "pg_isready", "-h", "127.0.0.1"]).returncode == 0, "server is not accepting connections")
    e.wait(lambda: e.state() == "unhealthy", "Compose did not mark partial provisioning unhealthy")
    require(not e.health(), "recreation hid partial provisioning")
    require(e.sql("SELECT count(*) FROM pg_database WHERE datname='fcg_notifications'").stdout.strip() == "0", "init reran unexpectedly")
    require(e.login("fcg_users", sql="SELECT value FROM probe WHERE id=1").stdout.strip() == "persistido", "partial init recovery reset existing data")
    print("PASS partial: pg_isready accepts; readiness unhealthy; data preserved; no automatic repair/reset", flush=True)


def init_errors(context, evidence):
    e = Environment(context, evidence)
    fixture = e.directory / "00-conflict.sql"
    fixture.write_text("CREATE ROLE fcg_users LOGIN;\nCREATE DATABASE fcg_users OWNER fcg_users;\n"
                       "\\connect fcg_users\nCREATE TABLE preserved(value text);\n"
                       "INSERT INTO preserved VALUES ('partial-sentinel');\n")
    override = json.loads(e.override.read_text())
    override["services"]["postgres"]["volumes"] = [str(fixture) + ":/docker-entrypoint-initdb.d/00-conflict.sql:ro"]
    e.override.write_text(json.dumps(override))
    try:
        start = e.compose("up", "-d", "postgres", check=False)
        e.capture()
        e.wait(lambda: e.command(e.docker + ["inspect", "--format", "{{.State.Status}}", e.container]).stdout.strip() == "exited",
               "conflicting role did not stop initial provisioning")
        logs = e.command(e.docker + ["logs", e.container])
        output = start.stdout + start.stderr + logs.stdout + logs.stderr
        require("already exists" in output, "expected duplicate-role diagnostic absent")
        # PostgreSQL can render SQL-escaped values in error statements.
        variants = []
        for password in PASSWORDS.values():
            variants.extend((password, password.replace("'", "''"),
                             password.replace("\\", "\\\\").replace("'", "''")))
        leaked = any(value in output for value in variants)
        redacted = output
        for value in sorted(variants, key=len, reverse=True):
            redacted = redacted.replace(value, "[REDACTED]")
        (e.evidence / (e.name + "-init-error.log")).write_text(redacted)
        require(not leaked, "credential leaked in duplicate-role init error logs (captured log redacted)")
        e.start(recreate=True)
        e.accessible()
        e.wait(lambda: e.state() == "unhealthy", "partial failed init reported healthy")
        require(e.sql("SELECT value FROM preserved", database="fcg_users").stdout.strip() == "partial-sentinel",
                "failed init was reset automatically")
        require(e.sql("SELECT count(*) FROM pg_roles WHERE rolname='fcg_notifications'").stdout.strip() == "0",
                "failed init was rerun automatically")
        print("PASS init-errors: duplicate role diagnostic retains no passwords; partial volume remains unhealthy and preserved", flush=True)
    finally:
        e.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", default="orbstack")
    parser.add_argument("--case", choices=("all", "provision", "init-errors"), default="all")
    parser.add_argument("--evidence-dir")
    args = parser.parse_args()
    if args.case == "init-errors":
        init_errors(args.context, args.evidence_dir)
        return
    e = Environment(args.context, args.evidence_dir)
    try:
        e.start()
        provision(e)
        if args.case == "all":
            for case in (isolation, passwords, persistence, empty, partial):
                case(e)
        log_result = e.command(e.docker + ["logs", e.container])
        logs = log_result.stdout + log_result.stderr
        require(all(value not in logs for value in PASSWORDS.values()), "credential leaked to normal container logs")
        print("PASS normal logs contain no test credential", flush=True)
    finally:
        e.cleanup()
    print("PASS cleanup: only recorded resources owned by this run were removed", flush=True)
    if args.case == "all":
        init_errors(args.context, args.evidence_dir)


if __name__ == "__main__":
    main()
