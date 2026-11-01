#!/usr/bin/env python3
"""MeowCloud day-0 PVE node boot staggering and narrowly scoped HA policy.

Managed HA resources: ct:121 observer, vm:271 database primary only.
Never manages ct:120 (the separately configured Auto-VM), tunnels, LBs,
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
# Guest-level Start/Shutdown Order captured from live Proxmox configuration.
# CT120 (order=1,up=60) is independently user-managed; NEVER edit that guest.
DB_GUEST_STARTUP = {
    271: "order=5,up=60",  # DB-01 Primary (HA-managed after Day-0)
    272: "order=4,up=60",  # DB-02 (not HA-managed)
}
# PVE 9.2: HA node-affinity rules replace the removed HA groups API.
# Positive non-strict rules set node priority; failback=0 and auto-rebalance=0
# on each HA service prevent automatic return/rebalancing (PVE 9.2 API).
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


def ha_rules():
    # PVE 9.2 removed /cluster/ha/groups; rules are now first-class objects.
    return by_key(pvesh_json("get", "/cluster/ha/rules"), "rule")


def ha_resources():
    return by_key(pvesh_json("get", "/cluster/ha/resources"), "sid")


def normalize_nodes(value):
    if not isinstance(value, str):
        raise PolicyError(f"Unexpected HA rule nodes: {value!r}")
    parts = [part.strip() for part in value.split(",") if part.strip()]
    result = {}
    for part in parts:
        fields = part.split(":")
        if len(fields) > 2 or not fields[0] or (len(fields) == 2 and not fields[1].isdigit()):
            raise PolicyError(f"Invalid node affinity entry: {part!r}")
        if fields[0] in result:
            raise PolicyError(f"Duplicate affinity node: {fields[0]}")
        result[fields[0]] = fields[1] if len(fields) == 2 else "1"
    return result


def rule_resources(rule):
    value = rule.get("resources", "")
    if isinstance(value, str):
        return set(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    raise PolicyError(f"Unexpected HA rule resources: {value!r}")


def disabled(value):
    return str(value).lower() in ("1", "true", "yes")


def rule_matches(rule, sid, desired_nodes):
    # On PVE 9, strict=0 is placement preference, NOT failback suppression.
    return (
        rule.get("type") == "node-affinity"
        and rule.get("affinity", "positive") == "positive"
        and not disabled(rule.get("strict", 0))
        and not disabled(rule.get("disable", 0))
        and rule_resources(rule) == {sid}
        and normalize_nodes(rule.get("nodes", "")) == normalize_nodes(desired_nodes)
    )


def resource_matches(resource):
    # Missing PVE fields have documented default=1 (unsafe for this policy).
    # Require explicit booleans off, including when evaluating existing HA.
    return (
        str(resource.get("failback", 1)).lower() in ("0", "false")
        and str(resource.get("auto-rebalance", 1)).lower() in ("0", "false")
        and resource.get("state", "started") == "started"
        and resource.get("group") in (None, "")
    )


def validate_owned_rules(rules, resources):
    """Refuse ambiguous ownership or third-party affinity before any writes."""
    for sid, name, nodes, _, _ in HA:
        current_rule = rules.get(name)
        if current_rule is not None and not rule_matches(current_rule, sid, nodes):
            raise PolicyError(f"Conflicting existing HA rule {name}: {current_rule}")
        for other_name, rule in rules.items():
            if other_name != name and sid in rule_resources(rule):
                raise PolicyError(f"External HA rule {other_name} already targets {sid}")
        current_resource = resources.get(sid)
        if current_resource is not None:
            if not resource_matches(current_resource):
                raise PolicyError(
                    f"HA service {sid} has incompatible state or failback/rebalance policy"
                )
            if current_rule is None:
                raise PolicyError(
                    f"HA service {sid} exists without owned rule {name}; "
                    "refusing to claim or delete externally managed service"
                )
        elif current_rule is not None:
            raise PolicyError(f"Rule {name} exists but service {sid} is missing")


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


def parse_startup(value):
    """Normalize Proxmox startup string without relying on option ordering."""
    if value is None or value == "":
        return {}
    if not isinstance(value, str):
        raise PolicyError("Unexpected PVE startup config type")
    result = {}
    for part in value.split(","):
        if "=" not in part:
            raise PolicyError(f"Unrecognized guest startup entry: {part!r}")
        key, val = [piece.strip() for piece in part.split("=", 1)]
        if key in result or key not in ("order", "up", "down") or not val.isdigit():
            raise PolicyError(f"Invalid guest startup entry: {part!r}")
        result[key] = int(val)
    return result


def set_guest_startup():
    """Restore only the observed startup policies for the two DB VMs.

    This is config metadata (no start/stop/reboot). A fresh Terraform create
    does not necessarily restore manually set Proxmox startup values.
    """
    check_cluster()
    resources = pvesh_json("get", "/cluster/resources", "--type", "vm")
    for vmid, desired in DB_GUEST_STARTUP.items():
        matches = [r for r in resources
                   if r.get("vmid") == vmid and r.get("type") == "qemu"]
        if len(matches) != 1 or matches[0].get("node") not in NODES:
            raise PolicyError(f"Expected exactly one DB QEMU VM {vmid} on known node")
        node = matches[0]["node"]
        endpoint = f"/nodes/{node}/qemu/{vmid}/config"
        current = pvesh_json("get", endpoint)
        expected = parse_startup(desired)
        if parse_startup(current.get("startup")) != expected:
            remote("pvesh", "set", endpoint, "--startup", desired)
        actual = pvesh_json("get", endpoint)
        if parse_startup(actual.get("startup")) != expected:
            raise PolicyError(f"Guest startup policy not persisted for VM {vmid}")
        print(f"[PASS] DB VM {vmid} on {node}: startup={desired}")
    print("[PASS] CT120 startup policy left untouched")


def apply_ha():
    check_cluster()
    check_guests()
    rules = ha_rules()
    resources = ha_resources()
    validate_owned_rules(rules, resources)

    # PVE 9 node-affinity rules refer to HA-registered service IDs.
    # Create resources before the corresponding rules.
    for sid, name, nodes, _, _ in HA:
        if sid not in resources:
            remote("pvesh", "create", "/cluster/ha/resources",
                   "--sid", sid, "--state", "started",
                   "--failback", "0", "--auto-rebalance", "0",
                   "--max_restart", "1", "--max_relocate", "1")
            print(f"[CREATE] HA service {sid}")
        else:
            print(f"[KEEP] HA service {sid}")
        if name not in rules:
            remote("pvesh", "create", "/cluster/ha/rules",
                   "--rule", name, "--type", "node-affinity",
                   "--resources", sid, "--nodes", nodes,
                   "--affinity", "positive", "--strict", "0")
            print(f"[CREATE] HA node-affinity rule {name}")
        else:
            print(f"[KEEP] HA node-affinity rule {name}")

    final_rules = ha_rules()
    final_resources = ha_resources()
    for sid, name, nodes, _, _ in HA:
        if sid not in final_resources or not resource_matches(final_resources[sid]):
            raise PolicyError(f"HA service policy verification failed: {sid}")
        if name not in final_rules or not rule_matches(final_rules[name], sid, nodes):
            raise PolicyError(f"HA rule verification failed: {name}")
        print(f"[PASS] {sid}: HA rule={name}; failback=0; auto-rebalance=0")
    print("[PASS] CT120 untouched")


def remove_ha():
    check_cluster()
    resources = ha_resources()
    rules = ha_rules()
    validate_owned_rules(rules, resources)

    # Remove rule references first, then only the services that the matching
    # owned rules proved belong to this controller. Empty state is a no-op.
    for sid, name, _, _, _ in HA:
        if name in rules:
            remote("pvesh", "delete", f"/cluster/ha/rules/{name}")
            print(f"[REMOVE] HA rule {name}")
        if sid in resources:
            remote("pvesh", "delete", f"/cluster/ha/resources/{sid}")
            print(f"[REMOVE] HA service {sid} (guest remains)")
    print("[PASS] Removed only owned ct:121/vm:271 HA policy; ct:120 untouched")


def audit():
    check_cluster()
    for name in NODES:
        node = pvesh_json("get", f"/nodes/{name}/config")
        print(f"[READ] {name} startup delay={node.get('startall-onboot-delay', 0)}")
    resources = ha_resources()
    rules = ha_rules()
    for sid, name, _, _, _ in HA:
        print(f"[READ] {sid}: {resources.get(sid, 'not registered')}")
        print(f"[READ] HA rule {name}: {rules.get(name, 'missing')}")
    print(f"[READ] ct:120: {'registered' if 'ct:120' in resources else 'not registered'} (not managed by script)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("audit", "set-startup-delay", "set-guest-startup", "apply-ha", "remove-ha"),
                        required=True)
    args = parser.parse_args()
    try:
        {
            "audit": audit, "set-startup-delay": set_startup_delay,
            "set-guest-startup": set_guest_startup,
            "apply-ha": apply_ha, "remove-ha": remove_ha,
        }[args.mode]()
    except (PolicyError, subprocess.TimeoutExpired) as exc:
        print(f"[STOP] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
