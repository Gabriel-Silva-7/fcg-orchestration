# Configuration and topology contract

`config/identity.example.json` is a portable, non-secret deployment-contract bundle for the four services. `config/schema/identity.schema.json` uses JSON Schema 2020-12 and closes unknown service/config keys. These files are documentation of the configuration interface; no current host loads the bundle and the bundle itself provisions no Kubernetes Secret, queue or database. The separate Compose provisions PostgreSQL and RabbitMQ with a topology bootstrap gate; see [PostgreSQL](postgres.md) and [RabbitMQ](rabbitmq.md). Deployment must split the bundle per service and resolve `secret:` references before binding .NET configuration. Use double underscore for environment keys, e.g. `Jwt__Issuer`, `RabbitMq__VirtualHost`, `ConnectionStrings__Default`.

| Owner | Own PostgreSQL database | Secret reference for ConnectionStrings:Default |
|---|---|---|
| Users | fcg_users | secret:users-db |
| Catalog | fcg_catalog | secret:catalog-db |
| Payments | fcg_payments | secret:payments-db |
| Notifications | fcg_notifications | secret:notifications-db |

Each credential must grant access only to its owner's database, including its outbox/inbox and local tables. One PostgreSQL instance may host all four local databases. Payments owns durable processing/result state in its own database. No runtime database credentials are shared, imported or used for recovery across services. `secret:` values are references with no credential material, not usable connection strings.

All publishers use RabbitMq:Host, Port, VirtualHost, Username, Password. The development virtual host is `/fcg`, AMQP port 5672, host example `rabbitmq`. Resolve per-service username/password Secrets; no committed credentials. Topology is a future implementation contract:

| Publication (publisher) | Durable endpoint/queue | Consumer owner |
|---|---|---|
| UserCreatedEvent, UserStateChangedEvent, UserStateSnapshotEvent (Users) | fcg-catalog-identity | Catalog |
| Same identity publications (Users) | fcg-notifications-identity | Notifications |
| OrderPlacedEvent (Catalog) | fcg-payments-orders | Payments |
| PaymentProcessedEvent (Payments) | fcg-catalog-payments | Catalog |
| Same payment publication (Payments) | fcg-notifications-payments | Notifications |

Publish events to their type exchange and bind each owner's durable queue independently; do not route fanout through competing consumers of a shared queue. All event namespaces are FCG.Contracts. Queue keys are Queues:Identity/Payments for Catalog and Notifications and Queues:Orders for Payments. Explicit endpoint names must override any automatic naming formatter in the later transport adapter. Broker durability, retry/error isolation and delivery across restart require tests against the actual transport; JSON/unit tests do not establish them.

JWT keys: Jwt:Issuer=`fcg-users`, Audience=`fcg-api`, Algorithm=`RS256`, ExpirationMinutes=15, ClockSkewSeconds=0, PublicKeyPath=`/run/secrets/jwt-public.pem`. Users alone has PrivateKeyPath=`/run/secrets/users-jwt-private.pem`. Require PEM PKCS#8 private and SubjectPublicKeyInfo public RSA >=2048 bits in the future key loader; preserve keys on ordinary restarts and never put them in Git. Catalog gets public only; Payments/Notifications no JWT key. Signature, issuer/audience/lifetime/required claims must be validated before local state. Required claims are sub/role/jti, roles User/Administrator, policies UserOrAdmin/AdminOnly. See [identity contracts](identity-contracts.md) for state/retention limits. The service options validate names/values/ownership; no cryptographic or authentication HTTP pipeline has been connected yet.

PaymentSimulation:ApprovalLimit defaults to 500 (decimal), PaymentSimulation:Delay to `00:00:02` (invariant TimeSpan); bounds belong to Payments-owned options, not this documentation schema. Notifications:PendingContactRetryInterval defaults to `00:00:01`; a future dispatcher revisits PendingContact locally, including races and restart. This operational interval is not a guarantee of recovery time or a second consumer of the identity queue. Runtime configuration validators must validate these values on startup in their owning service.

The bundle schema constrains shape, expected names, vhost, endpoint names and no private key on Catalog. It cannot prove secret permissions, RSA format/size, database isolation, matching keys between services, runtime binding, queue provisioning, TLS or delivery guarantees. No real key fixtures or credentials are included.
