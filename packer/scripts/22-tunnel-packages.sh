#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
# Stay consistent with the current Debian package already used by 01-setup-tunnels.yml.
# Pin exact version / SHA256 before considering the artifact production-reproducible.
apt-get update -qq
curl -fLSs --retry 3 -o /tmp/cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
dpkg-deb --field /tmp/cloudflared.deb Package | grep -qx 'cloudflared'
apt-get install -y -qq /tmp/cloudflared.deb
rm -f /tmp/cloudflared.deb
systemctl disable --now cloudflared.service 2>/dev/null || true
