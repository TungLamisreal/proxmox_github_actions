#!/bin/bash
export DEBIAN_FRONTEND=noninteractive

echo "=== [1/4] Cập nhật OS và cài đặt các công cụ nền tảng ==="
apt-get update -qq
apt-get upgrade -y -qq

apt-get install -y -qq \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    psmisc \
    zstd \
    locales \
    openssh-server \
    prometheus-node-exporter

echo "LC_ALL=C.UTF-8" >> /etc/environment
echo "LANG=C.UTF-8" >> /etc/environment

echo "=== CẤU HÌNH AUTO-REPAIR SSH CHO TẤT CẢ LXC CLONE ==="
# Cấu hình này ép mọi Linked Clone tự tạo /run/sshd và sinh Host Keys riêng biệt ở lần boot đầu tiên
mkdir -p /etc/systemd/system/ssh.service.d
cat << 'EOF' > /etc/systemd/system/ssh.service.d/meowcloud-selfheal.conf
[Service]
RuntimeDirectory=sshd
RuntimeDirectoryMode=0755

# Xóa danh sách ExecStartPre kế thừa từ service gốc
ExecStartPre=

# Sinh các host keys còn thiếu trước khi kiểm tra sshd
ExecStartPre=/usr/bin/ssh-keygen -A
ExecStartPre=/usr/sbin/sshd -t
EOF

# Đảm bảo tmpfiles cũng khai báo chuẩn
echo "d /run/sshd 0755 root root -" > /etc/tmpfiles.d/sshd.conf
systemctl daemon-reload
systemctl enable ssh

systemctl enable prometheus-node-exporter
systemctl start prometheus-node-exporter

echo "=== Hoàn tất cài đặt Base Packages ==="
