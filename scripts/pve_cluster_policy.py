#!/usr/bin/env python3
"""MeowCloud day-0 PVE node boot staggering and narrowly scoped HA policy.

Managed HA resources: ct:121 observer, vm:271 database primary only.
Never manages ct:120 (the separately user-managed Auto-VM), tunnels, LBs,
backends, frontends, or vm:272. Uses PVE's cluster API via trusted SSH.
All mutating operations are explicitly requested by workflow 01 or 02.
"""
import argparse
import json
import shlex
import subprocess
import sys

PVE_HOST = "192.168.150.49"
NODES = ("node1", "node2", "node3")
# Approved node-specific boot delays; preserve current PVE startup staggering.
BOOT_DELAYS = {"node1": 30, "node2": 30, "node3": 90}
# Proxmox HA groups, for PVE versions that support group nofailback.
HA = (
    ("ct:121", "meow-observer", "node1:2,node2:1,node3:1", "lxc", 121),
    ("vm:271", "meow-db-primary", "node2:2,node1:1,node3:1", "qemu", 271),
)


class PolicyError(RuntimeError):
    pass


def remote(*args):
    command = " ".join(shlex.quote(str(arg)) for arg in args)
    result = subprocess.run(
        [
            "ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "StrictHostKeyChecking=accept-new",
            "root@" + PVE_HOST, command,
        ],
        capture_output=True, text=True, timeout=65, check=False,
    )
    if result.returncode != 0:
        # No API tokens/passwords are passed in arguments.
        raise PolicyError(f"PVE command failed (rc={result.returncode}): {command}\n"
                          f"{result.stderr[-2000:]}")
    return result.stdout.strip()


def pvesh_json(*args):
    data = remote("pvesh", *args, "--output-format", "json")
    try:
        return json.loads(data)
    except ValueError as exc:
        raise PolicyError("Invalid PVE JSON response") from exc


def by_key(items, key):
    if not isinstance(items, list):
        raise PolicyError("Expected PVE cluster API list")
    return {item[key]: item for item in items}


def check_cluster():
    nodes = by_key(pvesh_json("get", "/nodes"), "node")
    for name in NODES:
        if name not in nodes or nodes[name].get("status") != "online":
            raise PolicyError(f"Required node is not online: {name}")
    status = remote("pvecm", "status")
    if "Quorate:          Yes" not in status and not any(
        line.strip().startswith("Quorate:") and line.split(":", 1)[1].strip() == "Yes"
        for line in status.splitlines()
    ):
        raise PolicyError("PVE cluster has no confirmed quorum")
    print("[PASS] Proxmox nodes online and cluster quorate")


def check_guests():
    resources = pvesh_json("get", "/cluster/resources", "--type", "vm")
    for sid, _, _, kind, vmid in HA:
        matched = [r for r in resources if r.get("vmid") == vmid and r.get("type") == kind]
        if len(matched) != 1:
            raise PolicyError(f"Expected exactly one {kind} guest {vmid}; found {len(matched)}")
        print(f"[PASS] Guest {sid} exists on {matched[0].get('node')}")


def ha_groups():
    return by_key(pvesh_json("get", "/cluster/ha/groups"), "group")


def ha_resources():
    return by_key(pvesh_json("get", "/cluster/ha/resources"), "sid")


def normalize_nodes(value):
    if not isinstance(value, str):
        raise PolicyError(f"Unexpected HA group nodes: {value!r}")
    parts = [part.strip() for part in value.split(",") if part.strip()]
    return {p.split(":")[0]: p.split(":")[1] if ":" in p else "1" for p in parts}


def group_matches(group, desired_nodes):
    return (
        normalize_nodes(group.get("nodes", "")) == normalize_nodes(desired_nodes)
        and str(group.get("nofailback", "0")).lower() in ("1", "true")
        and str(group.get("restricted", "0")).lower() in ("0", "false")
    )


def set_startup_delay():
    check_cluster()
    for name in NODES:
        endpoint = f"/nodes/{name}/config"
        current = pvesh_json("get", endpoint)
        delay = BOOT_DELAYS[name]
        setting = str(current.get("startall-onboot-delay", "0"))
        if setting != str(delay):
            remote("pvesh", "set", endpoint, "--startall-onboot-delay", str(delay))
        actual = pvesh_json("get", endpoint)
        if str(actual.get("startall-onboot-delay", "0")) != str(delay):
            raise PolicyError(f"Startup delay not persisted on {name}")
        print(f"[PASS] {name}: startall-onboot-delay={delay}s")


def apply_ha():
    check_cluster()
    check_guests()
    groups = ha_groups()
    resources = ha_resources()

    # Fail before any mutation if a resource or named group is controlled by
    # someone else, or if this PVE release lacks the required HA group policy.
    for sid, group, nodes, _, _ in HA:
        existing = resources.get(sid)
        if existing and (existing.get("group") != group or
                         existing.get("state", "started") != "started"):
            raise PolicyError(f"Refusing to change externally managed HA resource {sid}: {existing}")
        if group in groups and not group_matches(groups[group], nodes):
            raise PolicyError(f"Refusing to change conflicting HA group {group}: {groups[group]}")

    for sid, group, nodes, _, _ in HA:
        if group not in groups:
            remote("pvesh", "create", "/cluster/ha/groups", "--group", group,
                   "--nodes", nodes, "--nofailback", "1", "--restricted", "0")
            print(f"[CREATE] HA group {group}")
        if sid not in resources:
            remote("pvesh", "create", "/cluster/ha/resources",
                   "--sid", sid, "--group", group, "--state", "started",
                   "--max_restart", "1", "--max_relocate", "1")
            print(f"[CREATE] HA service {sid}")
        else:
            print(f"[KEEP] HA service {sid}")

    final_groups = ha_groups()
    final_resources = ha_resources()
    for sid, group, nodes, _, _ in HA:
        if group not in final_groups or not group_matches(final_groups[group], nodes):
            raise PolicyError(f"HA group verification failed: {group}")
        if final_resources.get(sid, {}).get("group") != group:
            raise PolicyError(f"HA resource verification failed: {sid}")
        print(f"[PASS] {sid}: HA group={group}, nofailback=1")


def remove_ha():
    check_cluster()
    resources = ha_resources()
    groups = ha_groups()
    # Preflight first: do not interfere with manually configured HA resources.
    for sid, group, _, _, _ in HA:
        existing = resources.get(sid)
        if existing and existing.get("group") != group:
            raise PolicyError(f"Refusing to remove HA service {sid} in a foreign group")
    # Check whether any other service uses our groups before changing anything.
    for sid, item in resources.items():
        if item.get("group") in {row[1] for row in HA} and sid not in {row[0] for row in HA}:
            raise PolicyError(f"Foreign HA service {sid} uses a MeowCloud HA group")

    for sid, _, _, _, _ in HA:
        if sid in resources:
            remote("pvesh", "delete", f"/cluster/ha/resources/{sid}")
            print(f"[REMOVE] HA service {sid} (guest remains)")
    for _, group, _, _, _ in HA:
        if group in groups:
            remote("pvesh", "delete", f"/cluster/ha/groups/{group}")
            print(f"[REMOVE] HA group {group}")
    # Crucially never touch ct:120.
    print("[PASS] Removed only ct:121/vm:271 HA registrations; ct:120 untouched")


def audit():
    check_cluster()
    for name in NODES:
        node = pvesh_json("get", f"/nodes/{name}/config")
        print(f"[READ] {name} startup delay={node.get('startall-onboot-delay', 0)}")
    resources = ha_resources()
    groups = ha_groups()
    for sid, group, nodes, _, _ in HA:
        print(f"[READ] {sid}: {resources.get(sid, 'not registered')}")
        print(f"[READ] {group}: {groups.get(group, 'missing')}")
    print(f"[READ] ct:120 user-managed: {'registered' if 'ct:120' in resources else 'not registered'}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("audit", "set-startup-delay", "apply-ha", "remove-ha"),
                        required=True)
    args = parser.parse_args()
    try:
        {
            "audit": audit, "set-startup-delay": set_startup_delay,
            "apply-ha": apply_ha, "remove-ha": remove_ha,
        }[args.mode]()
    except (PolicyError, subprocess.TimeoutExpired) as exc:
        print(f"[STOP] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
