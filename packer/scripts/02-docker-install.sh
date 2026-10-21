#!/bin/bash
export DEBIAN_FRONTEND=noninteractive

echo "=== [2/4] Cài đặt Docker Engine và Python SDK ==="
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian \
  "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update -qq
# Cài đặt Docker và python3-docker để Ansible có thể quản lý Container (community.docker)
apt-get install -y -qq \
    docker-ce \
    docker-ce-cli \
    containerd.io \
    docker-buildx-plugin \
    docker-compose-plugin \
    python3-docker

systemctl enable docker
systemctl start docker

echo "=== Hoàn tất cài đặt Docker Engine ==="
