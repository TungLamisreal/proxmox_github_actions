#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
command -v curl >/dev/null
command -v tar >/dev/null
command -v sha256sum >/dev/null
command -v systemctl >/dev/null
command -v python3 >/dev/null
app=/opt/meowcloud-grafana-13.3.0
config=/etc/meowcloud-grafana
unit=/etc/systemd/system/meowcloud-grafana.service
test "$(uname -m)" = x86_64
if [[ ! -x "$app/bin/grafana" ]]; then
  test "$(df -Pm /opt | awk 'END {print $4}')" -ge 3072
  test "$(awk '/MemAvailable/ {print $2}' /proc/meminfo)" -ge 786432
  if [[ -r /sys/fs/cgroup/memory.max ]]; then
    limit=$(cat /sys/fs/cgroup/memory.max)
    if [[ "$limit" != max ]]; then
      test "$((limit - $(cat /sys/fs/cgroup/memory.current)))" -ge 805306368
    fi
  fi
  stage=$(mktemp -d /opt/.grafana-XXXXXXXX)
  trap 'rm -rf -- "$stage"' EXIT
  curl -fsSL --retry 3 https://dl.grafana.com/grafana/release/13.3.0/grafana_13.3.0_40412481033_linux_amd64.tar.gz -o "$stage/archive"
  echo "cec8f98cc59bc1a1945833b652371a073be678c5c159a9b1695ad63c04fae5a7  $stage/archive" | sha256sum -c -
  mkdir "$stage/app"
  nice -n 15 tar xzf "$stage/archive" --no-same-owner --strip-components=1 -C "$stage/app"
  test -x "$stage/app/bin/grafana"
  chmod 755 "$stage/app"
  mv -T "$stage/app" "$app"
fi
getent passwd meow-grafana >/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin meow-grafana
install -d -m 0755 "$config/dashboards" "$config/datasources" "$config/boards"
changed=false
sync_file() {
  if ! cmp -s "$1" "$2"; then
    install -m 0644 "$1" "$2"
    changed=true
  fi
}
sync_file datasource.yaml "$config/datasources/meowcloud.yaml"
sync_file provider.yaml "$config/dashboards/meowcloud.yaml"
sync_file autoscale.json "$config/boards/autoscale.json"
sync_file grafana.service "$unit"
systemctl daemon-reload
systemctl enable meowcloud-grafana
if [[ "$changed" == true ]]; then
  systemctl restart meowcloud-grafana
else
  systemctl start meowcloud-grafana
fi
python3 check.py
printf "changed=%s\n" "$changed"
