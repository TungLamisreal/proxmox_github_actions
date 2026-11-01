#!/usr/bin/env bash
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get -o DPkg::Lock::Timeout=600 update -qq
# Do not install role services in the universal LXC / KVM base.
apt-get -o DPkg::Lock::Timeout=600 install -y -qq --no-install-recommends \
  ca-certificates curl wget gnupg lsb-release psmisc zstd locales \
  python3 python3-apt iproute2 openssh-server prometheus-node-exporter

install -d -m 0755 /etc/systemd/system/ssh.service.d
cat > /etc/systemd/system/ssh.service.d/meowcloud-selfheal.conf <<'EOF'
[Service]
RuntimeDirectory=sshd
RuntimeDirectoryMode=0755
ExecStartPre=
ExecStartPre=/usr/bin/ssh-keygen -A
ExecStartPre=/usr/sbin/sshd -t
EOF
printf '%s\n' 'd /run/sshd 0755 root root -' > /etc/tmpfiles.d/sshd.conf
systemctl daemon-reload
systemctl enable ssh.service
systemctl enable prometheus-node-exporter.service
systemctl start prometheus-node-exporter.service
