#!/usr/bin/env python3
"""Read-only DB07 lab diagnostics after Patroni fails to start.

Never alter PGDATA, DCS, containers, firewall or config. Do not print
environment, config contents, private keys or unredacted PostgreSQL URLs.
"""
import re
import shlex
import subprocess

KEY = "/home/github-runner/.ssh/id_ed25519"
HOSTS = {"db-01": "192.168.150.71", "db-02": "192.168.150.72"}
SSH = [
    "-T", "-n", "-i", KEY,
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
]
SENSITIVE = [
    (re.compile(r"(?i)(password|passwd|passphrase|secret|token)(\\s*[:=]\\s*)([^\\s,;]+)"),
     r"\\1\\2[REDACTED]"),
    (re.compile(r"(?i)(postgres(?:ql)?://[^:/\\s@]+:)[^@\\s]+(@)"),
     r"\\1[REDACTED]\\2"),
]
RELEVANT = re.compile(
    r"(?i)error|fatal|exception|traceback|etcd|postgres|python|permission|"
    r"cert|tls|ssl|startup|failed|could not|missing|invalid|module|refused|"
    r"listening|authentication|bind|data_dir|connection|configuration|"
    r"pg_hba|wal_log_hints|no such|restarting|exited|address already in use"
)


def redact(text):
    for pattern, substitution in SENSITIVE:
        text = pattern.sub(substitution, text)
    return text


def ssh_read(host, argv, max_bytes=22000):
    command = ["ssh", *SSH, "root@" + host, shlex.join(argv)]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=35, check=False
        )
    except subprocess.TimeoutExpired:
        return "SSH timeout (no guest modifications)"
    if result.returncode == 255:
        # Avoid emitting arbitrary SSH diagnostics from an untrusted guest.
        return "SSH transport unavailable (exit 255); inspect key, firewall or guest reachability"
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    # Explicit truncation; never dump an unbounded application log.
    return redact(output[-max_bytes:]).strip() or ("empty output (exit %d)" % result.returncode)


def patroni_log_excerpt(raw):
    lines = raw.splitlines()
    matching = [line for line in lines if RELEVANT.search(line)]
    return "\n".join(matching[-65:])[:13000] or "No matching Patroni errors in recent container logs"


def main():
    for name, host in HOSTS.items():
        print(f"=== DB07 READ-ONLY DIAGNOSIS: {name} ({host}) ===", flush=True)
        checks = [
            ("legacy Docker (running|restart policy)", [
                "docker", "inspect", "--format",
                "{{.State.Status}}|{{.HostConfig.RestartPolicy.Name}}",
                "meow-postgres",
            ]),
            ("Patroni (status|exit|restarts|image)", [
                "docker", "inspect", "--format",
                "{{.State.Status}}|{{.State.ExitCode}}|{{.RestartCount}}|{{.Config.Image}}",
                "meow-patroni",
            ]),
            ("persistent PG_VERSION", ["cat", "/opt/pgdata/data/PG_VERSION"]),
            ("PGDATA and staged TLS directory ownership", [
                "stat", "-c", "%u:%g %a %n",
                "/opt/pgdata/data", "/opt/meowcloud-patroni",
                "/opt/meowcloud-patroni/tls",
                "/opt/meowcloud-patroni/patroni.yml",
                "/opt/meowcloud-patroni/tls/patroni-client-key.pem",
            ]),
            ("Patroni recent logs", [
                "docker", "logs", "--tail", "140", "meow-patroni"
            ]),
        ]
        for title, argv in checks:
            result = ssh_read(host, argv)
            if title == "Patroni recent logs":
                result = patroni_log_excerpt(result)
            print(f"--- {title} ---\n{result}", flush=True)
    print("DIAGNOSTICS COMPLETE: no containers, services, DCS, PGDATA or files were changed.")


if __name__ == "__main__":
    main()
