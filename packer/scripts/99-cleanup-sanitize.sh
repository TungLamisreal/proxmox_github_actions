#!/bin/bash
export DEBIAN_FRONTEND=noninteractive

echo "=== [4/4] Bắt đầu dọn dẹp và làm sạch (Sanitize) Khuôn mẫu ==="

# 1. Dọn dẹp cache của APT để giảm dung lượng file nén
apt-get autoremove -y -qq
apt-get clean
rm -rf /var/lib/apt/lists/*
rm -rf /var/cache/apt/archives/*

# 2. Xóa các khóa định danh SSH (Máy nhân bản sẽ tự sinh khóa mới vào lần boot đầu)
rm -f /etc/ssh/ssh_host_*

# 3. Làm sạch Machine ID (Tuyệt đối không xóa file, chỉ làm rỗng nội dung)
# Nếu file này bị trùng, các máy ảo có thể nhận chung 1 IP từ DHCP.
truncate -s 0 /etc/machine-id
rm -f /var/lib/dbus/machine-id
ln -s /etc/machine-id /var/lib/dbus/machine-id

# 4. Làm sạch trạng thái Cloud-Init (Chỉ có tác dụng với KVM, vô hại với LXC)
if command -v cloud-init &> /dev/null; then
    cloud-init clean --logs --seed
    rm -rf /var/lib/cloud/*
fi

# 5. Xóa log hệ thống và lịch sử Bash
cat /dev/null > /var/log/wtmp
cat /dev/null > /var/log/lastlog
rm -rf /tmp/* /var/tmp/*
rm -f /root/.bash_history
history -c

echo "=== Hoàn tất Sanitize. Máy này đã sẵn sàng để niêm phong thành Golden Image! ==="
