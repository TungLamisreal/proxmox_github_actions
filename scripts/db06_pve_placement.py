#!/usr/bin/env python3
"""Read-only PVE placement gate: keep 3 etcd voters in 3 fault domains."""
import json
import shlex
import subprocess

EXPECTED = {("qemu", 271), ("qemu", 272), ("lxc", 121)}
PVE_HOST = "192.168.150.49"


def assert_distinct_nodes(resources):
    chosen = {}
    for entry in resources:
        key = (entry.get("type"), entry.get("vmid"))
        if key not in EXPECTED:
            continue
        if key in chosen:
            raise RuntimeError(f"Duplicate Proxmox guest record for {key}")
        if entry.get("status") != "running":
            raise RuntimeError(f"Expected DCS guest is not running: {key}")
        node = entry.get("node")
        if node not in ("node1", "node2", "node3"):
            raise RuntimeError(f"Unknown PVE location for {key}")
        chosen[key] = node
    if set(chosen) != EXPECTED:
        raise RuntimeError("Missing DB01/DB02/Observer from PVE cluster resources")
    if len(set(chosen.values())) != 3:
        raise RuntimeError(f"Etcd votes would share a physical PVE node: {chosen}")
    return chosen


def main():
    remote = shlex.join(["pvesh", "get", "/cluster/resources", "--type", "vm",
                         "--output-format", "json"])
    command = [
        "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=accept-new", "root@" + PVE_HOST, remote,
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=40,
                            check=False)
    if result.returncode:
        raise RuntimeError("Cannot audit live PVE placement; no etcd changes applied")
    placement = assert_distinct_nodes(json.loads(result.stdout))
    print("PASS: three etcd voter guests occupy different PVE nodes:", placement)


if __name__ == "__main__":
    main()
