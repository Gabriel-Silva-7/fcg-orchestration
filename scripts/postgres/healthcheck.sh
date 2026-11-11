#!/usr/bin/env bash
set -Eeuo pipefail

# The bootstrap server listens only on the Unix socket. Wait for the final TCP server.
pg_isready -q -h 127.0.0.1 -U fcg_bootstrap -d postgres || exit 1

# Local socket administration inspects persisted state. New env passwords are not rotation.
# No business tables or mutable completion marker are used as proof of readiness.
ready=$(psql -X --no-password --username fcg_bootstrap --dbname postgres \
    --set ON_ERROR_STOP=1 -Atq <<'SQL'
WITH expected(name) AS (
    VALUES ('fcg_users'), ('fcg_catalog'), ('fcg_payments'), ('fcg_notifications')
)
SELECT
    (SELECT count(*) = 4
     FROM expected e
     JOIN pg_roles r ON r.rolname = e.name
     JOIN pg_database d ON d.datname = e.name AND d.datdba = r.oid
     WHERE r.rolcanlogin AND d.datallowconn
       AND has_database_privilege(r.oid, d.oid, 'CREATE')
       AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls))
    AND NOT EXISTS (
        SELECT 1 FROM expected e JOIN pg_roles r ON r.rolname = e.name
        JOIN pg_auth_members m ON m.member = r.oid
    )
    AND NOT EXISTS (
        SELECT 1 FROM expected e JOIN pg_roles r ON r.rolname = e.name
        CROSS JOIN pg_database d
        WHERE has_database_privilege(r.oid, d.oid, 'CONNECT') <> (e.name = d.datname)
    )
    AND NOT EXISTS (
        SELECT 1 FROM pg_database d,
        LATERAL aclexplode(COALESCE(d.datacl, acldefault('d', d.datdba))) a
        WHERE a.grantee = 0 AND a.privilege_type = 'CONNECT'
    );
SQL
)
[[ "$ready" == "t" ]] || exit 1

# Schema ACLs are local to each database, and pg_database_owner is resolved there.
# Inspect effective privileges only; healthchecks must not create probe objects.
for database in fcg_users fcg_catalog fcg_payments fcg_notifications; do
    ready=$(psql -X --no-password --username fcg_bootstrap --dbname "$database" \
        --set ON_ERROR_STOP=1 --set app_role="$database" -Atq <<'SQL'
SELECT has_schema_privilege(:'app_role', n.oid, 'USAGE')
   AND has_schema_privilege(:'app_role', n.oid, 'CREATE')
FROM pg_namespace n WHERE n.nspname = 'public';
SQL
    )
    [[ "$ready" == "t" ]] || exit 1
done
