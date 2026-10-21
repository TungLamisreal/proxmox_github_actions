#!/bin/bash
export DEBIAN_FRONTEND=noninteractive

echo "=== [3/4] Cài đặt cấu phần đặc thù cho máy ảo KVM ==="
apt-get update -qq
apt-get install -y -qq \
    qemu-guest-agent \
    cloud-init \
    cloud-guest-utils

# Bật QEMU Guest Agent để Proxmox giao tiếp và freeze I/O khi backup
systemctl enable qemu-guest-agent

echo "=== Hoàn tất cài đặt KVM Specifics ==="
