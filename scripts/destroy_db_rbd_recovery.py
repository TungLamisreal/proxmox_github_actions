#!/usr/bin/env python3
"""Conservative recovery for interrupted MeowCloud DB VM destruction.

Workflow 02 runs a read-only RBD preflight, then Terraform destroy.
Only if Terraform fails may --repair-missing-references detach a missing
disk reference from the confirmed, stopped, non-HA DB guests 271/272.
Terraform remains responsible for removing the actual VM configurations.

NEVER delete real RBD images, VM configs, or Terraform state here.
An absent RBD image is not proof of ownership: independently verify local
Terraform state, the live VM type/name/tags, and a successful RBD listing.
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
from pathlib import Path

PVE_HOST = "192.168.150.49"
STATE_PATH = Path("/opt/meowcloud-iac/state/terraform.tfstate")
DBS = {
    271: {"name": "db-01", "state_index": 0},
    272: {"name": "db-02", "state_index": 1},
}
POOLS = ("general_pool", "db_pool")
DISKS = {
    271: {
        "scsi0": "general_pool:vm-271-disk-0",
        "scsi1": "db_pool:vm-271-disk-0",
        "ide2": "general_pool:vm-271-cloudinit",
    },
    272: {
        "scsi0": "general_pool:vm-272-disk-0",
        "scsi1": "db_pool:vm-272-disk-0",
        "ide2": "general_pool:vm-272-cloudinit",
    },
}
# Every existing disk entry must be expected: no unknown volumes get detached.
DRIVE_KEY = re.compile(r"^(?:scsi\d+|virtio\d+|sata\d+|ide\d+|unused\d+|efidisk0|tpmstate0)$")


class RecoveryError(RuntimeError):
    pass


def remote(*args):
    command = shlex.join([str(x) for x in args])
    call = subprocess.run(
        ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
         "-o", "StrictHostKeyChecking=accept-new", "root@" + PVE_HOST, command],
        capture_output=True, text=True, timeout=90, check=False,
    )
    if call.returncode != 0:
        raise RecoveryError(
            f"PVE command failed (rc={call.returncode}): {command}\n{call.stderr[-1200:]}"
        )
    return call.stdout.strip()


def remote_json(*args):
    try:
        return json.loads(remote(*args))
    except ValueError as exc:
        raise RecoveryError("Unexpected non-JSON PVE response") from exc


def preflight():
    """Refuse destroy when either RBD pool is inaccessible on a DB node."""
    status = remote_json("pvesh", "get", "/cluster/status", "--output-format", "json")
    if not any(row.get("type") == "cluster" and row.get("quorate") == 1
               for row in status):
        raise RecoveryError("Proxmox cluster has no confirmed quorum")

    for node in ("node2", "node3"):
        for pool in POOLS:
            remote_json("pvesh", "get", f"/nodes/{node}/storage/{pool}/content",
                        "--output-format", "json")
            print(f"[PASS] {node}/{pool}: PVE can list storage content")
    images = rbd_images()
    print("[PASS] RBD listing works for both pools:",
          {p: len(items) for p, items in images.items()})


def rbd_images():
    images = {}
    for pool in POOLS:
        data = remote_json("rbd", "ls", "-p", pool, "--format", "json")
        if not isinstance(data, list) or any(not isinstance(v, str) for v in data):
            raise RecoveryError(f"Unexpected RBD image listing for {pool}")
        images[pool] = set(data)
    return images


def check_state_ownership():
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryError("Canonical Terraform state cannot prove DB ownership") from exc

    present = set()
    for resource in state.get("resources", []):
        if (resource.get("mode"), resource.get("type"), resource.get("name")) != (
            "managed", "proxmox_virtual_environment_vm", "db"
        ):
            continue
        for instance in resource.get("instances", []):
            attrs = instance.get("attributes") or {}
            for vmid, identity in DBS.items():
                if (str(attrs.get("vm_id")) == str(vmid)
                        and attrs.get("name") == identity["name"]):
                    present.add(vmid)
    return present


def proposed_repairs():
    """Validate all candidates before any mutation; return detach actions."""
    owned = check_state_ownership()
    vms = remote_json("pvesh", "get", "/cluster/resources", "--type", "vm",
                      "--output-format", "json")
    ha = remote_json("pvesh", "get", "/cluster/ha/resources",
                     "--output-format", "json")
    if not isinstance(vms, list) or not isinstance(ha, list):
        raise RecoveryError("Invalid PVE resources response")
    managed = {entry.get("sid") for entry in ha}
    images = rbd_images()
    repairs = []
    for vmid, identity in DBS.items():
        matches = [v for v in vms if str(v.get("vmid")) == str(vmid)]
        if not matches:
            continue  # Successfully deleted guests need no repair.
        if len(matches) != 1 or matches[0].get("type") != "qemu":
            raise RecoveryError(f"Unexpected guest identity for VMID {vmid}")
        guest = matches[0]
        if vmid not in owned or guest.get("name") != identity["name"]:
            raise RecoveryError(f"VM {vmid}: Terraform state/live guest name mismatch")
        if guest.get("status") != "stopped":
            raise RecoveryError(f"VM {vmid}: not stopped; refuse disk detach")
        if f"vm:{vmid}" in managed:
            raise RecoveryError(f"VM {vmid}: still HA-managed")
        node = guest.get("node")
        if node not in ("node1", "node2", "node3"):
            raise RecoveryError(f"VM {vmid}: unexpected PVE node {node!r}")
        conf = remote_json("pvesh", "get", f"/nodes/{node}/qemu/{vmid}/config",
                           "--output-format", "json")
        if conf.get("lock"):
            raise RecoveryError(f"VM {vmid}: has lock {conf['lock']}")
        if conf.get("name") != identity["name"]:
            raise RecoveryError(f"VM {vmid}: config name mismatch")
        tags = set(str(conf.get("tags", "")).split(";"))
        if not {"database", "meowcloud"} <= tags:
            raise RecoveryError(f"VM {vmid}: ownership tags missing")
        unknown = [key for key in conf if DRIVE_KEY.fullmatch(key)
                   and key not in DISKS[vmid]]
        if unknown:
            raise RecoveryError(f"VM {vmid}: unknown disk keys {unknown}")
        missing = []
        for key, expected in DISKS[vmid].items():
            if key not in conf:
                continue
            # Disk references are intentionally checked strictly; never detach
            # a live image, another VM's volume or an unexpected datastore.
            volume = str(conf[key]).split(",", 1)[0]
            if volume != expected:
                raise RecoveryError(f"VM {vmid}/{key}: unexpected volume {volume}")
            pool, image = volume.split(":", 1)
            if image not in images[pool]:
                missing.append(key)
        if missing:
            repairs.append((node, vmid, tuple(missing)))
    return repairs


def repair_missing_references():
    repairs = proposed_repairs()
    if not repairs:
        raise RecoveryError(
            "Terraform failed but no verified partial DB RBD deletion found; "
            "refusing an automatic retry"
        )
    for node, vmid, keys in repairs:
        print(f"[RECOVER] VM {vmid}: detaching only missing volumes {','.join(keys)}",
              flush=True)
        remote("pvesh", "set", f"/nodes/{node}/qemu/{vmid}/config",
               "--delete", ",".join(keys))
        actual = remote_json("pvesh", "get", f"/nodes/{node}/qemu/{vmid}/config",
                             "--output-format", "json")
        if any(key in actual for key in keys):
            raise RecoveryError(f"VM {vmid}: detached refs not persisted")
    print("[PASS] Missing RBD references detached; retry Terraform destroy once")



def is_meowcloud_workload_id(vmid):
    return (vmid in {121, 131, 132, 133, 141, 142, 271, 272}
            or 151 <= vmid <= 169 or 171 <= vmid <= 189)


def verify_clean():
    """Postflight: do not call destroy green when guests or RBD orphan remain."""
    resources = remote_json("pvesh", "get", "/cluster/resources", "--type", "vm",
                            "--output-format", "json")
    leftovers = []
    for guest in resources:
        vmid = guest.get("vmid")
        try:
            number = int(vmid)
        except (TypeError, ValueError):
            continue
        if is_meowcloud_workload_id(number):
            leftovers.append(f"{guest.get('type')}:{number} ({guest.get('name')})")

    images = rbd_images()
    orphans = []
    for pool, disks in images.items():
        for disk in disks:
            match = re.match(r"^vm-(\d+)-", disk)
            if match and is_meowcloud_workload_id(int(match.group(1))):
                orphans.append(f"{pool}:{disk}")

    if leftovers or orphans:
        raise RecoveryError(
            "Residual MeowCloud guests or RBD images after Terraform: "
            f"guests={sorted(leftovers)} disks={sorted(orphans)}; "
            "do not delete volumes blindly"
        )
    print("[PASS] MeowCloud guest IDs and RBD volumes absent; templates untouched")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("preflight", "repair-missing-references", "verify-clean"),
                        required=True)
    args = parser.parse_args()
    try:
        if args.mode == "preflight":
            preflight()
        elif args.mode == "repair-missing-references":
            repair_missing_references()
        else:
            verify_clean()
    except (RecoveryError, subprocess.TimeoutExpired) as exc:
        print(f"[STOP] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
