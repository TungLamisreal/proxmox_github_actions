#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends prometheus prometheus-alertmanager
systemctl disable --now prometheus.service prometheus-alertmanager.service || true
