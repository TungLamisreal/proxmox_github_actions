#!/usr/bin/env bash
set -Eeuo pipefail
base_tarball="${1:?base tarball required}"
storage_pool="${2:?storage pool required}"
shared_path="${3:?shared fs path required}"
ctid=997
# Fail closed if an unrelated or previously interrupted CT exists.
if pct config "$ctid" >/dev/null 2>&1; then
  echo "[STOP] Temporary CTID $ctid already exists. Inspect before cleaning." >&2;exit 1
fi
cleanup() {
  if [[ "${created:-0}" == 1 ]];then
    pct stop "$ctid" >/dev/null 2>&1 || true
    pct destroy "$ctid" --purge >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
for role in lb observer tunnel;do
  created=0
  echo "=== Building ${role} role tarball ==="
  pct create "$ctid" "$base_tarball" -rootfs "${storage_pool}:8" \
    -memory 1024 -cores 2 -net0 name=eth0,bridge=vmbr0,ip=dhcp,firewall=0 \
    -unprivileged 1 -features nesting=1
  created=1
  pct start "$ctid"
  sleep 12
  pct exec "$ctid" -- mkdir -p /tmp/scripts
  case "$role" in
    lb) role_script=20-lb-packages.sh ;;
    observer) role_script=21-observer-packages.sh ;;
    tunnel) role_script=22-tunnel-packages.sh ;;
  esac
  pct push "$ctid" "/tmp/packer_scripts_roles/$role_script" "/tmp/scripts/$role_script"
  pct push "$ctid" /tmp/packer_scripts_roles/40-image-role-gate.sh /tmp/scripts/40-image-role-gate.sh
  pct push "$ctid" /tmp/packer_scripts_roles/99-cleanup-sanitize.sh /tmp/scripts/99-cleanup-sanitize.sh
  pct exec "$ctid" -- bash "/tmp/scripts/$role_script"
  pct exec "$ctid" -- bash /tmp/scripts/40-image-role-gate.sh "$role"
  pct exec "$ctid" -- bash /tmp/scripts/99-cleanup-sanitize.sh
  # Tar archives are a *source*, not a CephFS runtime rootfs.
  filename="meowcloud-lxc-${role}-debian12-v2.tar.zst"
  pct exec "$ctid" -- tar -I zstd -cpf "/tmp/$filename" \
    --exclude="/tmp/$filename" --exclude=/lost+found \
    --exclude='/dev/*' --exclude='/proc/*' --exclude='/sys/*' \
    --exclude='/run/*' --exclude='/mnt/*' --exclude='/media/*' /
  pct pull "$ctid" "/tmp/$filename" "${shared_path}/${filename}.staging"
  test -s "${shared_path}/${filename}.staging"
  mv -f "${shared_path}/${filename}.staging" "${shared_path}/${filename}"
  pct stop "$ctid"
  pct destroy "$ctid" --purge
  created=0
  echo "[PASS] $filename"
done
