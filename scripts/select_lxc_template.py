#!/usr/bin/env python3
"""Select the first usable MeowCloud LXC Docker template from 9100, 9101, 9102."""

import json
import os
import ssl
import sys
import urllib.error
import urllib.request


DEFAULT_API = "https://192.168.150.49:8006/api2/json"
CANDIDATES = (9100, 9101, 9102)
EXPECTED_DESCRIPTION = "meowcloud-ssh-bootstrap-v1"
EXPECTED_ROOTFS_PREFIX = "general_pool:"


def log(message):
    print(f"[lxc-template-selector] {message}", file=sys.stderr)


def api_get(base_url, token_id, token_secret, path):
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"PVEAPIToken={token_id}={token_secret}",
            "Accept": "application/json",
        },
        method="GET",
    )
    context = ssl._create_unverified_context()

    with urllib.request.urlopen(request, context=context, timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if "data" not in payload:
        raise RuntimeError(f"Proxmox API không trả về trường data cho {path}")

    return payload["data"]


def main():
    # Terraform external data source gửi JSON qua stdin. Lệnh chạy thủ công
    # trong workflow cũng được hỗ trợ khi stdin rỗng.
    raw = sys.stdin.read()
    if raw.strip():
        try:
            json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Input JSON không hợp lệ: {exc}") from exc

    base_url = os.environ.get("TF_VAR_proxmox_api_url") or DEFAULT_API
    token_id = os.environ.get("TF_VAR_proxmox_api_token_id") or "root@pam!iac-token"
    token_secret = os.environ.get("TF_VAR_proxmox_api_token_secret") or ""

    if not token_secret:
        raise RuntimeError("Thiếu TF_VAR_proxmox_api_token_secret.")

    resources = api_get(base_url, token_id, token_secret, "/cluster/resources?type=vm")
    nodes = api_get(base_url, token_id, token_secret, "/nodes")
    node_status = {item.get("node"): item.get("status") for item in nodes}

    for vmid in CANDIDATES:
        matches = [
            item for item in resources
            if item.get("type") == "lxc"
            and str(item.get("vmid")) == str(vmid)
        ]

        if len(matches) != 1:
            log(f"VMID {vmid}: tìm thấy {len(matches)} bản ghi; bỏ qua.")
            continue

        node = matches[0].get("node")
        if not node or node_status.get(node) != "online":
            log(f"VMID {vmid}: node {node!r} không online; bỏ qua.")
            continue

        try:
            config = api_get(
                base_url,
                token_id,
                token_secret,
                f"/nodes/{node}/lxc/{vmid}/config",
            )
        except Exception as exc:
            log(f"VMID {vmid} trên {node}: không đọc được config ({exc}); bỏ qua.")
            continue

        template_flag = config.get("template")
        is_template = (
            template_flag is True
            or str(template_flag) == "1"
        )
        description = str(config.get("description") or "").strip()
        rootfs = str(config.get("rootfs") or "")

        if not is_template:
            log(f"VMID {vmid} trên {node}: chưa phải template; bỏ qua.")
            continue

        if description != EXPECTED_DESCRIPTION:
            log(f"VMID {vmid} trên {node}: SSH-bootstrap marker không hợp lệ; bỏ qua.")
            continue

        if not rootfs.startswith(EXPECTED_ROOTFS_PREFIX):
            log(f"VMID {vmid} trên {node}: rootfs không nằm trên general_pool; bỏ qua.")
            continue

        log(f"Đã chọn VMID {vmid} trên {node}.")
        # external data source yêu cầu mọi giá trị đầu ra là chuỗi.
        print(json.dumps({"node_name": str(node), "vm_id": str(vmid)}))
        return

    raise RuntimeError(
        "Không tìm được template hợp lệ theo thứ tự 9100 -> 9101 -> 9102."
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
