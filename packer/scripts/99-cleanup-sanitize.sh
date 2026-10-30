#!/bin/bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

echo "=== [4/4] Bắt đầu dọn dẹp và làm sạch (Sanitize) Khuôn mẫu ==="

apt-get autoremove -y -qq
apt-get clean
rm -rf /var/lib/apt/lists/*
rm -rf /var/cache/apt/archives/*
rm -f /var/lib/apt/periodic/update-success-stamp

# Đánh dấu cache cũ để Ansible buộc refresh khi clone
mkdir -p /var/lib/apt/lists
touch -d '@1' /var/lib/apt/lists

# Chỉ xóa khóa định danh cũ để các Clone không dùng chung một vân tay SSH.
# Drop-in systemd ở bước 1 sẽ tự động sinh lại khóa mới tinh khi Clone khởi động.
rm -f /etc/ssh/ssh_host_*

truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id

if command -v cloud-init &> /dev/null; then
    cloud-init clean --logs --seed
    rm -rf /var/lib/cloud/*
fi

cat /dev/null > /var/log/wtmp
cat /dev/null > /var/log/lastlog
rm -rf /tmp/* /var/tmp/*
rm -f /root/.bash_history
history -c

echo "=== Hoàn tất Sanitize. Máy này đã sẵn sàng để niêm phong thành Golden Image! ==="
