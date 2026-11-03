#!/usr/bin/env python3
"""Fail-closed workflow 06 completion gate.

GitHub Actions may treat a branch with deliberately skipped seed jobs as
successful even if downstream HA jobs were silently skipped via implicit
success(). Compare actual job results with the contract for the selected
mode; do not accept a green run merely because every executed job passed.

No Proxmox, PostgreSQL, etcd or Terraform mutations.
"""
import json
import os
import sys


class GateError(RuntimeError):
    pass


REQUIRED = {
    "bootstrap-ha": (
        "preflight", "verify", "etcd-bootstrap", "etcd-verify", "prepare",
        "takeover", "health", "hba", "proxy", "writer-smoke",
        "activate-vip", "final-smoke",
    ),
    "health": ("gate", "health", "hba", "writer-smoke"),
    "adopt-vip": (
        "gate", "health", "hba", "writer-smoke", "activate-vip", "final-smoke",
    ),
    "gateway-repair": ("gateway-repair",),
    "resume": ("gate", "resume"),
    "diagnose": ("diagnose",),
}
SEED_JOBS = ("backup", "primary", "standby")


def validate(mode, needs):
    if mode not in REQUIRED:
        raise GateError(f"Unknown workflow 06 mode: {mode!r}")
    if not isinstance(needs, dict):
        raise GateError("Invalid GitHub needs JSON")

    def result(job):
        value = needs.get(job)
        if not isinstance(value, dict):
            raise GateError(f"Missing GitHub job dependency: {job}")
        status = value.get("result")
        if status not in ("success", "failure", "cancelled", "skipped"):
            raise GateError(f"Unknown job result for {job}: {status!r}")
        return status

    for job in needs:
        status = result(job)
        if status in ("failure", "cancelled"):
            raise GateError(f"Job {job} ended with {status}; refusing green run")

    for job in REQUIRED[mode]:
        status = result(job)
        if status != "success":
            raise GateError(f"{mode} incomplete: required job {job} is {status}")

    if mode == "bootstrap-ha":
        preflight = needs["preflight"]
        outputs = preflight.get("outputs", {})
        if not isinstance(outputs, dict):
            raise GateError("Missing preflight outputs")
        seed = outputs.get("bootstrap_required")
        if seed not in ("true", "false"):
            raise GateError(f"Invalid bootstrap_required={seed!r}; refusing green")
        wanted = "success" if seed == "true" else "skipped"
        for job in SEED_JOBS:
            status = result(job)
            if status != wanted:
                raise GateError(
                    f"Physical replication branch mismatch: {job}={status}, "
                    f"expected {wanted} when bootstrap_required={seed}"
                )

    print(f"[PASS] workflow 06 mode={mode}: all required job outcomes verified")


def main():
    try:
        mode = os.environ["MODE"]
        needs = json.loads(os.environ["NEEDS_JSON"])
        validate(mode, needs)
    except (GateError, KeyError, ValueError, TypeError) as exc:
        print(f"[STOP] workflow 06 completion gate: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
