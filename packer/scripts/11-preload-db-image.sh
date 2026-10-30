#!/usr/bin/env bash
set -Eeuo pipefail
docker pull postgres:15-alpine
docker image inspect postgres:15-alpine >/dev/null
