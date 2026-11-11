#!/usr/bin/env bash
set -Eeuo pipefail

# The official entrypoint runs this only when PGDATA is empty.
# psql quotes each variable as a SQL literal; never splice raw passwords into SQL.
# Keep the error diagnostic, but never log password-bearing SQL on failure.
PGOPTIONS="-c log_min_error_statement=panic -c log_statement=none" \
psql -X --username "$POSTGRES_USER" --dbname postgres \
    --set ON_ERROR_STOP=1 \
    --set users_password="$FCG_USERS_DB_PASSWORD" \
    --set catalog_password="$FCG_CATALOG_DB_PASSWORD" \
    --set payments_password="$FCG_PAYMENTS_DB_PASSWORD" \
    --set notifications_password="$FCG_NOTIFICATIONS_DB_PASSWORD" <<'SQL'
CREATE ROLE fcg_users LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'users_password';
CREATE ROLE fcg_catalog LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'catalog_password';
CREATE ROLE fcg_payments LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'payments_password';
CREATE ROLE fcg_notifications LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD :'notifications_password';

-- Close template/maintenance access before creating the four application databases.
REVOKE CONNECT ON DATABASE postgres, template1, template0 FROM PUBLIC;
CREATE DATABASE fcg_users OWNER fcg_users;
CREATE DATABASE fcg_catalog OWNER fcg_catalog;
CREATE DATABASE fcg_payments OWNER fcg_payments;
CREATE DATABASE fcg_notifications OWNER fcg_notifications;
REVOKE CONNECT ON DATABASE fcg_users, fcg_catalog, fcg_payments, fcg_notifications FROM PUBLIC;
SQL
