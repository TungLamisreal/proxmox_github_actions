#!/usr/bin/env bash
set -Eeuo pipefail
role="${1:?usage: 40-image-role-gate.sh base|docker|lb|observer|tunnel|db}"
installed() { dpkg-query -W -f='${db:Status-Abbrev}' "$1" 2>/dev/null | grep -q '^ii'; }
required=(openssh-server prometheus-node-exporter)
forbidden=()
case "$role" in
 base) forbidden=(nginx keepalived prometheus prometheus-alertmanager docker-ce cloudflared) ;;
 docker) required+=(docker-ce python3-docker); forbidden=(nginx keepalived prometheus prometheus-alertmanager cloudflared) ;;
 lb) required+=(nginx keepalived); forbidden=(prometheus prometheus-alertmanager docker-ce cloudflared) ;;
 observer) required+=(prometheus prometheus-alertmanager); forbidden=(nginx keepalived docker-ce cloudflared) ;;
 tunnel) required+=(cloudflared); forbidden=(nginx keepalived prometheus prometheus-alertmanager docker-ce) ;;
 db) required+=(docker-ce qemu-guest-agent); forbidden=(nginx keepalived prometheus prometheus-alertmanager cloudflared) ;;
 *) echo "Unknown role: $role" >&2; exit 2 ;;
esac
# Role-incompatible Nginx variants / Docker OS packages also count as contamination.
if [[ "$role" != "lb" ]];then forbidden+=(nginx-common nginx-core nginx-full nginx-light);fi
if [[ "$role" != "docker" && "$role" != "db" ]];then forbidden+=(docker.io docker-ce-cli containerd.io);fi
for p in "${required[@]}";do installed "$p" || { echo "[FAIL] $role lacks package $p" >&2; exit 1; };done
for p in "${forbidden[@]}";do if installed "$p";then echo "[FAIL] $role contains forbidden package $p" >&2;exit 1;fi;done
# Ensure role-exclusive daemons aren't accidentally enabled or running.
for s in nginx keepalived prometheus prometheus-alertmanager cloudflared; do
 if [[ "$role" != "lb" || "$s" != "nginx" && "$s" != "keepalived" ]] &&
    [[ "$role" != "observer" || "$s" != "prometheus" && "$s" != "prometheus-alertmanager" ]] &&
    [[ "$role" != "tunnel" || "$s" != "cloudflared" ]];then
   if systemctl is-active --quiet "$s" 2>/dev/null || systemctl is-enabled --quiet "$s" 2>/dev/null;then
     echo "[FAIL] $role has unexpected enabled/running systemd unit $s" >&2;exit 1
   fi
 fi
done
if [[ "$role" == 'docker' ]];then
 docker image inspect meow-backend:latest nginx:alpine >/dev/null
elif [[ "$role" == 'db' ]];then
 docker image inspect postgres:15-alpine >/dev/null
fi
echo "[PASS] image role: $role"
