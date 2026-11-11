#!/usr/bin/env bash
set -Eeuo pipefail
cd "$(dirname "$0")/../.."
DOTNET=${DOTNET:-dotnet}
export DOTNET
for project in scripts/rabbitmq/bootstrap/RabbitMq.Bootstrap.csproj tests/rabbitmq/Probes/RabbitMq.Probes.csproj; do
  "$DOTNET" restore "$project" --locked-mode
  "$DOTNET" build "$project" -c Release --no-restore
done
python3 -m unittest discover -s tests/rabbitmq -p 'test_*.py' -v
