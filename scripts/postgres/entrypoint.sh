#!/usr/bin/env bash
set -Eeuo pipefail

# Validate on every start, including an already initialized volume.
for key in POSTGRES_PASSWORD FCG_USERS_DB_PASSWORD FCG_CATALOG_DB_PASSWORD \
           FCG_PAYMENTS_DB_PASSWORD FCG_NOTIFICATIONS_DB_PASSWORD; do
    if [[ -z "${!key:-}" ]]; then
        printf '%s is required and must not be empty\n' "$key" >&2
        exit 1
    fi
done

exec /usr/local/bin/docker-entrypoint.sh "$@"
