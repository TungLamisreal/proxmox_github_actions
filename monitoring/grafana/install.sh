#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
docker compose -p meowcloud-grafana config --quiet
docker compose -p meowcloud-grafana up -d --build
python3 check.py
