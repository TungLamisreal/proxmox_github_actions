#!/usr/bin/env python3
"""Read-only DB07 lab diagnostics after Patroni fails to start.

Never alter PGDATA, DCS, containers, firewall or config. Do not print
environment, config contents, private keys or unredacted PostgreSQL URLs.
"""
import json
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
    # Strip a complete key/value instead of risky backreference substitution.
    (re.compile(r"(?i)\b(?:password|passwd|passphrase|secret|token)\s*[:=]\s*[^\s,;]+"),
     "[REDACTED]"),
    (re.compile(r"(?i)postgres(?:ql)?://[^\s@]+@"),
     "postgresql://[REDACTED]@"),
]
RELEVANT = re.compile(
    r"(?i)error|fatal|exception|traceback|etcd|postgres|python|permission|"
    r"cert|tls|ssl|startup|failed|could not|missing|invalid|module|refused|"
    r"listening|authentication|bind|data_dir|connection|configuration|"
    r"pg_hba|wal_log_hints|no such|restarting|exited|address already in use"
)


# Execute on DB guest loopback only. Read just selected fields; never dump a
# Patroni REST document, its environment, credentials or cluster configuration.
PATRONI_REST_PROBE = """import json
from urllib import request, error
base = 'http://127.0.0.1:8008'
result = {}
def fetch(path):
    try:
        with request.urlopen(base + path, timeout=3) as response:
            return response.status, response.read(32768)
    except error.HTTPError as exc:
        return exc.code, b''
    except (error.URLError, TimeoutError, OSError):
        return 0, b''
for endpoint in ('/primary', '/replica', '/', '/cluster'):
    status, raw = fetch(endpoint)
    result[endpoint + '_http'] = status
    if status != 200 or not raw:
        continue
    try:
        doc = json.loads(raw)
    except (ValueError, UnicodeError):
        continue
    if endpoint == '/' and isinstance(doc, dict):
        result['local'] = {
            key: doc[key] for key in ('state', 'role', 'pending_restart')
            if key in doc and isinstance(doc[key], (str, int, bool))
        }
    if endpoint == '/cluster' and isinstance(doc, dict):
        members = doc.get('members', [])
        if isinstance(members, list):
            result['members'] = [
                {k: member[k] for k in ('name', 'role', 'state')
                 if k in member and isinstance(member[k], str)}
                for member in members[:10] if isinstance(member, dict)
            ]
print(json.dumps(result, sort_keys=True, separators=(',', ':')))
"""


def interpret_patroni_rest(payload):
    """Explain 503 without conflating a reachable API with an elected writer."""
    try:
        status = json.loads(payload)
    except (ValueError, TypeError):
        return "REST probe unavailable; inspect Docker status and Patroni logs"
    if not isinstance(status, dict):
        return "REST probe returned invalid status"
    primary = status.get("/primary_http")
    replica = status.get("/replica_http")
    local = status.get("local", {})
    role = local.get("role", "unknown") if isinstance(local, dict) else "unknown"
    state = local.get("state", "unknown") if isinstance(local, dict) else "unknown"
    if primary == 200:
        return "Patroni reports PRIMARY; verify SQL and DCS before DB02 resume"
    if replica == 200:
        return "Patroni reports REPLICA; DB01 must not be assumed the primary"
    if primary == 503:
        return f"Patroni API responds, but is NOT PRIMARY (role={role}, state={state}); inspect logs and DCS lease"
    if primary == 0:
        return "Patroni API not reachable on DB loopback; inspect service/port and logs"
    return f"Patroni primary status={primary}; inspect local role={role}, state={state}"


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
            ("Patroni REST local role and cluster", [
                "python3", "-c", PATRONI_REST_PROBE,
            ]),
            ("PostgreSQL TCP readiness inside the Patroni container", [
                "docker", "exec", "-u", "postgres", "meow-patroni",
                "pg_isready", "-h", "127.0.0.1", "-p", "5432",
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
            if title == "Patroni REST local role and cluster":
                print("PATRONI INTERPRETATION: " + interpret_patroni_rest(result), flush=True)
    print("DIAGNOSTICS COMPLETE: no containers, services, DCS, PGDATA or files were changed.")


if __name__ == "__main__":
    main()
