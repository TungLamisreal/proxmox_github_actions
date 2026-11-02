#!/usr/bin/env python3
"""MeowCloud stable PostgreSQL writer endpoint state.

No PostgreSQL, Patroni, etcd or Docker changes. Explicit --activate changes
only CT120's protected meowcrud.json, and only after the caller's verified
DB HA health gate. Never dump password or authentication token.
"""
import argparse
import json
import os
from pathlib import Path
import tempfile

STATE = Path("/opt/meowcloud-iac/state/meowcrud.json")
WRITER_VIP = "192.168.150.100"
OLD_BOOTSTRAP_DB = "192.168.150.71"


def validate(config, *, require_vip=False):
    if not isinstance(config, dict):
        raise ValueError("Invalid CRUD state")
    if (config.get("initialized") is not True or
            config.get("db_name") != "meowcloud_demo" or
            config.get("db_user") != "meowcrud" or
            config.get("db_port") != 5432 or
            not config.get("db_password") or not config.get("demo_token")):
        raise ValueError("CRUD bootstrap state is missing or unexpected")
    allowed = {WRITER_VIP} if require_vip else {WRITER_VIP, OLD_BOOTSTRAP_DB}
    if config.get("db_host") not in allowed:
        raise ValueError("Unexpected PostgreSQL writer endpoint")


def activate(path=STATE):
    # Preserve every secret and other config field byte-for-byte in value.
    state = Path(path)
    config = json.loads(state.read_text(encoding="utf-8"))
    validate(config)
    if config["db_host"] == WRITER_VIP:
        print("PASS: DB writer VIP state already active")
        return False
    # Atomic replacement in same directory with private permissions.
    fd, name = tempfile.mkstemp(prefix=".meowcrud-vip-", dir=state.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            config["db_host"] = WRITER_VIP
            json.dump(config, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, state)
        state.chmod(0o600)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print("PASS: app database endpoint published as 192.168.150.100:5432")
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path, default=STATE)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--activate", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    if args.check:
        validate(json.loads(args.state.read_text(encoding="utf-8")), require_vip=True)
        print("PASS: application state points to 192.168.150.100:5432")
        return
    if args.confirm != "ACTIVATE_DB_WRITER_VIP":
        parser.error("Explicit --confirm ACTIVATE_DB_WRITER_VIP required")
    activate(args.state)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        # Never emit parsed state or raw secrets from unexpected input files.
        raise SystemExit(f"DB HA VIP state gate failed: {type(exc).__name__}") from None
