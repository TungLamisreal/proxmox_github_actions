#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "=== [3/4] Cài đặt cấu phần đặc thù cho máy ảo KVM ==="
apt-get -o DPkg::Lock::Timeout=600 update -qq
# Seed 9000 might predate the clean-base change; clean the cloned KVM image,
# never touch the source 9000 or live DB data disk.
stale_packages=()
for pkg in nginx nginx-common nginx-core nginx-full nginx-light keepalived prometheus prometheus-alertmanager; do
    if dpkg-query -W -f='${db:Status-Abbrev}' "$pkg" 2>/dev/null | grep -q '^ii'; then
        stale_packages+=("$pkg")
    fi
done
if (( ${#stale_packages[@]} > 0 )); then
    apt-get -o DPkg::Lock::Timeout=600 purge -y -qq "${stale_packages[@]}"
    apt-get -o DPkg::Lock::Timeout=600 autoremove -y -qq
fi
apt-get -o DPkg::Lock::Timeout=600 install -y -qq \
    qemu-guest-agent \
    cloud-init \
    cloud-guest-utils

# Bật QEMU Guest Agent để Proxmox giao tiếp và freeze I/O khi backup
systemctl is-active --quiet qemu-guest-agent || systemctl start qemu-guest-agent
systemctl is-active qemu-guest-agent

echo "=== Hoàn tất cài đặt KVM Specifics ==="
