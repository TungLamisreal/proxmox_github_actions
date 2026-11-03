#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
# Recreating an identical dashboard interrupts the viewer during a demo.
revision=$(sha256sum Dockerfile datasource.yaml provider.yaml autoscale.json | sha256sum | cut -d' ' -f1)
export MEOW_GRAFANA_REVISION="$revision"
image="meowcloud-local/grafana:$revision"
docker compose -p meowcloud-grafana config --quiet
if ! docker image inspect "$image" >/dev/null 2>&1; then
  docker build --tag "$image" .
fi
docker compose -p meowcloud-grafana up -d
python3 check.py
