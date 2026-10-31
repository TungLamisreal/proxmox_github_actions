#!/usr/bin/env python3
"""Atomically publish a release manifest only after successful rolling deployment."""
import argparse
import json
import os
import re
import tempfile

IMAGE_PATTERN = re.compile(r"^ghcr\.io/[a-z0-9_.-]+/[a-z0-9_./-]+@sha256:[a-f0-9]{64}$")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", default="/opt/meowcloud-iac/state/app-release.json")
    parser.add_argument("--backend", required=True)
    parser.add_argument("--frontend", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    for image in (args.backend, args.frontend):
        if not IMAGE_PATTERN.fullmatch(image):
            parser.error("Expected immutable, lowercase GHCR sha256 digest reference")
    if not re.fullmatch(r"[a-f0-9]{40}", args.version):
        parser.error("Expected full Git commit SHA")
    parent = os.path.dirname(os.path.abspath(args.state))
    os.makedirs(parent, exist_ok=True)
    payload = {"backend_image": args.backend, "frontend_image": args.frontend, "version": args.version}
    fd, path = tempfile.mkstemp(prefix=".app-release-", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            os.fchmod(output.fileno(), 0o600)
            json.dump(payload, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(path, args.state)
    finally:
        if os.path.exists(path):
            os.unlink(path)

if __name__ == "__main__":
    main()
