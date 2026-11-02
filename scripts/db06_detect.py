#!/usr/bin/env python3
"""Read-only streaming topology gate for the DB06 job pipeline.

Do not infer leadership from hostnames or a successful TCP connection.
A live replica must share DB01's PostgreSQL system identifier AND be streaming.
"""
import argparse
from pathlib import Path
import subprocess
from typing import NamedTuple


class Snapshot(NamedTuple):
    in_recovery: str
    system_id: str
    senders: int
    receivers: int


def detect(primary: Snapshot, standby: Snapshot, mode: str, confirmed: bool) -> bool:
    """Return whether new standalone DB02 initialization is required.

    Fail closed on promoted DB02, conflicting identities, partial replication
    or mismatched topologies. No writes happen in this module.
    """
    if mode not in ("full", "verify", "ha-readiness"):
        raise ValueError("Unknown DB06 execution mode")
    if primary.in_recovery != "f":
        raise ValueError("DB01 is not the current writer; do not bootstrap")
    if standby.in_recovery == "t":
        if (primary.system_id != standby.system_id or
                primary.senders < 1 or standby.receivers < 1):
            raise ValueError("Standby exists but streaming/lineage is broken; manual repair required")
        return False
    if standby.in_recovery != "f":
        raise ValueError("DB02 recovery state is unknown")
    if primary.system_id == standby.system_id:
        raise ValueError("Both hosts appear writable in one PostgreSQL lineage: split-brain risk")
    if mode != "full":
        raise ValueError("Streaming is not set up; verify cannot prepare a primary")
    if not confirmed:
        raise ValueError("New standby initialization requires APPLY_DB_REPLICATION")
    return True


def remote_sql(host: str, sql: str) -> str:
    # The only remote operation is a read-only SELECT sent over ssh stdin.
    command = [
        "ssh", "-i", "/home/github-runner/.ssh/id_ed25519",
        "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
        "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null", f"root@{host}",
        "docker", "exec", "-i", "-u", "postgres", "meow-postgres", "psql",
        "-XAt", "-F", "|", "-v", "ON_ERROR_STOP=1", "-U", "postgres",
        "-d", "postgres",
    ]
    result = subprocess.run(command, input=sql, capture_output=True, text=True,
                            timeout=35, check=True)
    return result.stdout.strip()


def snapshot(host: str) -> Snapshot:
    sql = """SELECT pg_is_in_recovery(),
             (SELECT system_identifier FROM pg_control_system()),
             (SELECT count(*) FROM pg_stat_replication WHERE state = 'streaming'),
             (SELECT count(*) FROM pg_stat_wal_receiver WHERE status = 'streaming');
          """
    cells = remote_sql(host, sql).split("|")
    if len(cells) != 4 or not cells[1].isdigit():
        raise ValueError(f"Unexpected PostgreSQL result from {host}")
    return Snapshot(cells[0], cells[1], int(cells[2]), int(cells[3]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", required=True)
    parser.add_argument("--confirmation", default="")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--backup-reference", required=True)
    args = parser.parse_args()
    if not Path(args.inventory).is_file():
        parser.error("Runtime inventory not found")
    a = snapshot("192.168.150.71")
    b = snapshot("192.168.150.72")
    bootstrap = detect(a, b, args.mode, args.confirmation == "APPLY_DB_REPLICATION")
    with open(args.output, "a", encoding="utf8") as output:
        output.write(f"bootstrap_required={str(bootstrap).lower()}\n")
        output.write(f"backup_reference={args.backup_reference}\n")
    print("PASS: DB06 topology verified; bootstrap required:", bootstrap)


if __name__ == "__main__":
    main()

