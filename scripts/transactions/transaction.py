#!/usr/bin/env python3
"""MeowCloud compensating transaction v1 (standard library only).

This tool is intentionally fail-closed. It never deletes a Proxmox resource directly;
rollback is performed only through a fresh Terraform plan that passes a strict allowlist.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import shlex
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

VERSION = "1.0.3"
TERMINAL = {"COMMITTED", "ROLLED_BACK"}
OPEN_STATES = {"PREPARED", "PROVISIONING", "PROVISIONED", "CONFIGURING", "VALIDATING", "ROLLBACK_PENDING", "NEEDS_REPAIR"}
JOURNAL_ROOT_DEFAULT = "/opt/meowcloud-iac/state/transactions"
STATE_FILE_DEFAULT = "/opt/meowcloud-iac/state/autoscale.tfvars.json"
PROTECTED_VMIDS = {120, 9000, 9001, 9002, 9003}
LOCAL_INVENTORY_ADDRESS = "local_file.ansible_inventory"

BACKEND_HELPERS = {
    "proxmox_virtual_environment_firewall_options.backend_fw",
    "proxmox_virtual_environment_firewall_rules.backend_rules",
    "proxmox_virtual_environment_firewall_ipset.be_ipfilter_net0",
}
FRONTEND_HELPERS = {
    "proxmox_virtual_environment_firewall_options.frontend_fw",
    "proxmox_virtual_environment_firewall_rules.frontend_rules",
    "proxmox_virtual_environment_firewall_ipset.fe_ipfilter_net0",
}
MAIN_RESOURCE_RE = re.compile(r"^proxmox_virtual_environment_container\.(backend|frontend)\[(\d+)\]$")
HELPER_RESOURCE_RE = re.compile(
    r"^proxmox_virtual_environment_(firewall_options|firewall_rules|firewall_ipset)\."
    r"(backend_fw|backend_rules|be_ipfilter_net0|frontend_fw|frontend_rules|fe_ipfilter_net0)\[(\d+)\]$"
)
SAFE_ID_RE = re.compile(r"^txn-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{8}$")


class TransactionError(RuntimeError):
    """Expected fail-closed transaction error."""


class NeedsRepair(TransactionError):
    """Ownership or plan safety is ambiguous; automatic destruction is forbidden."""


class RollbackPending(TransactionError):
    """A retryable prerequisite failed; keep the transaction open and locked."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_json_file(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise TransactionError(f"Không đọc được JSON {path}: {e}") from e


def capacity_shape(value: Any, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TransactionError(f"{where}: capacity phải là JSON object.")
    for key in ("backend_count", "frontend_count"):
        if not isinstance(value.get(key), int) or isinstance(value.get(key), bool) or value[key] < 0:
            raise TransactionError(f"{where}: {key} không phải số nguyên không âm.")
    for key in ("backend_node_map", "frontend_node_map"):
        if not isinstance(value.get(key), dict):
            raise TransactionError(f"{where}: {key} phải là object.")
        for k, v in value[key].items():
            if not isinstance(k, str) or not isinstance(v, str) or not v:
                raise TransactionError(f"{where}: mục {key} không hợp lệ.")
    # Keep additional non-secret keys if a future schema adds them, but return a copy.
    return dict(value)


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.tmp-", dir=str(path.parent))
    tmp = Path(temp_name)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        try:
            os.chmod(path, mode)
        except OSError:
            pass
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, value: Any, mode: int = 0o600) -> None:
    data = (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    atomic_write_bytes(path, data, mode)


def safe_operation_id(value: str) -> str:
    if not SAFE_ID_RE.fullmatch(value or ""):
        raise TransactionError("Transaction ID không hợp lệ.")
    return value


def txn_dir(root: Path, operation_id: str) -> Path:
    return root / safe_operation_id(operation_id)


def read_journal(root: Path, operation_id: str) -> tuple[Path, dict[str, Any]]:
    directory = txn_dir(root, operation_id)
    path = directory / "journal.json"
    if not path.is_file() or path.is_symlink():
        raise TransactionError(f"Không tìm thấy journal hợp lệ cho {operation_id}.")
    journal = parse_json_file(path)
    if not isinstance(journal, dict) or journal.get("operation_id") != operation_id:
        raise TransactionError("Journal không khớp transaction ID.")
    return directory, journal


def write_journal(directory: Path, journal: dict[str, Any]) -> None:
    journal["updated_at"] = utc_now()
    atomic_write_json(directory / "journal.json", journal, 0o600)


def redact(text: str) -> str:
    for name in ("TF_VAR_proxmox_api_token_secret", "TF_VAR_proxmox_api_token_id"):
        value = os.getenv(name)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def append_github_output(key: str, value: str) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def append_github_env(key: str, value: str) -> None:
    path = os.getenv("GITHUB_ENV")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"{key}={value}\n")


def load_baseline(directory: Path) -> dict[str, Any]:
    value = capacity_shape(parse_json_file(directory / "baseline.json"), "baseline.json")
    return value


def assert_transaction_open(journal: dict[str, Any]) -> None:
    if journal.get("status") in TERMINAL:
        raise TransactionError(f"Transaction {journal.get('operation_id')} đã terminal: {journal.get('status')}.")
    if journal.get("status") == "NEEDS_REPAIR":
        raise NeedsRepair("Transaction đã ở NEEDS_REPAIR; không tự chạy compensation lần nữa khi chưa xử lý thủ công.")


def release_lock(root: Path, operation_id: str) -> None:
    lock = root / "active.lock"
    try:
        current = lock.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return
    if current != operation_id:
        raise TransactionError("active.lock không thuộc transaction hiện tại; giữ nguyên khóa để kiểm tra.")
    lock.unlink()
    try:
        fd = os.open(str(root), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def acquire_lock(root: Path, operation_id: str) -> None:
    if root.is_symlink():
        raise TransactionError(f"Journal root là symlink; từ chối lấy lock: {root}")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if root.is_symlink() or not root.is_dir():
        raise TransactionError(f"Journal root không phải thư mục thường: {root}")
    os.chmod(root, 0o700)
    lock = root / "active.lock"
    # A leftover lock can be removed only when its referenced journal is terminal.
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(operation_id + "\n")
                f.flush()
                os.fsync(f.fileno())
            return
        except FileExistsError:
            try:
                owner = lock.read_text(encoding="utf-8").strip()
            except Exception as e:
                raise TransactionError(f"Không đọc được active.lock; fail-closed: {e}") from e
            try:
                _, current = read_journal(root, owner)
            except Exception as e:
                raise TransactionError(f"Đã tồn tại active.lock không rõ ownership ({owner}); cần kiểm tra thủ công. {e}") from e
            if current.get("status") not in TERMINAL:
                raise TransactionError(
                    f"Đã có transaction chưa terminal {owner} (status={current.get('status')}); chặn transaction mới."
                )
            # Terminal journal proves a stale lock can be released.
            try:
                if lock.read_text(encoding="utf-8").strip() == owner:
                    lock.unlink()
            except FileNotFoundError:
                pass
    raise TransactionError("Không thể lấy transaction lock sau khi kiểm tra stale lock.")


def scan_open_transactions(root: Path, excluding: str | None = None) -> list[str]:
    """Fail closed on open journals, unreadable journals, and txn dirs missing a journal."""
    opened: list[str] = []
    if root.is_symlink():
        return ["journal-root:symlink"]
    if not root.exists():
        return opened
    if not root.is_dir():
        return ["journal-root:not-a-regular-directory"]
    for entry in sorted(root.glob("txn-*")):
        if entry.name == excluding:
            continue
        if entry.is_symlink() or not entry.is_dir():
            opened.append(f"{entry.name}:transaction-path-not-a-directory")
            continue
        jpath = entry / "journal.json"
        if not jpath.is_file() or jpath.is_symlink():
            opened.append(f"{entry.name}:journal-missing-or-invalid")
            continue
        try:
            j = parse_json_file(jpath)
        except Exception:
            opened.append(f"{entry.name}:journal-unreadable")
            continue
        if not isinstance(j, dict) or j.get("operation_id") != entry.name:
            opened.append(f"{entry.name}:journal-identity-mismatch")
            continue
        if j.get("status") not in TERMINAL:
            opened.append(f"{entry.name}:{j.get('status', 'unknown')}")
    return opened


def guard(args: argparse.Namespace) -> int:
    """Read-only guard for provision/destroy workflows sharing the Terraform state."""
    root = Path(args.journal_root)
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise NeedsRepair(f"Transaction journal root không phải thư mục bình thường: {root}")

    lock = root / "active.lock"
    if lock.exists() or lock.is_symlink():
        if lock.is_symlink() or not lock.is_file():
            raise NeedsRepair(f"active.lock không phải file thường: {lock}")
        try:
            owner = lock.read_text(encoding="utf-8").strip()
        except Exception as e:
            raise NeedsRepair(f"Không đọc được active.lock: {e}") from e
        if not owner:
            raise NeedsRepair("active.lock rỗng; chặn workflow cho đến khi được đối soát thủ công.")
        try:
            _, owner_journal = read_journal(root, owner)
        except Exception as e:
            raise NeedsRepair(f"active.lock không trỏ tới journal có thể xác minh ({owner!r}): {e}") from e
        if owner_journal.get("status") not in TERMINAL:
            raise TransactionError(
                f"Workflow {args.workflow} bị chặn: transaction {owner} chưa terminal "
                f"(status={owner_journal.get('status', 'unknown')}, stage={owner_journal.get('stage', 'unknown')})."
            )
        # A terminal journal makes this a stale lock. Guard is read-only; begin() reconciles it.

    opened = scan_open_transactions(root)
    if opened:
        raise TransactionError(
            f"Workflow {args.workflow} bị chặn: phát hiện transaction chưa terminal/không hợp lệ: "
            + ", ".join(opened)
        )
    print(f"[transaction] GUARD PASS: không có transaction mở; workflow={args.workflow}; journal_root={root}")
    return 0


def effective_adds(scale_target: str, add_be: int, add_fe: int) -> tuple[int, int]:
    if scale_target not in {"backend", "frontend", "both"}:
        raise TransactionError(f"scale_target không hợp lệ: {scale_target}")
    if scale_target == "backend":
        return add_be, 0
    if scale_target == "frontend":
        return 0, add_fe
    return add_be, add_fe


def begin(args: argparse.Namespace) -> int:
    state_path = Path(args.state_file)
    backup_path = Path(args.backup_file)
    current = capacity_shape(parse_json_file(state_path), "autoscale state")
    backup = capacity_shape(parse_json_file(backup_path), "autoscale backup")
    if current != backup:
        raise TransactionError("Capacity state đã thay đổi sau khi tạo backup; từ chối mở transaction trên baseline không nhất quán.")

    try:
        add_be_raw = int(args.add_be)
        add_fe_raw = int(args.add_fe)
    except ValueError as e:
        raise TransactionError("Số lượng scale phải là số nguyên.") from e
    if add_be_raw < 0 or add_fe_raw < 0:
        raise TransactionError("Số lượng scale không được âm.")
    add_be, add_fe = effective_adds(args.scale_target, add_be_raw, add_fe_raw)
    if add_be == 0 and add_fe == 0:
        append_github_output("transaction_id", "")
        append_github_output("noop", "true")
        print("[transaction] No-op: không mở journal hoặc khóa.")
        return 0

    root = Path(args.journal_root)
    now = dt.datetime.now(dt.timezone.utc)
    operation_id = "txn-{}-{}".format(now.strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])
    acquire_lock(root, operation_id)
    directory = txn_dir(root, operation_id)
    try:
        opened = scan_open_transactions(root, excluding=operation_id)
        if opened:
            raise TransactionError("Có transaction chưa terminal; chặn transaction mới: " + ", ".join(opened))
        directory.mkdir(mode=0o700)
        os.chmod(directory, 0o700)
        atomic_write_json(directory / "baseline.json", backup, 0o600)
        source_commit = ""
        try:
            root_candidate = Path(args.repo_root).resolve()
            cp = subprocess.run(["git", "-C", str(root_candidate), "rev-parse", "--short", "HEAD"], text=True, capture_output=True, timeout=5)
            if cp.returncode == 0:
                source_commit = cp.stdout.strip()
        except Exception:
            pass
        journal: dict[str, Any] = {
            "schema_version": 1,
            "tool_version": VERSION,
            "operation_id": operation_id,
            "created_at": utc_now(),
            "updated_at": utc_now(),
            "status": "PREPARED",
            "stage": "PREPARED",
            "request": {
                "scale_target": args.scale_target,
                "input_add_backend": add_be_raw,
                "input_add_frontend": add_fe_raw,
                "effective_add_backend": add_be,
                "effective_add_frontend": add_fe,
            },
            "baseline_capacity": backup,
            "target_capacity": None,
            "planned_create_addresses": [],
            "expected_resources": [],
            "approved_delete_addresses": [],
            "apply_attempted": False,
            "rollback_status": "NOT_STARTED",
            "last_error": None,
            "source_commit": source_commit,
            "safety": {
                "vmids_absent_before_apply": False,
                "inventory_restored_before_delete": False,
                "terraform_rollback_plan_validated": False,
            },
        }
        write_journal(directory, journal)
    except Exception:
        # Remove the lock only if no nonterminal journal was successfully created.
        try:
            if not (directory / "journal.json").exists():
                directory.rmdir()
            release_lock(root, operation_id)
        except Exception:
            pass
        raise

    append_github_output("transaction_id", operation_id)
    append_github_output("noop", "false")
    append_github_env("MEOW_TRANSACTION_ID", operation_id)
    print(f"[transaction] Opened {operation_id}; baseline BE/FE={backup['backend_count']}/{backup['frontend_count']}; journal={directory}")
    return 0



def record_target(args: argparse.Namespace) -> int:
    root = Path(args.journal_root)
    directory, journal = read_journal(root, args.transaction_id)
    assert_transaction_open(journal)
    if journal.get("stage") != "PREPARED" or journal.get("apply_attempted"):
        raise TransactionError("record-target chỉ được chạy trước Terraform apply.")
    baseline = load_baseline(directory)
    observed = capacity_shape(parse_json_file(Path(args.state_file)), "selector candidate capacity")
    # Persist the observed candidate before validating it. If validation fails, the no-apply
    # compensation can prove which exact file content belongs to this operation before restore.
    journal["candidate_capacity"] = observed
    journal["target_capacity"] = observed
    old_be = int(baseline["backend_count"])
    old_fe = int(baseline["frontend_count"])
    expected_be = old_be + int(journal["request"]["effective_add_backend"])
    expected_fe = old_fe + int(journal["request"]["effective_add_frontend"])
    valid = observed["backend_count"] == expected_be and observed["frontend_count"] == expected_fe
    journal["target_capacity_valid"] = valid
    if not valid:
        journal["last_error"] = (
            f"Selector candidate count mismatch: expected BE/FE={expected_be}/{expected_fe}, "
            f"observed={observed['backend_count']}/{observed['frontend_count']}."
        )
    write_journal(directory, journal)
    if not valid:
        raise NeedsRepair(journal["last_error"] + " Đã ghi candidate vào journal; no-apply compensation chỉ được restore nếu file vẫn khớp đúng snapshot này.")
    print(f"[transaction] Candidate capacity journaled: BE/FE={observed['backend_count']}/{observed['frontend_count']}.")
    return 0

def expected_compute(kind: str, index: int) -> dict[str, Any]:
    if kind == "backend":
        return {"kind": kind, "index": index, "address": f"proxmox_virtual_environment_container.backend[{index}]",
                "vmid": 151 + index, "hostname": f"be-{index + 1}", "ip": f"192.168.150.{101 + index}"}
    return {"kind": kind, "index": index, "address": f"proxmox_virtual_environment_container.frontend[{index}]",
            "vmid": 171 + index, "hostname": f"fe-{index + 1}", "ip": f"192.168.150.{171 + index}"}


def expected_index_addresses(kind: str, index: int) -> set[str]:
    if kind == "backend":
        names = ["proxmox_virtual_environment_container.backend", *sorted(BACKEND_HELPERS)]
    else:
        names = ["proxmox_virtual_environment_container.frontend", *sorted(FRONTEND_HELPERS)]
    # A direct main address is already fully qualified; helper names are fully qualified too.
    return {f"{base}[{index}]" for base in names}


def expected_create_addresses(old_be: int, new_be: int, old_fe: int, new_fe: int) -> set[str]:
    result: set[str] = set()
    for idx in range(old_be, new_be):
        result |= expected_index_addresses("backend", idx)
    for idx in range(old_fe, new_fe):
        result |= expected_index_addresses("frontend", idx)
    return result


def resource_changes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    changes = plan.get("resource_changes")
    if not isinstance(changes, list):
        raise TransactionError("Terraform plan JSON thiếu resource_changes.")
    return changes


def find_planned_resource(plan: dict[str, Any], address: str) -> dict[str, Any] | None:
    def walk(module: dict[str, Any]) -> dict[str, Any] | None:
        for r in module.get("resources", []) or []:
            if r.get("address") == address:
                return r
        for child in module.get("child_modules", []) or []:
            found = walk(child)
            if found is not None:
                return found
        return None
    return walk(plan.get("planned_values", {}).get("root_module", {}) or {})


def get_inventory_content_from_plan(plan: dict[str, Any], address: str = LOCAL_INVENTORY_ADDRESS) -> str:
    for rc in resource_changes(plan):
        if rc.get("address") == address:
            after = (rc.get("change") or {}).get("after") or {}
            if isinstance(after.get("content"), str):
                return after["content"]
    planned = find_planned_resource(plan, address)
    if planned:
        values = planned.get("values") or {}
        if isinstance(values.get("content"), str):
            return values["content"]
    raise TransactionError("Không lấy được content dự kiến của local_file.ansible_inventory từ rollback plan.")


def inventory_group_hosts(content: str, group: str) -> set[str]:
    current_group: str | None = None
    hosts: set[str] = set()
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current_group = line[1:-1].split(":", 1)[0]
            continue
        if current_group == group and "=" in line:
            hosts.add(line.split()[0])
    return hosts


def expected_host_names(kind: str, count: int) -> set[str]:
    prefix = "backend" if kind == "backend" else "frontend"
    return {f"{prefix}-0{i + 1}" for i in range(count)}


def validate_inventory_content(content: str, be_count: int, fe_count: int) -> None:
    be_hosts = inventory_group_hosts(content, "backends")
    fe_hosts = inventory_group_hosts(content, "frontends")
    expected_be = expected_host_names("backend", be_count)
    expected_fe = expected_host_names("frontend", fe_count)
    if be_hosts != expected_be:
        raise NeedsRepair(f"Inventory plan backend hosts sai baseline: expected={sorted(expected_be)}, actual={sorted(be_hosts)}")
    if fe_hosts != expected_fe:
        raise NeedsRepair(f"Inventory plan frontend hosts sai baseline: expected={sorted(expected_fe)}, actual={sorted(fe_hosts)}")


def pve_api_get(api_url: str, path: str, timeout: int = 10) -> Any:
    token_id = os.getenv("TF_VAR_proxmox_api_token_id", "")
    token_secret = os.getenv("TF_VAR_proxmox_api_token_secret", "")
    if not token_id or not token_secret:
        raise RollbackPending("Thiếu Proxmox API token trong environment; không thể xác minh resource ownership.")
    base = api_url.rstrip("/")
    if not base.endswith("/api2/json"):
        base += "/api2/json"
    url = base + path
    req = urllib.request.Request(url, headers={"Authorization": f"PVEAPIToken={token_id}={token_secret}"})
    context = ssl._create_unverified_context()  # Match the existing Terraform provider's insecure=true setting.
    try:
        with urllib.request.urlopen(req, context=context, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise RollbackPending(f"Proxmox API read failed ({path}): {redact(str(e))}") from e
    if not isinstance(payload, dict) or "data" not in payload:
        raise RollbackPending(f"Proxmox API trả response không hợp lệ tại {path}.")
    return payload["data"]


def pve_resources(api_url: str) -> dict[int, dict[str, Any]]:
    data = pve_api_get(api_url, "/cluster/resources?type=vm")
    if not isinstance(data, list):
        raise RollbackPending("Proxmox /cluster/resources không trả list.")
    result: dict[int, dict[str, Any]] = {}
    for item in data:
        try:
            result[int(item.get("vmid"))] = item
        except (TypeError, ValueError):
            continue
    return result


def live_config(api_url: str, item: dict[str, Any]) -> dict[str, Any]:
    node = item.get("node")
    vmid = item.get("vmid")
    if not isinstance(node, str) or not node or vmid is None:
        raise RollbackPending("Live Proxmox resource thiếu node/vmid.")
    path = f"/nodes/{urllib.parse.quote(node, safe='')}/lxc/{int(vmid)}/config"
    data = pve_api_get(api_url, path)
    if not isinstance(data, dict):
        raise RollbackPending(f"Proxmox không trả LXC config object cho VMID {vmid}.")
    return data


def verify_live_identity(api_url: str, resource: dict[str, Any], item: dict[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    vmid = int(resource["vmid"])
    if int(item.get("vmid", -1)) != vmid:
        raise NeedsRepair(f"Live VMID mismatch cho {resource['address']}.")
    if item.get("type") not in {"lxc", "container"}:
        raise NeedsRepair(f"VMID {vmid} tồn tại nhưng type không phải LXC; từ chối tự xóa.")
    config = live_config(api_url, item)
    if config.get("hostname") != resource["hostname"]:
        raise NeedsRepair(f"VMID {vmid} hostname không khớp journal ({resource['hostname']}); từ chối tự xóa.")
    net0 = config.get("net0", "")
    if not isinstance(net0, str) or not re.search(r"(?:^|,)ip=" + re.escape(resource["ip"]) + r"(?:/\d+)?(?:,|$)", net0):
        raise NeedsRepair(f"VMID {vmid} net0 không chứa IP dự kiến {resource['ip']}; từ chối tự xóa.")
    if item.get("name") and item.get("name") != resource["hostname"]:
        raise NeedsRepair(f"VMID {vmid} Proxmox name không khớp journal ({resource['hostname']}); từ chối tự xóa.")
    return {"vmid": vmid, "node": item.get("node"), "status": item.get("status"), "hostname": config.get("hostname"), "ip": resource["ip"]}


def command_result(command: list[str], cwd: Path, timeout: int | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(command, cwd=str(cwd), text=True, capture_output=True, timeout=timeout, env=env)
    except FileNotFoundError as e:
        raise RollbackPending(f"Không tìm thấy executable '{command[0]}'.") from e
    except subprocess.TimeoutExpired as e:
        raise RollbackPending(f"Lệnh timeout: {command[0]}.") from e
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "no output").strip()
        detail = redact(detail)[-5000:]
        raise RollbackPending(f"Lệnh thất bại (rc={result.returncode}): {' '.join(command[:4])} ...\n{detail}")
    return result


def terraform_state_addresses(terraform_dir: Path) -> set[str]:
    result = command_result(["terraform", "state", "list"], terraform_dir, timeout=60)
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def terraform_state_values(terraform_dir: Path) -> dict[str, dict[str, Any]]:
    result = command_result(["terraform", "show", "-json"], terraform_dir, timeout=120)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        raise RollbackPending("terraform show -json trả JSON không hợp lệ.") from e
    out: dict[str, dict[str, Any]] = {}
    def walk(module: dict[str, Any]) -> None:
        for resource in module.get("resources", []) or []:
            if resource.get("mode", "managed") == "managed" and resource.get("address"):
                out[resource["address"]] = resource.get("values") or {}
        for child in module.get("child_modules", []) or []:
            walk(child)
    walk(data.get("values", {}).get("root_module", {}) or {})
    return out


def expected_helpers_for_resource(resource: dict[str, Any]) -> list[str]:
    kind = resource["kind"]
    index = int(resource["index"])
    bases = sorted(BACKEND_HELPERS if kind == "backend" else FRONTEND_HELPERS)
    return [f"{base}[{index}]" for base in bases]


def create_plan_preflight(plan: dict[str, Any], old_be: int, new_be: int, old_fe: int, new_fe: int) -> tuple[list[dict[str, Any]], set[str], str]:
    expected = expected_create_addresses(old_be, new_be, old_fe, new_fe)
    actual: set[str] = set()
    actual_changes: list[str] = []
    for rc in resource_changes(plan):
        address = rc.get("address", "")
        mode = rc.get("mode", "managed")
        actions = (rc.get("change") or {}).get("actions", [])
        if not actions or actions == ["no-op"] or (mode == "data" and actions == ["read"]):
            continue
        if address == LOCAL_INVENTORY_ADDRESS:
            if "create" not in actions and "update" not in actions and actions != ["delete", "create"]:
                raise NeedsRepair(f"Plan scale-out làm thay đổi inventory theo action không được phép: {actions}")
            if "delete" in actions and actions != ["delete", "create"]:
                raise NeedsRepair("Plan scale-out đề xuất xóa local_file.ansible_inventory mà không tạo lại.")
            continue
        if address in expected:
            expected_type = address.split(".", 1)[0]
            if rc.get("type") != expected_type:
                raise NeedsRepair(f"Resource type không khớp address ở plan scale-out: {address} type={rc.get('type')}.")
            if actions != ["create"]:
                raise NeedsRepair(f"Resource scale-out {address} không phải create thuần: {actions}")
            actual.add(address)
            actual_changes.append(address)
            continue
        raise NeedsRepair(f"Plan scale-out có thay đổi ngoài danh sách resource mới cho phép: {address} actions={actions}")
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise NeedsRepair(f"Plan scale-out không khớp tập create chính xác; missing={missing}; extra={extra}")

    resources: list[dict[str, Any]] = []
    for kind, old_count, new_count in (("backend", old_be, new_be), ("frontend", old_fe, new_fe)):
        for index in range(old_count, new_count):
            item = expected_compute(kind, index)
            rc = next((x for x in resource_changes(plan) if x.get("address") == item["address"]), None)
            if rc is None:
                raise NeedsRepair(f"Plan thiếu compute create {item['address']}.")
            after = (rc.get("change") or {}).get("after") or {}
            planned_vmid = after.get("vm_id")
            planned_node = after.get("node_name")
            if planned_vmid is None or int(planned_vmid) != item["vmid"]:
                raise NeedsRepair(f"VMID planned của {item['address']} không khớp expected {item['vmid']}.")
            if not isinstance(planned_node, str) or not planned_node:
                raise NeedsRepair(f"Node planned của {item['address']} chưa xác định.")
            item["planned_node"] = planned_node
            item["helper_addresses"] = expected_helpers_for_resource(item)
            resources.append(item)
            for helper in item["helper_addresses"]:
                hrc = next((x for x in resource_changes(plan) if x.get("address") == helper), None)
                if hrc is None:
                    raise NeedsRepair(f"Plan thiếu firewall helper {helper} cho {item['address']}.")
                h_after = (hrc.get("change") or {}).get("after") or {}
                if int(h_after.get("container_id", -1)) != item["vmid"]:
                    raise NeedsRepair(f"Firewall helper {helper} trỏ sai container_id.")
                if h_after.get("node_name") != planned_node:
                    raise NeedsRepair(f"Firewall helper {helper} node không khớp compute planned.")
    inventory_content = get_inventory_content_from_plan(plan)
    validate_inventory_content(inventory_content, new_be, new_fe)
    return resources, actual, inventory_content


def assert_pve_guest_files_absent(api_url: str, vmid: int) -> None:
    # Read-only check for stale LXC config/firewall files before reusing a VMID.
    # PVE stores guest firewall files under /etc/pve/firewall/<VMID>.fw and LXC
    # definitions under /etc/pve/nodes/<node>/lxc/<VMID>.conf on cluster filesystem.
    parsed = urllib.parse.urlparse(api_url if "://" in api_url else "https://" + api_url)
    host = parsed.hostname
    if not host:
        raise RollbackPending("Không suy ra được SSH host từ Proxmox API URL để kiểm tra file guest/firewall mồ côi.")

    key_candidates = [
        Path.home() / ".ssh" / "id_ed25519",
        Path("/home/github-runner/.ssh/id_ed25519"),
    ]
    key_path = next((candidate for candidate in key_candidates if candidate.is_file() and not candidate.is_symlink()), None)
    if key_path is None:
        raise RollbackPending("Không tìm thấy SSH private key để kiểm tra read-only /etc/pve guest/firewall files.")

    remote_script = f'''set -eu
vmid={int(vmid)}
found=0
fw="/etc/pve/firewall/${{vmid}}.fw"
if [ -e "$fw" ]; then
  printf "PRESENT:%s\\n" "$fw"
  found=1
fi
for path in /etc/pve/nodes/*/lxc/${{vmid}}.conf; do
  [ -e "$path" ] || continue
  printf "PRESENT:%s\\n" "$path"
  found=1
done
if [ "$found" -ne 0 ]; then exit 42; fi
printf "ABSENT:%s\\n" "$vmid"
'''
    command = [
        "ssh", "-i", str(key_path),
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=8",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        f"root@{host}",
        "bash -lc " + shlex.quote(remote_script),
    ]
    try:
        result = subprocess.run(command, text=True, capture_output=True, timeout=15)
    except FileNotFoundError as e:
        raise RollbackPending("Không tìm thấy ssh executable để kiểm tra file guest/firewall mồ côi.") from e
    except subprocess.TimeoutExpired as e:
        raise RollbackPending(f"SSH kiểm tra read-only file guest/firewall VMID {vmid} bị timeout.") from e

    detail = redact(((result.stdout or "") + "\\n" + (result.stderr or "")).strip())[-2000:]
    if result.returncode == 42 or "PRESENT:" in result.stdout:
        raise NeedsRepair(
            f"Phát hiện file cấu hình LXC/firewall mồ côi hoặc VMID conflict cho VMID {vmid}: {detail}. "
            "Không tự ghi đè hay xóa; cần đối soát trên Proxmox."
        )
    if result.returncode != 0 or f"ABSENT:{vmid}" not in result.stdout:
        raise RollbackPending(
            f"Không thể chứng minh các file guest/firewall của VMID {vmid} vắng mặt qua SSH read-only: {detail}"
        )


def record_plan(args: argparse.Namespace) -> int:
    root = Path(args.journal_root)
    directory, journal = read_journal(root, args.transaction_id)
    assert_transaction_open(journal)
    if journal.get("stage") != "PREPARED" or journal.get("apply_attempted"):
        raise TransactionError("record-plan chỉ được chạy ở PREPARED trước Terraform apply.")
    baseline = load_baseline(directory)
    state_file = Path(args.state_file)
    target = capacity_shape(parse_json_file(state_file), "target autoscale state")
    old_be, old_fe = baseline["backend_count"], baseline["frontend_count"]
    new_be, new_fe = int(args.new_be), int(args.new_fe)
    if (old_be, old_fe) != (int(args.old_be), int(args.old_fe)):
        raise NeedsRepair("Old capacity từ workflow không khớp baseline trong journal.")
    if target["backend_count"] != new_be or target["frontend_count"] != new_fe:
        raise NeedsRepair("Target counts trong state file không khớp workflow outputs.")
    if new_be != old_be + journal["request"]["effective_add_backend"] or new_fe != old_fe + journal["request"]["effective_add_frontend"]:
        raise NeedsRepair("Target capacity không khớp số lượng đã yêu cầu khi mở transaction.")
    plan = parse_json_file(Path(args.plan_json))
    resources, actual_creates, inventory_content = create_plan_preflight(plan, old_be, new_be, old_fe, new_fe)
    for item in resources:
        map_name = "backend_node_map" if item["kind"] == "backend" else "frontend_node_map"
        mapped_node = target[map_name].get(str(item["vmid"]))
        if mapped_node != item["planned_node"]:
            raise NeedsRepair(f"Node map {map_name}[{item['vmid']}]={mapped_node!r} không khớp Terraform plan node={item['planned_node']!r}.")

    # Reserve only IDs that are proved absent before apply, both in Terraform state and live PVE.
    tf_state = terraform_state_addresses(Path(args.terraform_dir))
    for item in resources:
        if item["address"] in tf_state:
            raise NeedsRepair(f"Expected new address đã tồn tại trong Terraform state trước apply: {item['address']}.")
    pve = pve_resources(args.api_url)
    collisions = [item["vmid"] for item in resources if item["vmid"] in pve]
    if collisions:
        raise NeedsRepair(f"VMID planned đã tồn tại trên Proxmox trước apply: {collisions}; từ chối tạo giao dịch.")

    # Check guest definitions and firewall files as well as the API/state view. This catches
    # orphaned /etc/pve/firewall/<VMID>.fw and stale per-node LXC configs left by old attempts.
    for item in resources:
        assert_pve_guest_files_absent(args.api_url, int(item["vmid"]))

    journal["target_capacity"] = target
    journal["planned_create_addresses"] = sorted(actual_creates)
    journal["expected_resources"] = resources
    journal["plan_preflight"] = {
        "terraform_plan_json_sha256": hashlib.sha256(Path(args.plan_json).read_bytes()).hexdigest(),
        "expected_create_count": len(actual_creates),
        "preflight_pve_resource_count": len(pve),
        "preflight_timestamp": utc_now(),
        "target_inventory_sha256": hashlib.sha256(inventory_content.encode("utf-8")).hexdigest(),
    }
    journal["safety"]["vmids_absent_before_apply"] = True
    journal["safety"]["plan_create_allowlist_exact"] = True
    write_journal(directory, journal)
    print(f"[transaction] Plan preflight OK: {len(resources)} container(s), {len(actual_creates)} planned resources; all VMIDs absent from live Proxmox.")
    return 0


def stage(args: argparse.Namespace) -> int:
    root = Path(args.journal_root)
    directory, journal = read_journal(root, args.transaction_id)
    assert_transaction_open(journal)
    stages = ["PREPARED", "PROVISIONING", "PROVISIONED", "CONFIGURING", "VALIDATING"]
    stage_value = args.stage
    if stage_value not in stages:
        raise TransactionError(f"Stage không hợp lệ: {stage_value}")
    current = journal.get("stage", "PREPARED")
    if current in stages and stages.index(stage_value) < stages.index(current):
        raise TransactionError(f"Không cho phép lùi stage từ {current} về {stage_value}.")
    if stage_value == "PROVISIONING":
        if not journal.get("planned_create_addresses") or not journal.get("safety", {}).get("vmids_absent_before_apply"):
            raise NeedsRepair("Không được đánh dấu apply attempted khi chưa lưu plan preflight.")
        journal["apply_attempted"] = True
        journal["apply_started_at"] = utc_now()
    journal["stage"] = stage_value
    journal["status"] = stage_value
    write_journal(directory, journal)
    print(f"[transaction] {args.transaction_id}: stage={stage_value}")
    return 0


def http_get(url: str, timeout: int = 5) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "meowcloud-transaction-v1/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.getcode(), response.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        raise RollbackPending(f"HTTP healthcheck không truy cập được {url}: {e}") from e


def baseline_health_checks(resources: dict[int, dict[str, Any]], baseline: dict[str, Any], phase: str) -> set[str]:
    backend_ids: set[str] = set()
    be_count = int(baseline["backend_count"])
    fe_count = int(baseline["frontend_count"])
    for index in range(be_count):
        ip = f"192.168.150.{101 + index}"
        code, body = http_get(f"http://{ip}:5000/health")
        if code != 200:
            raise RollbackPending(f"Baseline backend {ip} health trả HTTP {code} ở phase {phase}; không xóa resource mới.")
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception as e:
            raise RollbackPending(f"Baseline backend {ip} trả health JSON lỗi ở phase {phase}.") from e
        if payload.get("status") != "UP" or not isinstance(payload.get("node"), str) or not payload["node"]:
            raise RollbackPending(f"Baseline backend {ip} không báo status UP/node hợp lệ ở phase {phase}.")
        backend_ids.add(payload["node"])
    for index in range(fe_count):
        ip = f"192.168.150.{171 + index}"
        code, _ = http_get(f"http://{ip}/")
        if code != 200:
            raise RollbackPending(f"Baseline frontend {ip} health trả HTTP {code} ở phase {phase}; không xóa resource mới.")
    return backend_ids


def baseline_frontend_ips(baseline: dict[str, Any]) -> set[str]:
    return {f"192.168.150.{171 + index}" for index in range(int(baseline["frontend_count"]))}


def verify_vip(baseline_backend_ids: set[str], baseline_frontend_ip_set: set[str], phase: str, attempts: int = 15) -> None:
    code, _ = http_get("http://192.168.150.100/lb-status")
    if code != 200:
        raise RollbackPending(f"VIP 192.168.150.100/lb-status trả HTTP {code} ở phase {phase}.")

    # The frontend returns its own IP in the page. A plain HTTP 200 is not sufficient:
    # it could still come from a newly-created frontend that the rollback is about to delete.
    for attempt in range(attempts):
        code, body = http_get("http://192.168.150.100/")
        if code != 200:
            raise RollbackPending(f"VIP frontend route trả HTTP {code} ở phase {phase}.")
        page = body.decode("utf-8", errors="replace")
        matched_ip = next((ip for ip in baseline_frontend_ip_set if ip in page), None)
        if matched_ip is None:
            raise RollbackPending(
                f"VIP frontend response không định danh được frontend baseline ở phase {phase}; "
                "có thể upstream còn trỏ tới frontend mới."
            )

    last_node = ""
    for attempt in range(attempts):
        try:
            code, body = http_get("http://192.168.150.100/api/health")
            if code == 200:
                payload = json.loads(body.decode("utf-8"))
                last_node = str(payload.get("node", ""))
                if payload.get("status") == "UP" and last_node in baseline_backend_ids:
                    return
        except Exception:
            pass
        if attempt + 1 < attempts:
            time.sleep(1)
    raise RollbackPending(f"VIP API sau {phase} không route tới baseline backend; last_node={last_node!r}.")


def ensure_capacity_file(path: Path, expected: dict[str, Any], label: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise NeedsRepair(f"Thiếu hoặc symlink {label}: {path}.")
    actual = capacity_shape(parse_json_file(path), label)
    if actual != expected:
        raise NeedsRepair(f"{label} không khớp expected transaction snapshot; không tự ghi đè file bị thay đổi ngoài giao dịch.")


def ensure_capacity_is_baseline_or_target(actual: dict[str, Any], target: Any, baseline: dict[str, Any], label: str) -> None:
    if actual == baseline:
        return
    if isinstance(target, dict) and actual == target:
        return
    raise NeedsRepair(
        f"{label} không khớp baseline hoặc candidate đã ghi journal; "
        "từ chối ghi đè state chưa chứng minh thuộc transaction."
    )


def verify_inventory_transaction_ownership(inventory_path: Path, journal: dict[str, Any], baseline_content: str) -> str:
    """Only permit replacement when current inventory matches a journaled transaction/baseline snapshot."""
    if inventory_path.is_symlink() or not inventory_path.is_file():
        raise NeedsRepair(f"Inventory hiện tại thiếu hoặc không phải file thường: {inventory_path}; không tự tạo/ghi đè.")
    try:
        current_bytes = inventory_path.read_bytes()
    except OSError as e:
        raise RollbackPending(f"Không đọc được inventory để xác minh ownership: {e}") from e
    current_hash = hashlib.sha256(current_bytes).hexdigest()
    target_hash = (journal.get("plan_preflight") or {}).get("target_inventory_sha256")
    baseline_hash = hashlib.sha256(baseline_content.encode("utf-8")).hexdigest()
    permitted = {baseline_hash}
    if isinstance(target_hash, str) and re.fullmatch(r"[0-9a-f]{64}", target_hash):
        permitted.add(target_hash)
    else:
        raise NeedsRepair("Journal thiếu target_inventory_sha256 hợp lệ; không thể chứng minh file inventory thuộc transaction.")
    if current_hash not in permitted:
        raise NeedsRepair(
            f"Inventory bị thay đổi ngoài snapshot baseline/transaction (sha256={current_hash}); "
            "không ghi đè và không xóa resource."
        )
    return current_hash


def validate_rollback_plan(plan: dict[str, Any], expected_resources: list[dict[str, Any]], baseline: dict[str, Any]) -> tuple[set[str], str]:
    allowed: dict[str, dict[str, Any] | None] = {}
    for item in expected_resources:
        allowed[item["address"]] = item
        for helper in item.get("helper_addresses", []):
            allowed[helper] = item

    deletes: set[str] = set()
    inventory_seen = False
    inventory_content = ""
    for rc in resource_changes(plan):
        address = rc.get("address", "")
        mode = rc.get("mode", "managed")
        actions = (rc.get("change") or {}).get("actions", [])
        before = (rc.get("change") or {}).get("before") or {}
        after = (rc.get("change") or {}).get("after") or {}
        if not actions or actions == ["no-op"] or (mode == "data" and actions == ["read"]):
            if address == LOCAL_INVENTORY_ADDRESS:
                inventory_seen = True
            continue
        if address == LOCAL_INVENTORY_ADDRESS:
            inventory_seen = True
            if actions == ["delete"]:
                raise NeedsRepair("Rollback plan muốn xóa inventory mà không tạo lại.")
            if actions not in (["create"], ["update"], ["delete", "create"]):
                raise NeedsRepair(f"Inventory plan action không hợp lệ: {actions}.")
            continue
        expected = allowed.get(address)
        if expected is None:
            raise NeedsRepair(f"Rollback plan thay đổi resource ngoài allowlist transaction: {address} actions={actions}.")
        expected_type = address.split(".", 1)[0]
        if rc.get("type") != expected_type:
            raise NeedsRepair(f"Rollback plan resource type không khớp address: {address} type={rc.get('type')}.")
        if actions != ["delete"]:
            raise NeedsRepair(f"Rollback plan có update/replacement/create trên transaction resource {address}: {actions}.")
        if address == expected["address"]:
            vmid = before.get("vm_id") or before.get("container_id")
        else:
            vmid = before.get("container_id")
        if vmid is None or int(vmid) != int(expected["vmid"]):
            raise NeedsRepair(f"Rollback delete {address} có VMID/container_id={vmid}, không khớp VMID được journal sở hữu ({expected['vmid']}).")
        deletes.add(address)

    inventory_content = get_inventory_content_from_plan(plan)
    validate_inventory_content(inventory_content, int(baseline["backend_count"]), int(baseline["frontend_count"]))
    if not inventory_seen and LOCAL_INVENTORY_ADDRESS not in {r.get("address") for r in (plan.get("planned_values", {}).get("root_module", {}).get("resources", []) or [])}:
        # We still accept planned_values-only representation; get_inventory_content above proves it exists.
        pass
    return deletes, inventory_content


def temp_plan_paths() -> tuple[Path, Path]:
    tmp_root = Path(os.getenv("RUNNER_TEMP", tempfile.gettempdir()))
    tmp_root.mkdir(parents=True, exist_ok=True)
    fd, p1 = tempfile.mkstemp(prefix="meowcloud-rollback-", suffix=".tfplan", dir=str(tmp_root))
    os.close(fd)
    Path(p1).unlink(missing_ok=True)
    fd, p2 = tempfile.mkstemp(prefix="meowcloud-rollback-", suffix=".json", dir=str(tmp_root))
    os.close(fd)
    os.chmod(p2, 0o600)
    return Path(p1), Path(p2)


def cleanup_temp(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def ensure_tf_initialized(terraform_dir: Path) -> None:
    # The normal workflow initializes Terraform before attempting apply. On early apply failure,
    # this call is still safe and does not mutate infrastructure.
    command_result(["terraform", "init", "-input=false"], terraform_dir, timeout=300)


def assert_transaction_resources_tracked(
    expected_resources: list[dict[str, Any]],
    current_addresses: set[str],
    live_resources: dict[int, dict[str, Any]],
) -> None:
    """Fail closed if a transaction-owned live CT or its helpers are missing from state.

    This helper performs no Terraform state mutation. In v1 policy A, any live CT that
    cannot be proven tracked is an operator-repair case, not an implicit import operation.
    """
    for item in expected_resources:
        vmid = int(item["vmid"])
        if vmid not in live_resources:
            continue
        if item["address"] not in current_addresses:
            raise NeedsRepair(
                f"VMID {vmid} ({item['address']}) còn tồn tại trên Proxmox nhưng thiếu resource address "
                "trong Terraform state. Chính sách v1.0.3 không tự import hoặc sửa state; "
                "giữ transaction/active.lock ở NEEDS_REPAIR để đối soát thủ công."
            )
        missing_helpers = sorted(set(item.get("helper_addresses", [])) - current_addresses)
        if missing_helpers:
            raise NeedsRepair(
                f"VMID {vmid} live và resource chính đã tracked, nhưng Terraform state thiếu firewall helper "
                f"mapping(s) {missing_helpers}; không tự sửa state hoặc xóa VM."
            )


def run_rollback(args: argparse.Namespace, directory: Path, journal: dict[str, Any]) -> int:
    if not args.execute:
        print("DRY-RUN: chưa chạy compensation. Để thực thi cần --execute (workflow chỉ truyền cờ này cho failure handler).")
        print(json.dumps({"operation_id": journal["operation_id"], "status": journal["status"], "stage": journal["stage"],
                          "apply_attempted": journal.get("apply_attempted"),
                          "planned_create_addresses": journal.get("planned_create_addresses", []),
                          "expected_resources": journal.get("expected_resources", [])}, ensure_ascii=False, indent=2))
        return 0

    root = Path(args.journal_root)
    state_path = Path(args.state_file)

    # A plan/identity preflight ambiguity is not a successful rollback. Restore only the
    # candidate capacity files proven to belong to this transaction, but retain NEEDS_REPAIR
    # and active.lock so all shared-state workflows remain blocked pending manual reconciliation.
    if journal.get("status") == "NEEDS_REPAIR" and not journal.get("apply_attempted"):
        baseline = load_baseline(directory)
        current_capacity = capacity_shape(parse_json_file(state_path), "autoscale state")
        ensure_capacity_is_baseline_or_target(current_capacity, journal.get("target_capacity"), baseline, "AUTOSCALE_STATE_FILE")
        terraform_dir_for_check = Path(args.terraform_dir).resolve()
        auto_tfvars = terraform_dir_for_check / "autoscale.auto.tfvars.json"
        if auto_tfvars.exists() or auto_tfvars.is_symlink():
            if auto_tfvars.is_symlink() or not auto_tfvars.is_file():
                raise NeedsRepair(f"Terraform auto tfvars là symlink hoặc không phải file thường: {auto_tfvars}.")
            auto_current = capacity_shape(parse_json_file(auto_tfvars), "terraform/autoscale.auto.tfvars.json")
            ensure_capacity_is_baseline_or_target(auto_current, journal.get("target_capacity"), baseline, "terraform/autoscale.auto.tfvars.json")
            atomic_write_json(auto_tfvars, baseline, 0o600)
        atomic_write_json(state_path, baseline, 0o600)
        journal["rollback_status"] = "PREFLIGHT_BLOCKED_NEEDS_REPAIR"
        journal["capacity_restored_at"] = utc_now()
        root_cause = redact(str(journal.get("last_error") or "Preflight ownership ambiguous; reason was not recorded."))[:2500]
        journal["last_error"] = root_cause
        write_journal(directory, journal)
        raise NeedsRepair(
            "Preflight ownership/plan ambiguity (original reason: " + root_cause + "). "
            "Capacity candidate was restored only after exact snapshot verification, "
            "but journal and active.lock remain NEEDS_REPAIR; resolve the original reason before proceeding."
        )

    assert_transaction_open(journal)
    terraform_dir = Path(args.terraform_dir).resolve()
    ansible_dir = Path(args.ansible_dir).resolve()
    inventory_path = Path(args.inventory_file).resolve()
    api_url = args.api_url or os.getenv("TF_VAR_proxmox_api_url", "")
    baseline = load_baseline(directory)

    journal["rollback_status"] = "IN_PROGRESS"
    journal["rollback_started_at"] = utc_now()
    journal["status"] = "ROLLBACK_PENDING"
    journal["stage"] = "ROLLBACK_PENDING"
    write_journal(directory, journal)

    if not journal.get("apply_attempted"):
        # No Terraform apply was attempted: no resource is owned by this operation yet.
        current_state = capacity_shape(parse_json_file(state_path), "autoscale state")
        target = journal.get("target_capacity")
        # Restore only a state snapshot proven to be either the pre-transaction baseline or
        # the exact candidate recorded by this operation. If the runner died after selector
        # wrote the file but before record-target persisted it, do not guess ownership.
        ensure_capacity_is_baseline_or_target(current_state, target, baseline, "AUTOSCALE_STATE_FILE")
        auto_tfvars = terraform_dir / "autoscale.auto.tfvars.json"
        if auto_tfvars.exists() or auto_tfvars.is_symlink():
            if auto_tfvars.is_symlink() or not auto_tfvars.is_file():
                raise NeedsRepair(f"Terraform auto tfvars là symlink hoặc không phải file thường: {auto_tfvars}.")
            auto_current = capacity_shape(parse_json_file(auto_tfvars), "terraform/autoscale.auto.tfvars.json")
            ensure_capacity_is_baseline_or_target(auto_current, target, baseline, "terraform/autoscale.auto.tfvars.json")
        atomic_write_json(state_path, baseline, 0o600)
        atomic_write_json(auto_tfvars, baseline, 0o600)
        for env_name in ("TFPLAN_FILE", "TFPLAN_JSON"):
            value = os.getenv(env_name)
            if value:
                path = Path(value).resolve()
                runner_temp = Path(os.getenv("RUNNER_TEMP", tempfile.gettempdir())).resolve()
                try:
                    path.relative_to(runner_temp)
                    cleanup_temp(path)
                except ValueError:
                    pass
        journal["status"] = "ROLLED_BACK"
        journal["stage"] = "ROLLED_BACK"
        journal["rollback_status"] = "COMPLETED_NO_APPLY"
        journal["rollback_completed_at"] = utc_now()
        journal["last_error"] = "Workflow thất bại trước Terraform apply; chỉ khôi phục capacity candidate về baseline, không có tài nguyên mới được phép xóa."
        write_journal(directory, journal)
        try:
            release_lock(root, journal["operation_id"])
        except Exception as e:
            # State is already terminal and baseline is verified; a terminal journal lets the
            # next begin reconcile a stale lock without mislabeling the rollback outcome.
            print(f"[transaction] WARNING: ROLLED_BACK nhưng cần đối soát active.lock: {redact(str(e))}", file=sys.stderr)
        print("[transaction] ROLLED_BACK: apply chưa được bắt đầu; đã phục hồi capacity baseline, không xóa resource.")
        return 0

    target = journal.get("target_capacity")
    if not isinstance(target, dict) or not journal.get("expected_resources"):
        raise NeedsRepair("Apply đã được đánh dấu attempted nhưng journal không có target/expected resources; cấm suy đoán resource để xóa.")
    ensure_capacity_file(state_path, target, "AUTOSCALE_STATE_FILE")
    auto_tfvars = terraform_dir / "autoscale.auto.tfvars.json"
    ensure_capacity_file(auto_tfvars, target, "terraform/autoscale.auto.tfvars.json")
    if not api_url:
        raise RollbackPending("Thiếu URL Proxmox API; không thể xác minh ownership.")
    inventory_expected_path = (terraform_dir / "../ansible/inventory.ini").resolve()
    if inventory_path != inventory_expected_path:
        raise NeedsRepair(f"Inventory path không khớp local_file.ansible_inventory: {inventory_path} != {inventory_expected_path}.")

    ensure_tf_initialized(terraform_dir)
    current_addresses = terraform_state_addresses(terraform_dir)
    live = pve_resources(api_url)
    expected_resources = journal["expected_resources"]

    # Policy A (v1.0.3): never mutate Terraform state automatically to reconcile an
    # untracked live guest. If apply created a Proxmox CT but failed before the main
    # resource address was persisted, stop in NEEDS_REPAIR and retain active.lock.
    # This intentionally sacrifices automatic recovery for ambiguous partial-apply cases.
    assert_transaction_resources_tracked(expected_resources, current_addresses, live)

    for item in expected_resources:
        vmid = int(item["vmid"])
        live_item = live.get(vmid)
        verified = verify_live_identity(api_url, item, live_item)
        if live_item is None and item["address"] in current_addresses:
            # The CT may have vanished during partial apply/manual intervention. Do not create it;
            # the fresh baseline plan must still pass the strict transaction allowlist.
            print(f"[transaction] Expected CT VMID={vmid} absent from Proxmox; fresh rollback plan will reconcile current state.")
        elif live_item is not None:
            print(f"[transaction] Verified live transaction resource {item['address']} VMID={vmid} node={verified['node']}")

    # A plan is always produced from current state; no stale pre-apply state snapshot is restored.
    plan_path, plan_json_path = temp_plan_paths()
    try:
        command_result(["terraform", "plan", "-input=false", "-parallelism=1", f"-var-file={directory / 'baseline.json'}", f"-out={plan_path}"], terraform_dir, timeout=600)
        os.chmod(plan_path, 0o600)
        result = command_result(["terraform", "show", "-json", str(plan_path)], terraform_dir, timeout=180)
        atomic_write_bytes(plan_json_path, result.stdout.encode("utf-8"), 0o600)
        plan = parse_json_file(plan_json_path)
        deletes, baseline_inventory_content = validate_rollback_plan(plan, expected_resources, baseline)
        # Every transaction-owned address that is currently tracked must be deleted by this
        # exact plan. Missing state addresses are handled fail-closed above when their CT is live.
        expected_tracked_deletes = set(journal.get("planned_create_addresses", [])) & current_addresses
        if deletes != expected_tracked_deletes:
            raise NeedsRepair(
                "Rollback delete set không khớp chính xác các resource transaction hiện có trong state; "
                f"expected={sorted(expected_tracked_deletes)}, actual={sorted(deletes)}."
            )
        # Keep the existing protected-VMID gate active for rollback, after our stricter ownership gate.
        verify_script = Path(args.verify_script).resolve()
        gate = subprocess.run([sys.executable, str(verify_script), str(plan_json_path), "--allow-scale-in"],
                              cwd=str(terraform_dir), text=True, capture_output=True, timeout=60)
        if gate.returncode != 0:
            detail = redact((gate.stderr or gate.stdout or "verify_tfplan.py rejected rollback plan").strip())[-4000:]
            raise NeedsRepair(f"Existing verify_tfplan.py rejected the rollback plan: {detail}")
        journal["approved_delete_addresses"] = sorted(deletes)
        journal["safety"]["terraform_rollback_plan_validated"] = True
        journal["rollback_plan"] = {
            "sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
            "json_sha256": hashlib.sha256(plan_json_path.read_bytes()).hexdigest(),
            "approved_delete_addresses": sorted(deletes),
            "planned_delete_count": len(deletes),
            "validated_at": utc_now(),
        }
        write_journal(directory, journal)

        # Ensure old service instances are healthy before changing upstreams.
        old_backend_ids = baseline_health_checks(live, baseline, "before-upstream-restore")
        old_frontend_ips = baseline_frontend_ips(baseline)
        current_inventory_sha256 = verify_inventory_transaction_ownership(
            inventory_path, journal, baseline_inventory_content
        )
        journal["safety"]["inventory_current_snapshot_verified"] = True
        journal["safety"]["inventory_current_sha256"] = current_inventory_sha256
        write_journal(directory, journal)
        atomic_write_bytes(inventory_path, baseline_inventory_content.encode("utf-8"), 0o644)
        journal["safety"]["inventory_restored_before_delete"] = True
        journal["baseline_inventory_sha256"] = hashlib.sha256(baseline_inventory_content.encode("utf-8")).hexdigest()
        journal["stage"] = "ROLLBACK_CONFIGURING"
        write_journal(directory, journal)

        # Remove the new endpoints from every generated upstream before destroying any new CT.
        for playbook in (
            "playbooks/04-setup-frontend.yml",
            "playbooks/02-setup-lb.yml",
            "playbooks/06-setup-observer.yml",
        ):
            command_result(["ansible-playbook", "-i", str(inventory_path), playbook], ansible_dir, timeout=900)
        verify_vip(old_backend_ids, old_frontend_ips, "after-upstream-restore")

        # Apply exactly the saved plan that passed our resource ownership allowlist and the
        # repository's existing verify_tfplan.py protected-VMID checks.
        journal["stage"] = "ROLLBACK_APPLYING"
        write_journal(directory, journal)
        command_result(["terraform", "apply", "-parallelism=1", "-auto-approve", "-input=false", str(plan_path)], terraform_dir, timeout=900)

        # Verify state and the Proxmox API agree that every transaction-created CT is gone.
        after_addresses = terraform_state_addresses(terraform_dir)
        after_live = pve_resources(api_url)
        remaining_addresses = sorted(set(journal.get("planned_create_addresses", [])) & after_addresses)
        if remaining_addresses:
            raise RollbackPending(f"Terraform state vẫn chứa resource thuộc transaction sau rollback: {remaining_addresses}.")
        for item in expected_resources:
            if item["address"] in after_addresses:
                raise RollbackPending(f"Terraform state vẫn chứa compute resource mới sau rollback: {item['address']}.")
            if int(item["vmid"]) in after_live:
                # If still present, prove its identity before refusing to finish; never delete it directly.
                verify_live_identity(api_url, item, after_live[int(item["vmid"])])
                raise RollbackPending(f"VMID transaction-owned {item['vmid']} vẫn tồn tại sau terraform apply; giữ capacity target và khóa.")
            assert_pve_guest_files_absent(api_url, int(item["vmid"]))

        # Check baseline guest endpoints and routes after the destroy apply.
        old_backend_ids_after = baseline_health_checks(after_live, baseline, "after-terraform-rollback")
        verify_vip(old_backend_ids_after, old_frontend_ips, "after-terraform-rollback")
        current_inventory = inventory_path.read_text(encoding="utf-8")
        if current_inventory != baseline_inventory_content:
            raise RollbackPending("Inventory file sau Terraform rollback khác content baseline đã duyệt.")

        # Capacity is changed back only after resources, state, inventory and service traffic verify.
        atomic_write_json(state_path, baseline, 0o600)
        atomic_write_json(auto_tfvars, baseline, 0o600)
        final_state = capacity_shape(parse_json_file(state_path), "restored capacity")
        if final_state != baseline:
            raise RollbackPending("Capacity file không đọc lại đúng baseline sau khi restore.")
        journal["status"] = "ROLLED_BACK"
        journal["stage"] = "ROLLED_BACK"
        journal["rollback_status"] = "COMPLETED"
        journal["rollback_completed_at"] = utc_now()
        journal["last_error"] = "Workflow thất bại sau khi Terraform apply bắt đầu; compensation đã phục hồi upstream trước khi xóa resource của transaction và xác minh baseline."
        write_journal(directory, journal)
        try:
            release_lock(root, journal["operation_id"])
        except Exception as e:
            print(f"[transaction] WARNING: ROLLED_BACK nhưng cần đối soát active.lock: {redact(str(e))}", file=sys.stderr)
        print(f"[transaction] ROLLED_BACK OK: {len(deletes)} state resource(s) deleted by reviewed Terraform plan; baseline capacity/services verified.")
        return 0
    finally:
        cleanup_temp(plan_path)
        cleanup_temp(plan_json_path)


def commit(args: argparse.Namespace) -> int:
    root = Path(args.journal_root)
    directory, journal = read_journal(root, args.transaction_id)
    assert_transaction_open(journal)
    if journal.get("stage") != "VALIDATING":
        raise TransactionError(f"Chỉ commit sau VALIDATING; stage hiện tại={journal.get('stage')}.")
    target = journal.get("target_capacity")
    if not isinstance(target, dict) or not journal.get("expected_resources"):
        raise NeedsRepair("Journal thiếu target capacity hoặc expected resources.")
    state_path = Path(args.state_file)
    actual_capacity = capacity_shape(parse_json_file(state_path), "autoscale state")
    if actual_capacity != target:
        raise NeedsRepair("Capacity file sau healthcheck không còn khớp target transaction; không commit.")

    terraform_dir = Path(args.terraform_dir).resolve()
    addresses = terraform_state_addresses(terraform_dir)
    state_values = terraform_state_values(terraform_dir)
    api_url = args.api_url or os.getenv("TF_VAR_proxmox_api_url", "")
    live = pve_resources(api_url)
    missing_created = sorted(set(journal.get("planned_create_addresses", [])) - addresses)
    if missing_created:
        raise NeedsRepair(f"Terraform state thiếu planned resource(s) khi commit: {missing_created}.")
    for item in journal["expected_resources"]:
        if item["address"] not in addresses:
            raise NeedsRepair(f"Không tìm thấy compute resource trong Terraform state khi commit: {item['address']}.")
        values = state_values.get(item["address"], {})
        if values.get("vm_id") is None or int(values["vm_id"]) != int(item["vmid"]):
            raise NeedsRepair(f"VMID trong Terraform state không khớp journal ở {item['address']}.")
        verified = verify_live_identity(api_url, item, live.get(int(item["vmid"])))
        if verified is None:
            raise RollbackPending(f"Không tìm thấy VMID={item['vmid']} trên Proxmox khi commit.")
    journal["status"] = "COMMITTED"
    journal["stage"] = "COMMITTED"
    journal["committed_at"] = utc_now()
    journal["rollback_status"] = "NOT_REQUIRED"
    journal["last_error"] = None
    write_journal(directory, journal)
    try:
        release_lock(root, journal["operation_id"])
    except Exception as e:
        # The journal is terminal now; a stale lock can be safely reconciled by the next begin.
        print(f"[transaction] WARNING: journal COMMITTED but lock release needs cleanup: {redact(str(e))}", file=sys.stderr)
    print(f"[transaction] COMMITTED: {journal['operation_id']} resources and Proxmox identities verified.")
    return 0


def status(args: argparse.Namespace) -> int:
    root = Path(args.journal_root)
    if args.transaction_id:
        directory, journal = read_journal(root, args.transaction_id)
        print(json.dumps({k: journal.get(k) for k in (
            "operation_id", "created_at", "updated_at", "status", "stage", "request", "baseline_capacity",
            "target_capacity", "planned_create_addresses", "expected_resources", "approved_delete_addresses",
            "apply_attempted", "rollback_status", "safety", "last_error", "source_commit")}, ensure_ascii=False, indent=2))
        return 0
    rows = []
    if root.exists():
        for path in sorted(root.glob("txn-*/journal.json")):
            try:
                j = parse_json_file(path)
                rows.append({"operation_id": j.get("operation_id"), "status": j.get("status"), "stage": j.get("stage"),
                             "created_at": j.get("created_at"), "last_error": j.get("last_error")})
            except Exception as e:
                rows.append({"journal": str(path), "status": "UNREADABLE", "error": str(e)})
    print(json.dumps({"journal_root": str(root), "active_lock": (root / "active.lock").read_text(encoding="utf-8").strip() if (root / "active.lock").exists() else None,
                      "transactions": rows}, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="MeowCloud Compensating Transactions v1")
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("begin", help="Mở journal bền vững trước khi selector thay đổi capacity")
    b.add_argument("--state-file", default=STATE_FILE_DEFAULT)
    b.add_argument("--backup-file", required=True)
    b.add_argument("--journal-root", default=JOURNAL_ROOT_DEFAULT)
    b.add_argument("--scale-target", required=True, choices=["backend", "frontend", "both"])
    b.add_argument("--add-be", required=True)
    b.add_argument("--add-fe", required=True)
    b.add_argument("--repo-root", default=".")
    b.set_defaults(func=begin)

    rt = sub.add_parser("record-target", help="Ghi target capacity ngay sau selector để rollback pre-apply có ownership proof")
    rt.add_argument("--transaction-id", required=True)
    rt.add_argument("--state-file", default=STATE_FILE_DEFAULT)
    rt.add_argument("--journal-root", default=JOURNAL_ROOT_DEFAULT)
    rt.set_defaults(func=record_target)

    r = sub.add_parser("record-plan", help="Kiểm duyệt plan scale-out và chứng minh VMID vắng mặt trước apply")
    r.add_argument("--transaction-id", required=True)
    r.add_argument("--state-file", default=STATE_FILE_DEFAULT)
    r.add_argument("--journal-root", default=JOURNAL_ROOT_DEFAULT)
    r.add_argument("--plan-json", required=True)
    r.add_argument("--terraform-dir", required=True)
    r.add_argument("--api-url", default="")
    r.add_argument("--old-be", required=True, type=int)
    r.add_argument("--old-fe", required=True, type=int)
    r.add_argument("--new-be", required=True, type=int)
    r.add_argument("--new-fe", required=True, type=int)
    r.set_defaults(func=record_plan)

    s = sub.add_parser("stage", help="Ghi stage bền vững trước/sau apply và trước kiểm tra dịch vụ")
    s.add_argument("--transaction-id", required=True)
    s.add_argument("--journal-root", default=JOURNAL_ROOT_DEFAULT)
    s.add_argument("--stage", required=True, choices=["PROVISIONING", "PROVISIONED", "CONFIGURING", "VALIDATING"])
    s.set_defaults(func=stage)

    c = sub.add_parser("commit", help="Commit chỉ sau xác minh Terraform state và Proxmox live")
    c.add_argument("--transaction-id", required=True)
    c.add_argument("--state-file", default=STATE_FILE_DEFAULT)
    c.add_argument("--journal-root", default=JOURNAL_ROOT_DEFAULT)
    c.add_argument("--terraform-dir", required=True)
    c.add_argument("--api-url", default="")
    c.set_defaults(func=commit)

    rb = sub.add_parser("compensate", help="Rollback có allowlist; mặc định dry-run, --execute chỉ dùng trong failure handler đã duyệt")
    rb.add_argument("--transaction-id", required=True)
    rb.add_argument("--state-file", default=STATE_FILE_DEFAULT)
    rb.add_argument("--journal-root", default=JOURNAL_ROOT_DEFAULT)
    rb.add_argument("--terraform-dir", required=True)
    rb.add_argument("--ansible-dir", required=True)
    rb.add_argument("--inventory-file", required=True)
    rb.add_argument("--verify-script", required=True)
    rb.add_argument("--api-url", default="")
    rb.add_argument("--execute", action="store_true", help="Actually restore routes and apply the strictly-validated rollback plan")
    rb.set_defaults(func=lambda a: _compensate_entry(a))

    gd = sub.add_parser("guard", help="Read-only guard cho provision/destroy khi có transaction mở")
    gd.add_argument("--journal-root", default=JOURNAL_ROOT_DEFAULT)
    gd.add_argument("--workflow", choices=["provision", "destroy", "manual"], default="manual")
    gd.set_defaults(func=guard)

    st = sub.add_parser("status", help="Xem journal mà không in secret")
    st.add_argument("--journal-root", default=JOURNAL_ROOT_DEFAULT)
    st.add_argument("--transaction-id")
    st.set_defaults(func=status)
    return p


def _compensate_entry(args: argparse.Namespace) -> int:
    root = Path(args.journal_root)
    directory, journal = read_journal(root, args.transaction_id)
    try:
        return run_rollback(args, directory, journal)
    except NeedsRepair as e:
        journal["status"] = "NEEDS_REPAIR"
        journal["stage"] = "NEEDS_REPAIR"
        journal["rollback_status"] = "BLOCKED_NEEDS_REPAIR"
        journal["last_error"] = redact(str(e))[:4000]
        journal["last_error_at"] = utc_now()
        write_journal(directory, journal)
        print(f"::error::Transaction NEEDS_REPAIR; không thực hiện thêm thao tác phá hủy: {redact(str(e))}", file=sys.stderr)
        raise
    except Exception as e:
        # Keep the durable lock and candidate capacity; the system must not pretend cleanup succeeded.
        try:
            _, fresh = read_journal(root, args.transaction_id)
            fresh["status"] = "ROLLBACK_PENDING"
            fresh["stage"] = "ROLLBACK_PENDING"
            fresh["rollback_status"] = "FAILED_OR_INCOMPLETE"
            fresh["last_error"] = redact(str(e))[:4000]
            fresh["last_error_at"] = utc_now()
            write_journal(directory, fresh)
        except Exception:
            pass
        print(f"::error::Transaction ROLLBACK_PENDING; giữ journal/lock để chặn lần chạy tiếp theo: {redact(str(e))}", file=sys.stderr)
        raise


def mark_preapply_needs_repair(args: argparse.Namespace, message: str) -> None:
    if getattr(args, "command", None) not in {"record-target", "record-plan"}:
        return
    operation_id = getattr(args, "transaction_id", None)
    if not operation_id:
        return
    root = Path(getattr(args, "journal_root", JOURNAL_ROOT_DEFAULT))
    try:
        directory, journal = read_journal(root, operation_id)
        if journal.get("apply_attempted"):
            return
        journal["status"] = "NEEDS_REPAIR"
        journal["stage"] = "NEEDS_REPAIR"
        journal["rollback_status"] = "PREFLIGHT_BLOCKED_NEEDS_REPAIR"
        journal["last_error"] = redact(message)[:4000]
        journal["last_error_at"] = utc_now()
        write_journal(directory, journal)
    except Exception as journal_error:
        print(f"::error::Không thể ghi preflight failure vào journal: {redact(str(journal_error))}", file=sys.stderr)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except NeedsRepair as e:
        mark_preapply_needs_repair(args, str(e))
        print(f"::error::NEEDS_REPAIR: {redact(str(e))}", file=sys.stderr)
        return 20
    except (TransactionError, RollbackPending) as e:
        print(f"::error::TRANSACTION BLOCKED: {redact(str(e))}", file=sys.stderr)
        return 21
    except Exception as e:
        print(f"::error::Unexpected transaction tool error: {redact(str(e))}", file=sys.stderr)
        return 22


if __name__ == "__main__":
    sys.exit(main())
