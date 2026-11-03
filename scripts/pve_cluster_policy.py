#!/usr/bin/env python3
"""MeowCloud day-0 PVE node boot staggering and scoped HA policy.

Managed HA resources: CT121 Observer, VM271/272 databases, CT141/142 LBs.
Both database VMs have Patroni-managed PostgreSQL roles that can switch
on failover; Proxmox HA only controls their VM lifecycle, never promotes a DB.
Never manages ct:120 (the separately configured Auto-VM), tunnels,
backends or frontends. Uses PVE's cluster API via trusted SSH.
All mutating operations are explicitly requested by workflow 01 or 02.
"""
import argparse
import json
from pathlib import Path
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
    271: "order=5,up=60",  # DB-01, Patroni role can change (HA-managed)
    272: "order=4,up=60",  # DB-02, Patroni role can change (HA-managed)
}
# Node-affinity descriptors are kept ONLY to validate and remove legacy rules
# created by earlier 01 runs. Do not create prioritized node-affinity rules
# together with resource-affinity: PVE 9 rejects the combination when the
# node rules use multiple priorities ("resources are in node affinity rules
# with multiple priorities"). Terraform chooses initial node placement;
# non-strict negative resource-affinity handles HA spread during failover.
# failback=0 and auto-rebalance=0 prevent automatic return/rebalancing.
HA = (
    ("ct:121", "meow-observer", "node1:2,node2:1,node3:1", "lxc", 121),
    ("vm:271", "meow-db-primary", "node2:2,node1:1,node3:1", "qemu", 271),
    # Preferences only: do not pin a former Replica after Patroni promotes it.
    # DB02 favors node3 so DB01/node2 and Observer/node1 remain separated.
    ("vm:272", "meow-db-02", "node3:2,node1:1,node2:1", "qemu", 272),
    ("ct:141", "meow-lb-01", "node1:2,node2:1,node3:1", "lxc", 141),
    ("ct:142", "meow-lb-02", "node2:2,node1:1,node3:1", "lxc", 142),
)

# PVE 9 resource-affinity: non-strict negative affinity (best-effort spread).
# Hard anti-affinity is intentionally avoided: after losing a host, requiring
# three etcd voters on three separate nodes would block HA guest recovery.
SPREAD_RULES = (
    ("meow-etcd-voter-spread", ("ct:121", "vm:271", "vm:272")),
    ("meow-lb-spread", ("ct:141", "ct:142")),
)
TERRAFORM_STATE_PATH = Path("/opt/meowcloud-iac/state/terraform.tfstate")
GUEST_IDENTITIES = {
    "ct:121": ("proxmox_virtual_environment_container", "observer", 121, "lxc", "observer"),
    "vm:271": ("proxmox_virtual_environment_vm", "db", 271, "qemu", "db-01"),
    "vm:272": ("proxmox_virtual_environment_vm", "db", 272, "qemu", "db-02"),
    "ct:141": ("proxmox_virtual_environment_container", "loadbalancer", 141, "lxc", "lb-master"),
    "ct:142": ("proxmox_virtual_environment_container", "loadbalancer", 142, "lxc", "lb-backup"),
}


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


def spread_rule_matches(rule, members):
    return (
        rule.get("type") == "resource-affinity"
        and rule.get("affinity") == "negative"
        and not disabled(rule.get("strict", 0))
        and not disabled(rule.get("disable", 0))
        and rule_resources(rule) == set(members)
    )


def prove_orphaned_ha_ownership(sids):
    """Before 02 cleans up legacy rule-less HA, prove its Terraform ownership.

    A VMID alone is not proof: require both canonical local Terraform state
    and the actual guest type/name. Never let 02 claim arbitrary HA services.
    """
    try:
        state = json.loads(TERRAFORM_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PolicyError("Cannot verify legacy HA ownership: Terraform state unreadable") from exc

    managed = set()
    for item in state.get("resources", []):
        if item.get("mode") != "managed":
            continue
        for instance in item.get("instances", []):
            vmid = instance.get("attributes", {}).get("vm_id")
            for sid in sids:
                resource_type, resource_name, expected_id, _, _ = GUEST_IDENTITIES[sid]
                if (item.get("type"), item.get("name"), str(vmid)) == (
                    resource_type, resource_name, str(expected_id)
                ):
                    managed.add(sid)

    if managed != set(sids):
        raise PolicyError(
            f"Legacy HA ownership not proved by Terraform state: {sorted(set(sids) - managed)}"
        )

    guests = pvesh_json("get", "/cluster/resources", "--type", "vm")
    for sid in sorted(sids):
        _, _, vmid, guest_type, guest_name = GUEST_IDENTITIES[sid]
        matches = [guest for guest in guests
                   if str(guest.get("vmid")) == str(vmid)
                   and guest.get("type") == guest_type
                   and guest.get("name") == guest_name]
        if len(matches) != 1:
            raise PolicyError(f"Legacy HA guest identity mismatch: {sid}; refusing to remove")
        print(f"[PASS] Legacy HA ownership verified: {sid} ({guest_name})")


def covered_by_spread(rules, sid):
    return any(name in rules and sid in members for name, members in SPREAD_RULES)


def unruled_owned_resources(rules, resources):
    # Legacy partial 01 may have HA services but no completed spread rules.
    # The caller must verify Terraform ownership before mutating these.
    return {
        sid for sid, node_rule, _, _, _ in HA
        if sid in resources and node_rule not in rules
        and not covered_by_spread(rules, sid)
    }


def resource_matches(resource):
    # Missing PVE fields have documented default=1 (unsafe for this policy).
    # Require explicit booleans off, including when evaluating existing HA.
    return (
        str(resource.get("failback", 1)).lower() in ("0", "false")
        and str(resource.get("auto-rebalance", 1)).lower() in ("0", "false")
        and resource.get("state", "started") == "started"
        and resource.get("group") in (None, "")
    )


def validate_owned_rules(rules, resources, allow_unruled=()):
    """Fail closed on external HA policies; allow verified legacy gaps for 02."""
    allowed_unruled = set(allow_unruled)
    known_services = {item[0] for item in HA}
    if not allowed_unruled <= known_services:
        raise PolicyError("Cannot relax HA ownership outside MeowCloud services")
    for name, members in SPREAD_RULES:
        current = rules.get(name)
        if current is not None and not spread_rule_matches(current, members):
            raise PolicyError(f"Conflicting existing HA spread rule {name}: {current}")
    for sid, name, nodes, _, _ in HA:
        current_rule = rules.get(name)
        if current_rule is not None and not rule_matches(current_rule, sid, nodes):
            raise PolicyError(f"Conflicting existing HA rule {name}: {current_rule}")
        permitted_spread = {
            spread_name for spread_name, members in SPREAD_RULES if sid in members
        }
        for other_name, rule in rules.items():
            if (other_name != name and other_name not in permitted_spread
                    and sid in rule_resources(rule)):
                raise PolicyError(f"External HA rule {other_name} already targets {sid}")
        current_resource = resources.get(sid)
        if current_resource is not None:
            if not resource_matches(current_resource):
                raise PolicyError(
                    f"HA service {sid} has incompatible state or failback/rebalance policy"
                )
            if (current_rule is None and not covered_by_spread(rules, sid)
                    and sid not in allowed_unruled):
                raise PolicyError(
                    f"HA service {sid} exists without owned node/spread rule; "
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

    # The previous workflow 01 may have stopped after creating all node
    # affinity rules but before creating resource anti-affinity rules. Validate
    # all ownership/policy before modifying anything. Also support resume
    # after a partially deleted legacy node rule, with independent proof.
    unruled = unruled_owned_resources(rules, resources)
    validate_owned_rules(rules, resources, allow_unruled=unruled)
    if unruled:
        prove_orphaned_ha_ownership(unruled)

    # HA services are independent of placement rules. Keep existing services,
    # create only missing ones, and NEVER touch CT120.
    for sid, _, _, _, _ in HA:
        if sid not in resources:
            remote("pvesh", "create", "/cluster/ha/resources",
                   "--sid", sid, "--state", "started",
                   "--failback", "0", "--auto-rebalance", "0",
                   "--max_restart", "1", "--max_relocate", "1")
            print(f"[CREATE] HA service {sid}")
        else:
            print(f"[KEEP] HA service {sid}")

    # Proxmox 9 disallows resource spread when a member also belongs to a
    # node-affinity rule with multiple priorities. Migrate EXACTLY the five
    # validated MeowCloud legacy node rules, not foreign rules or services.
    # This operation does not stop/reboot a guest.
    for sid, name, _, _, _ in HA:
        if name in rules:
            remote("pvesh", "delete", f"/cluster/ha/rules/{name}")
            print(f"[MIGRATE] Removed conflicting owned node rule {name} ({sid})")

    # Soft negative resource-affinity works with the initial node placement
    # from Terraform. Non-strict so 2 surviving nodes can host 3 etcd voters.
    for name, members in SPREAD_RULES:
        if name not in rules:
            remote("pvesh", "create", "/cluster/ha/rules",
                   "--rule", name, "--type", "resource-affinity",
                   "--resources", ",".join(members), "--affinity", "negative",
                   "--strict", "0")
            print(f"[CREATE] Non-strict HA resource anti-affinity {name}")
        else:
            print(f"[KEEP] Non-strict HA resource anti-affinity {name}")

    final_rules = ha_rules()
    final_resources = ha_resources()
    validate_owned_rules(final_rules, final_resources)
    for sid, name, _, _, _ in HA:
        if sid not in final_resources or not resource_matches(final_resources[sid]):
            raise PolicyError(f"HA service policy verification failed: {sid}")
        if name in final_rules:
            raise PolicyError(f"Conflicting legacy node-affinity survived: {name}")
        if not covered_by_spread(final_rules, sid):
            raise PolicyError(f"No owned spread rule covers HA service: {sid}")
        print(f"[PASS] {sid}: HA managed; failback=0; auto-rebalance=0")
    for name, members in SPREAD_RULES:
        if name not in final_rules or not spread_rule_matches(final_rules[name], members):
            raise PolicyError(f"HA spread rule verification failed: {name}")
        print(f"[PASS] Non-strict anti-affinity {name}: {','.join(members)}")
    print("[PASS] CT120 untouched")


def remove_ha(allow_owned_missing_rule=False):
    check_cluster()
    resources = ha_resources()
    rules = ha_rules()
    legacy = unruled_owned_resources(rules, resources)
    if allow_owned_missing_rule and legacy:
        prove_orphaned_ha_ownership(legacy)
    validate_owned_rules(rules, resources,
                         allow_unruled=legacy if allow_owned_missing_rule else ())

    # Remove cross-service rules first, then node rules and registered services.
    for name, _ in SPREAD_RULES:
        if name in rules:
            remote("pvesh", "delete", f"/cluster/ha/rules/{name}")
            print(f"[REMOVE] HA spread rule {name}")
    for sid, name, _, _, _ in HA:
        if name in rules:
            remote("pvesh", "delete", f"/cluster/ha/rules/{name}")
            print(f"[REMOVE] HA rule {name}")
        if sid in resources:
            remote("pvesh", "delete", f"/cluster/ha/resources/{sid}")
            print(f"[REMOVE] HA service {sid} (guest remains)")
    print("[PASS] Removed only owned MeowCloud Observer/DB/LB HA policy; ct:120 untouched")


def audit():
    check_cluster()
    for name in NODES:
        node = pvesh_json("get", f"/nodes/{name}/config")
        print(f"[READ] {name} startup delay={node.get('startall-onboot-delay', 0)}")
    resources = ha_resources()
    rules = ha_rules()
    for sid, name, _, _, _ in HA:
        print(f"[READ] {sid}: {resources.get(sid, 'not registered')}")
        print(f"[READ] legacy node-affinity rule {name}: {rules.get(name, 'absent (expected)')}")
    for name, _ in SPREAD_RULES:
        print(f"[READ] HA spread {name}: {rules.get(name, 'missing')}")
    print(f"[READ] ct:120: {'registered' if 'ct:120' in resources else 'not registered'} (not managed by script)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("audit", "set-startup-delay", "set-guest-startup", "apply-ha", "remove-ha"),
                        required=True)
    parser.add_argument(
        "--allow-owned-missing-rule", action="store_true",
        help="02 only: reconcile missing HA rules after Terraform state and live identity checks",
    )
    args = parser.parse_args()
    try:
        if args.allow_owned_missing_rule and args.mode != "remove-ha":
            raise PolicyError("--allow-owned-missing-rule is restricted to destroy")
        if args.mode == "remove-ha":
            remove_ha(allow_owned_missing_rule=args.allow_owned_missing_rule)
        else:
            {
                "audit": audit, "set-startup-delay": set_startup_delay,
                "set-guest-startup": set_guest_startup, "apply-ha": apply_ha,
            }[args.mode]()
    except (PolicyError, subprocess.TimeoutExpired) as exc:
        print(f"[STOP] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
