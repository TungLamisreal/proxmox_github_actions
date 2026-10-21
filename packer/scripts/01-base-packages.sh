#!/bin/bash
# Đảm bảo tiến trình cài đặt không bị dừng lại hỏi (prompt)
export DEBIAN_FRONTEND=noninteractive

echo "=== [1/4] Cập nhật OS và cài đặt các công cụ nền tảng ==="
apt-get update -qq
apt-get upgrade -y -qq

# Cài đặt các package cơ bản và Prometheus Node Exporter
apt-get install -y -qq \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    psmisc \
    prometheus-node-exporter

# Đảm bảo Node Exporter tự khởi động cùng hệ thống
systemctl enable prometheus-node-exporter
systemctl start prometheus-node-exporter

echo "=== Hoàn tất cài đặt Base Packages ==="
