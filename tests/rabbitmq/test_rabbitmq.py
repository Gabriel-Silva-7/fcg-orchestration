"""Behavior tests against the fixed real broker; unittest requires Docker and .NET10."""
import json
import time
import unittest
from support import Broker, ENDPOINTS, EVENTS, SUBSCRIPTIONS, segment

VHOST = "/api/vhosts/%2Ffcg"
BASE = "/api/"


class RabbitMqTests(unittest.TestCase):
    def setUp(self):
        self.b = Broker(permit_transient=self._testMethodName == "test_queue_conflict_preserves_message_and_does_not_repair_silently")
        self.addCleanup(self.b.close)
        self.b.start()

    def assert_topology(self):
        status, vhost = self.b.api("GET", VHOST, allow=(404,))
        self.assertEqual(200, status, "broker authenticated but /fcg bootstrap is absent")
        self.assertEqual("classic", vhost["default_queue_type"])
        _, exchanges = self.b.api("GET", "/api/exchanges/%2Ffcg")
        managed = {e["name"]: e for e in exchanges if e["name"] in ENDPOINTS or e["name"].startswith("FCG.Contracts:")}
        self.assertEqual(set(ENDPOINTS) | {"FCG.Contracts:" + e for e in (*EVENTS, "IdentitySnapshot")}, set(managed))
        for exchange in managed.values():
            self.assertEqual(("fanout", True, False, False, {}),
                             (exchange["type"], exchange["durable"], exchange["auto_delete"], exchange["internal"], exchange["arguments"]))
        _, queues = self.b.api("GET", "/api/queues/%2Ffcg")
        self.assertEqual(set(ENDPOINTS), {q["name"] for q in queues})
        for queue in queues:
            self.assertEqual(("classic", True, False, False, {}),
                             (queue["type"], queue["durable"], queue["auto_delete"], queue["exclusive"], {k:v for k,v in queue["arguments"].items() if not (k == "x-queue-type" and v == "classic")}))
        _, bindings = self.b.api("GET", "/api/bindings/%2Ffcg")
        actual = {(b["source"], b["destination"], b["destination_type"], b["routing_key"], json.dumps(b["arguments"], sort_keys=True)) for b in bindings if b["source"]}
        expected = {(q, q, "queue", "", "{}") for q in ENDPOINTS}
        expected |= {("FCG.Contracts:" + e, q, "exchange", "", "{}") for e, endpoints in SUBSCRIPTIONS.items() for q in endpoints}
        expected |= {("FCG.Contracts:" + e, "FCG.Contracts:IdentitySnapshot", "exchange", "", "{}") for e in EVENTS[:3]}
        self.assertEqual(expected, actual)

    def test_empty_broker_bootstrap_before_any_consumer(self):
        result = self.b.bootstrap()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assert_topology()
        self.assertIn("rabbitmq bootstrap ready", result.stdout)

    def test_timeout_auth_and_invalid_configuration_never_ready(self):
        started = time.monotonic()
        result = self.b.bootstrap({"RABBITMQ_MANAGEMENT_URL": "http://127.0.0.1:1", "RABBITMQ_BOOTSTRAP_TIMEOUT_SECONDS": "1"})
        self.assertNotEqual(0, result.returncode)
        self.assertIn("bootstrap_timeout", result.stderr)
        self.assertLess(time.monotonic() - started, 6)
        for overrides, code in (({"RABBITMQ_BOOTSTRAP_PASSWORD": "incorrect"}, "authentication_failed"),
                                ({"FCG_USERS_RABBITMQ_USER": "fcg_bootstrap"}, "configuration_invalid"),
                                ({"FCG_USERS_RABBITMQ_PASSWORD": ""}, "configuration_invalid")):
            with self.subTest(code=code, keys=tuple(overrides)):
                result = self.b.bootstrap(overrides)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(code, result.stderr)
                self.assertNotIn("rabbitmq bootstrap ready", result.stdout)


class RabbitMqDeliveryTests(unittest.TestCase):
    setUp = RabbitMqTests.setUp
    assert_topology = RabbitMqTests.assert_topology
    # Inherit the fixture and topology assertion, not the two bootstrap test methods.
    def checked_probe(self, command, owner="PAYMENTS", **overrides):
        result = self.b.probe(command, owner, **overrides)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def publish(self, event="PaymentProcessedEvent", **overrides):
        payload = {"event": event, "orderId": "11111111-1111-1111-1111-111111111111", "price": 44.93}
        body = json.dumps(payload, separators=(",", ":"))
        result = self.checked_probe("publish-confirm", EXCHANGE="FCG.Contracts:" + event, MESSAGE_BODY=body, **overrides)
        self.assertEqual("AMQP 0-9-1", result.get("protocol"))
        self.assertTrue(result["confirmed"])
        return body

    def get(self, queue):
        return self.checked_probe("get-ack", QUEUE=queue)

    def counts(self):
        return self.checked_probe("counts")["queues"]

    def test_publish_without_consumers_fanout_exact_bytes_and_properties(self):
        self.b.require_bootstrap()
        _, consumers = self.b.api("GET", "/api/consumers/%2Ffcg")
        self.assertEqual([], consumers)
        expected = {}
        for event in EVENTS:
            body = self.publish(event)
            for queue in SUBSCRIPTIONS[event]: expected.setdefault(queue, []).append(body)
        self.assertEqual({q:len(bodies) for q,bodies in expected.items()}, self.counts())
        for queue,bodies in expected.items():
            for body in bodies:
                received = self.get(queue)
                self.assertEqual(body, received["body"])
                self.assertEqual("44444444-4444-4444-4444-444444444444", received["messageId"])
                self.assertEqual("11111111-1111-1111-1111-111111111111", received["correlationId"])
                self.assertTrue(received["persistent"])
            self.assertEqual(0, self.counts()[queue])

    def test_persistent_message_survives_idempotence_and_container_recreation(self):
        self.b.require_bootstrap(); body = self.publish()
        before = self.counts(); self.assertEqual(1, before[ENDPOINTS[3]]); self.assertEqual(1, before[ENDPOINTS[4]])
        self.b.require_bootstrap(); self.b.require_bootstrap(); self.assertEqual(before, self.counts())
        old, volume = self.b.container, self.b.volume
        self.b.recreate(); self.assertNotEqual(old, self.b.container); self.assertEqual(volume, self.b.volume)
        self.b.require_bootstrap(); self.assertEqual(before, self.counts())
        self.assertEqual(body, self.get(ENDPOINTS[3])["body"])
        self.assertEqual(1, self.counts()[ENDPOINTS[4]])
        self.assertEqual(body, self.get(ENDPOINTS[4])["body"])

    def test_actual_masstransit_with_approved_contracts_has_compatible_topology(self):
        self.b.require_bootstrap()
        result = self.checked_probe("masstransit")
        self.assertEqual(9, result.get("delivered"))
        self.assertEqual(["urn:message:FCG.Contracts:"+name for name in EVENTS], result["eventUrns"])
        self.assertTrue(result["payloadAndCorrelationVerified"])
        # Error/skipped/bus resources created by MT are outside managed source topology.
        self.assert_topology()
        self.b.require_bootstrap()

    def test_each_application_is_non_admin_and_denied_other_vhosts(self):
        self.b.require_bootstrap(); self.b.api("PUT", "/api/vhosts/sentinel", {})
        for owner in ("USERS","CATALOG","PAYMENTS","NOTIFICATIONS"):
            self.assertTrue(self.checked_probe("connect",owner)["connected"])
            for vhost in ("/","sentinel"):
                self.assertNotEqual(0,self.b.probe("connect",owner,AMQP_VHOST=vhost).returncode,(owner,vhost))
            status,_ = self.b.api("GET","/api/users",allow=(401,403),owner=owner)
            self.assertIn(status,(401,403))
            _,user=self.b.api("GET","/api/users/"+self.b.values[f"FCG_{owner}_RABBITMQ_USER"])
            self.assertEqual([],user["tags"])
        self.assertEqual(200,self.b.api("GET","/api/vhosts/sentinel")[0])

    def test_queue_conflict_preserves_message_and_does_not_repair_silently(self):
        self.b.require_bootstrap(); queue=ENDPOINTS[3]; path="/api/queues/%2Ffcg/"+queue
        self.b.api("DELETE",path)
        self.b.api("PUT",path,{"durable":False,"auto_delete":False,"arguments":{}})
        body="queue conflict marker"
        result=self.checked_probe("publish-confirm",EXCHANGE="",ROUTING_KEY=queue,MESSAGE_BODY=body)
        self.assertTrue(result["confirmed"])
        failed=self.b.bootstrap();self.assertNotEqual(0,failed.returncode);self.assertIn("topology_conflict",failed.stderr)
        self.assertFalse(self.b.api("GET",path)[1]["durable"])
        self.assertEqual(body,self.get(queue)["body"])

    def test_argument_exchange_and_binding_conflicts_are_fail_closed(self):
        self.b.require_bootstrap()
        # Each independent drift is restored only by the test fixture for the next case.
        queue=ENDPOINTS[3]; path="/api/queues/%2Ffcg/"+queue
        self.b.api("DELETE",path);self.b.api("PUT",path,{"durable":True,"auto_delete":False,"arguments":{"x-max-length":1}})
        failed=self.b.bootstrap();self.assertIn("topology_conflict",failed.stderr);self.assertNotEqual(0,failed.returncode)
        self.assertEqual(1, self.b.api("GET",path)[1]["arguments"]["x-max-length"])
        self.b.api("DELETE",path);self.b.api("PUT",path,{"durable":True,"auto_delete":False,"arguments":{}});self.b.require_bootstrap()
        source="FCG.Contracts:OrderPlacedEvent";path="/api/exchanges/%2Ffcg/"+segment(source)
        self.b.api("DELETE",path);self.b.api("PUT",path,{"type":"direct","durable":True,"auto_delete":False,"internal":False,"arguments":{}})
        failed=self.b.bootstrap();self.assertIn("topology_conflict",failed.stderr);self.assertNotEqual(0,failed.returncode)
        self.assertEqual("direct",self.b.api("GET",path)[1]["type"])
        self.b.api("DELETE",path);self.b.require_bootstrap()
        self.b.api("POST","/api/bindings/%2Ffcg/e/"+segment(source)+"/q/"+ENDPOINTS[2],{"routing_key":"unexpected","arguments":{}})
        failed=self.b.bootstrap();self.assertIn("topology_conflict",failed.stderr);self.assertNotEqual(0,failed.returncode)
        edges=self.b.api("GET",path+"/bindings/source")[1]
        self.assertTrue(any(e["routing_key"]=="unexpected" for e in edges))

    def test_credential_tags_and_permission_drift_are_preserved_not_rotated(self):
        self.b.require_bootstrap()
        failed=self.b.bootstrap({"FCG_USERS_RABBITMQ_PASSWORD":"new credential should not rotate"})
        self.assertIn("topology_conflict",failed.stderr);self.assertTrue(self.checked_probe("connect","USERS")["connected"])
        user=self.b.values["FCG_USERS_RABBITMQ_USER"];path="/api/users/"+user
        self.b.api("PUT",path,{"password":self.b.values["FCG_USERS_RABBITMQ_PASSWORD"],"tags":["administrator"]})
        failed=self.b.bootstrap();self.assertIn("topology_conflict",failed.stderr)
        self.assertEqual(["administrator"],self.b.api("GET",path)[1]["tags"])
        self.b.api("PUT",path,{"password":self.b.values["FCG_USERS_RABBITMQ_PASSWORD"],"tags":[]})
        self.b.api("PUT","/api/vhosts/sentinel",{})
        self.b.api("PUT","/api/permissions/sentinel/"+user,{"configure":".*","write":".*","read":".*"})
        failed=self.b.bootstrap();self.assertIn("topology_conflict",failed.stderr)
        self.assertEqual(200,self.b.api("GET","/api/permissions/sentinel/"+user)[0])


    def test_unroutable_mandatory_publish_does_not_claim_confirmation(self):
        self.b.require_bootstrap()
        result=self.b.probe("publish-confirm",EXCHANGE="",ROUTING_KEY="queue-that-does-not-exist",MESSAGE_BODY="marker")
        self.assertNotEqual(0,result.returncode)
        self.assertNotIn('"confirmed":true',result.stdout)
