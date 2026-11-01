#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=600 update -qq
apt-get -o DPkg::Lock::Timeout=600 install -y -qq --no-install-recommends prometheus prometheus-alertmanager
systemctl disable --now prometheus.service prometheus-alertmanager.service || true
