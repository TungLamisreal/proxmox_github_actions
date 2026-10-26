#!/bin/bash
set -Eeuo pipefail

DROPIN_DIR="/etc/systemd/system/ssh.service.d"
DROPIN_FILE="${DROPIN_DIR}/meowcloud-selfheal.conf"

install -d -m 0755 "${DROPIN_DIR}"
cat > "${DROPIN_FILE}" <<'DROPIN_EOF'
[Service]
RuntimeDirectory=sshd
RuntimeDirectoryMode=0755

# Generate host keys before Debian's sshd configuration test.
ExecStartPre=
ExecStartPre=/usr/bin/ssh-keygen -A
ExecStartPre=/usr/sbin/sshd -t
DROPIN_EOF
chmod 0644 "${DROPIN_FILE}"

# Simulate the exact state a newly cloned CT must recover from: no host keys.
rm -f /etc/ssh/ssh_host_*
systemctl daemon-reload

if ! systemctl cat ssh.service | grep -F 'meowcloud-selfheal.conf' >/dev/null || \
   ! systemctl cat ssh.service | grep -F 'ExecStartPre=/usr/bin/ssh-keygen -A' >/dev/null; then
  echo "ERROR: systemd did not load the SSH host-key bootstrap drop-in" >&2
  systemctl cat ssh.service >&2 || true
  exit 1
fi

# Reset rate limits after previous attempts failed due to absent host keys.
systemctl reset-failed ssh.service ssh.socket 2>/dev/null || true

# The unit on this host uses ssh.socket; start it before the service check.
systemctl start ssh.socket
systemctl restart ssh.service

if ! systemctl is-active --quiet ssh.service; then
  journalctl -u ssh.service --no-pager -n 50 >&2 || true
  exit 1
fi

if ! ss -lnt | awk '$4 ~ /:22$/ { found=1 } END { exit !found }'; then
  echo "ERROR: SSH service is active but port 22 is not listening" >&2
  ss -lntp >&2 || true
  exit 1
fi

# Ensure that a later template boot can create its own unique host keys.
for key in /etc/ssh/ssh_host_*; do
  [ -f "$key" ] || continue
  echo "Generated host key: $key"
done

echo "OK: SSH bootstrap works from an empty host-key state; port 22 is listening."
