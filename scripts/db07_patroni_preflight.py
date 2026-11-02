#!/usr/bin/env python3
"""Read-only Patroni takeover blockers report for MeowCloud DB01/DB02.

Do not print secrets, stop services, probe credentials, mutate PGDATA or HA state.
A successful audit means data was collected, NOT that takeover is approved.
"""
import argparse
import json
import shlex
import subprocess

PVE_HOST = "192.168.150.49"
DB = {"db-01": "192.168.150.71", "db-02": "192.168.150.72"}
LB = {"lb-01": "192.168.150.61", "lb-02": "192.168.150.62"}
DOCKER_FMT = "{{.State.Running}}|{{.HostConfig.RestartPolicy.Name}}|{{.Config.Image}}|{{range .Mounts}}{{.Source}}={{.Destination}};{{end}}"


class AuditError(RuntimeError):
    pass


def ssh(host, argv):
    remote = shlex.join(argv)
    proc = subprocess.run(
        ["ssh", "-T", "-i", "/home/github-runner/.ssh/id_ed25519",
         "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
         "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new",
         "root@" + host, remote],
        capture_output=True, text=True, check=False, timeout=35,
    )
    if proc.returncode:
        raise AuditError(f"Read-only SSH inspection failed for {host}, rc={proc.returncode}")
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
    args = parser.parse_args()

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
