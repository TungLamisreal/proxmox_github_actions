#!/usr/bin/env python3
"""Resolve or publish an explicitly selected immutable MeowCloud demo release.

Does not connect to Proxmox, Docker or PostgreSQL. Workflow owns rollout and health gates.
"""
import argparse
import json
import os
import pathlib
import re
import tempfile

V1_SHA = "cd32cd5031cc37096fc64d0507d9328ae2329f32"
PREFIX = "ghcr.io/tunglamisreal/proxmox_github_actions/"
IMAGE = re.compile(r"^" + re.escape(PREFIX) + r"(backend|frontend)@sha256:[0-9a-f]{64}$")
SHA = re.compile(r"^[0-9a-f]{40}$")
BASELINE = {"backend_image": "meow-backend:latest",
            "frontend_image": "nginx:alpine", "version": ""}


def select(target, directory):
    if target == "baseline":
        return dict(BASELINE)
    if target not in ("v1", "v2"):
        raise ValueError("Expected baseline, v1 or v2")
    path = directory / ("app-release-" + target + ".json")
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"backend_image", "frontend_image", "version"}:
        raise ValueError("Release manifest has missing or unexpected keys")
    for kind in ("backend", "frontend"):
        image = data[kind + "_image"]
        if not isinstance(image, str) or not IMAGE.fullmatch(image) or (
                "/"+kind+"@" not in image):
            raise ValueError("Invalid immutable "+kind+" image")
    version = data["version"]
    if not isinstance(version, str) or not SHA.fullmatch(version):
        raise ValueError("Release requires full 40-character commit SHA")
    if target == "v1" and version != V1_SHA:
        raise ValueError("v1 checkpoint does not match pinned known-good commit")
    if target == "v2" and version == V1_SHA:
        raise ValueError("v2 cannot point to the v1 commit")
    return data


def atomic_write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".demo-release-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(payload, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["resolve", "publish"])
    parser.add_argument("--target", required=True, choices=["baseline", "v1", "v2"])
    parser.add_argument("--state-dir", default="/opt/meowcloud-iac/state")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    directory = pathlib.Path(args.state_dir)
    release = select(args.target, directory)
    if args.action == "resolve":
        print(json.dumps(release, sort_keys=True))
        return
    if args.confirm != "PUBLISH-VERIFIED-RELEASE":
        parser.error("Publishing requires an explicit verified-rollout confirmation")
    atomic_write(directory / "app-release.json", release)
    print("PASS: published validated " + args.target + " release after successful rollout")


if __name__ == "__main__":
    main()
