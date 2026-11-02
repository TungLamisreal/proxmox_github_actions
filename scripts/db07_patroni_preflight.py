#!/usr/bin/env python3
"""Read-only Patroni takeover blockers report for MeowCloud DB01/DB02.

Do not print secrets, stop services, probe credentials, mutate PGDATA or HA state.
A successful audit means data was collected, NOT that takeover is approved.
"""
import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess

PVE_HOST = "192.168.150.49"
DB = {"db-01": "192.168.150.71", "db-02": "192.168.150.72"}
LB = {"lb-01": "192.168.150.61", "lb-02": "192.168.150.62"}
OBSERVER = {"observer": "192.168.150.21"}
SSH_IDENTITY = "/home/github-runner/.ssh/id_ed25519"
DEFAULT_INVENTORY = "/opt/meowcloud-iac/ansible/inventory.ini"
# This lab rebuilds SSH guests from golden images and guest host keys change.
# Match the proven DB06 SSH options; keep key-based login mandatory. For
# production, replace /dev/null known_hosts with provisioning-pinned keys.
SSH_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "ConnectTimeout=10",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
]

DOCKER_FMT = "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}|{{.Config.Image}}|{{range .Mounts}}{{.Source}}={{.Destination}};{{end}}"


class AuditError(RuntimeError):
    pass


def verify_runtime_inventory(path):
    """Refuse to probe stale or unexpected targets after Terraform rebuild."""
    target = Path(path)
    if not target.is_file():
        raise AuditError(f"Runtime inventory missing: {path}; run Workflow 01 first")
    expected = {"databases": DB, "loadbalancers": LB, "monitoring": OBSERVER}
    actual = {section: {} for section in expected}
    section = None
    for raw in target.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section not in expected:
            continue
        fields = line.split()
        if not fields:
            continue
        name = fields[0]
        values = dict(field.split("=", 1) for field in fields[1:] if "=" in field)
        if "ansible_host" in values:
            if name in actual[section]:
                raise AuditError(f"Duplicate {name} in runtime inventory")
            actual[section][name] = values["ansible_host"]
    for section, hosts in expected.items():
        if actual[section] != hosts:
            raise AuditError(
                f"Stale or unexpected [{section}] runtime inventory after 00/01: "
                f"expected {hosts}, got {actual[section]}"
            )


def ssh_error_reason(stderr, returncode):
    """Bounded, credential-free SSH failure diagnosis."""
    message = (stderr or "").lower()
    if "host key verification failed" in message or "remote host identification has changed" in message:
        return "host-key mismatch after VM recreation"
    if "permission denied" in message or "publickey" in message:
        return "SSH public-key authentication rejected"
    if "connection refused" in message:
        return "guest SSH daemon/port 22 refused connection"
    if "no route to host" in message or "network is unreachable" in message:
        return "guest or network route is unreachable"
    if "timed out" in message or "connection timeout" in message:
        return "SSH TCP timeout (firewall/guest/network)"
    if "could not resolve hostname" in message:
        return "SSH hostname resolution failed"
    if returncode == 127 or "command not found" in message:
        return "remote read-only inspection command unavailable"
    if returncode == 255:
        return "SSH transport/authentication failure"
    return "remote read-only command exited unsuccessfully"


def ssh(host, argv):
    # ssh joins remote argv into a shell command. Quote the complete command,
    # including the Docker Go template, so it survives the remote shell.
    remote = shlex.join(argv)
    command = ["ssh", "-T", "-n", "-i", SSH_IDENTITY, *SSH_OPTIONS,
               "root@" + host, remote]
    try:
        proc = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=35,
        )
    except subprocess.TimeoutExpired as exc:
        raise AuditError(f"Read-only SSH inspection timed out for {host}") from exc
    if proc.returncode:
        reason = ssh_error_reason(proc.stderr, proc.returncode)
        raise AuditError(
            f"Read-only SSH inspection failed for {host}, rc={proc.returncode}: "
            f"{reason}. Check CT120 key/guest readiness and Workflow 01 SSH setup."
        )
    return proc.stdout.strip()

def parse_container(text):
    fields = text.split("|", 3)
    if len(fields) != 4 or fields[0] not in ("true", "false"):
        raise AuditError("Unexpected Docker inspect result (no secrets will be logged)")
    mounts = dict(
        pair.split("=", 1) for pair in fields[3].split(";") if pair and "=" in pair
    )
    return {
        "running": fields[0] == "true",
        "restart_policy": fields[1],
        "image": fields[2],
        "mounts": mounts,
    }


def evaluate(db_containers, mounts, pve_resources, lb_listeners):
    """Produce explicit blockers without changing any service."""
    issues = []
    if set(db_containers) != set(DB) or set(mounts) != set(DB):
        raise AuditError("DB audit did not inspect exactly DB01 and DB02")
    if set(lb_listeners) != set(LB):
        raise AuditError("LB audit did not inspect both load balancers")

    for name in DB:
        db = db_containers[name]
        if not db["image"].startswith("postgres:15-alpine"):
            issues.append(f"{name}: existing image requires PostgreSQL 15 compatibility review")
        if db["mounts"].get("/opt/pgdata/data") != "/var/lib/postgresql/data":
            issues.append(f"{name}: expected persistent PGDATA Docker bind mount not present")
        if mounts[name] != "/opt/pgdata":
            issues.append(f"{name}: PGDATA filesystem not mounted at /opt/pgdata")
        if db["running"]:
            issues.append(f"{name}: legacy standalone PostgreSQL container is still running")
        if db["restart_policy"] != "no":
            issues.append(f"{name}: Docker restart policy {db['restart_policy']!r} could resurrect the legacy writer")

    ha = {x.get("sid"): x for x in pve_resources if isinstance(x, dict)}
    if "vm:271" in ha and ha["vm:271"].get("state", "started") == "started":
        issues.append("VM271: Proxmox HA still starts DB01; coordinate guest boot and Patroni handover")
    for name, listening in lb_listeners.items():
        if not listening:
            issues.append(f"{name}: database writer proxy port 5432 not listening")
    # Even a clean infrastructure report would not prove exclusive writer
    # ownership, backups, credentials, backend cutover or safe automatic failover.
    issues.append("Cutover not approved: requires supervised DB01 handover, fenced legacy Docker, tested backup/rollback and app VIP migration")
    return issues


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-ready", action="store_true",
                        help="Fail closed while any ownership blocker remains")
    parser.add_argument("--inventory", default=os.environ.get("INVENTORY", DEFAULT_INVENTORY))
    parser.add_argument("--validate-inventory-only", action="store_true",
                        help="Validate Terraform-generated inventory after Workflow 01; no SSH")
    args = parser.parse_args()
    verify_runtime_inventory(args.inventory)
    if args.validate_inventory_only:
        print("PASS: DB07 runtime inventory matches DB01/DB02/LB01/LB02/Observer; no SSH")
        return
    if not Path(SSH_IDENTITY).is_file():
        raise AuditError("Canonical CT120 SSH private key is missing; run Workflow 01 preflight")

    containers, mounts = {}, {}
    for name, ip in DB.items():
        containers[name] = parse_container(ssh(
            ip, ["docker", "inspect", "--format", DOCKER_FMT, "meow-postgres"]
        ))
        mounts[name] = ssh(ip, ["findmnt", "-n", "-o", "TARGET", "--target", "/opt/pgdata"])
    pve = json.loads(ssh(PVE_HOST, ["pvesh", "get", "/cluster/ha/resources",
                                    "--output-format", "json"]))
    if not isinstance(pve, list):
        raise AuditError("Unexpected Proxmox HA resource response")
    listeners = {}
    for name, ip in LB.items():
        lines = ssh(ip, ["ss", "-lnt"])
        listeners[name] = any(
            line.split()[3].endswith(":5432")
            for line in lines.splitlines()[1:] if len(line.split()) >= 4
        )

    issues = evaluate(containers, mounts, pve, listeners)
    print("PASS: DB07 read-only lifecycle inspection completed for both DBs, LBs and PVE HA")
    print("READINESS: BLOCKED (no Patroni takeover performed)")
    for idx, message in enumerate(issues, 1):
        print(f"BLOCKER {idx}: {message}")
    if args.require_ready and issues:
        raise AuditError("Fail-closed: DB07 requires explicit lifecycle reconciliation")


if __name__ == "__main__":
    try:
        main()
    except (AuditError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        raise SystemExit(f"STOP: {exc}") from None
