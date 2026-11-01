#!/usr/bin/env python3
import os
import sys
import json
import re
import argparse
import ssl
import urllib.request

# Khóa bảo vệ cứng (Không bao giờ chạm vào các VM này)
PROTECTED_VMIDS = {120, 9000, 9001, 9002, 9003}
DEFAULT_PVE_NODES = ["node1", "node2", "node3"]

def parse_args():
    parser = argparse.ArgumentParser(description="MeowCloud Auto-Scale Node Selector")
    parser.add_argument("--add-be", type=int, default=0, help="Số lượng Backend muốn tăng thêm")
    parser.add_argument("--add-fe", type=int, default=0, help="Số lượng Frontend muốn tăng thêm")
    parser.add_argument("--ram-reserve-mb", type=int, default=1024, help="RAM dự phòng bắt buộc (MB) cho mỗi Node")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in ra kế hoạch phân bổ, không ghi đè file state")
    return parser.parse_args()

def fetch_proxmox_nodes(api_url, token_id, token_secret):
    url = f"{api_url}/nodes"
    headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, context=ctx, timeout=10) as response:
            return json.loads(response.read().decode())['data']
    except Exception as e:
        print(f"[!] Lỗi kết nối Proxmox API: {e}", file=sys.stderr)
        sys.exit(1)

def get_guest_ram_requested(tf_file, default_mb=1024):
    """Đọc file cấu hình Terraform để lấy thông số RAM thực tế của FE/BE."""
    try:
        with open(tf_file, 'r') as f:
            content = f.read()
            match = re.search(r'memory\s*\{\s*dedicated\s*=\s*(\d+)\s*\}', content)
            if match:
                return int(match.group(1))
    except FileNotFoundError:
        print(f"[-] Không tìm thấy {tf_file}, giả định RAM = {default_mb}MB", file=sys.stderr)
    return default_mb

def load_current_state(state_file):
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            return json.load(f)
    return {
        "backend_count": 3,
        "frontend_count": 3,
        "backend_node_map": {},
        "frontend_node_map": {}
    }

def count_existing_scaled_containers(state):
    """Đếm số lượng LXC sinh thêm (Auto-scale) đang được gán trên từng Node."""
    counts = {node: 0 for node in DEFAULT_PVE_NODES}
    for _, node in state.get("backend_node_map", {}).items():
        counts[node] = counts.get(node, 0) + 1
    for _, node in state.get("frontend_node_map", {}).items():
        counts[node] = counts.get(node, 0) + 1
    return counts

def main():
    args = parse_args()

    api_url = os.getenv("TF_VAR_proxmox_api_url", "https://192.168.150.49:8006/api2/json")
    token_id = os.getenv("TF_VAR_proxmox_api_token_id", "root@pam!iac-token")
    token_secret = os.getenv("TF_VAR_proxmox_api_token_secret")

    if not token_secret:
        print("[!] Thiếu biến môi trường TF_VAR_proxmox_api_token_secret", file=sys.stderr)
        sys.exit(1)

    print("=== THU THẬP THÔNG TIN TÀI NGUYÊN ===", file=sys.stderr)
    nodes_data = fetch_proxmox_nodes(api_url, token_id, token_secret)

    ram_be_mb = get_guest_ram_requested("terraform/04-backend.tf", 1024)
    ram_fe_mb = get_guest_ram_requested("terraform/05-frontend.tf", 1024)
    print(f"[*] Nhu cầu: Backend = {ram_be_mb}MB | Frontend = {ram_fe_mb}MB", file=sys.stderr)
    print(f"[*] Dự phòng Host/Ceph: {args.ram_reserve_mb}MB mỗi Node\n", file=sys.stderr)

    state = load_current_state("/opt/meowcloud-iac/state/autoscale.tfvars.json")
    scaled_counts = count_existing_scaled_containers(state)

    next_be_vmid = 151 + state["backend_count"]
    next_fe_vmid = 171 + state["frontend_count"]

    queue = []
    for _ in range(args.add_be):
        queue.append(("Backend", ram_be_mb, next_be_vmid))
        next_be_vmid += 1
    for _ in range(args.add_fe):
        queue.append(("Frontend", ram_fe_mb, next_fe_vmid))
        next_fe_vmid += 1

    if not queue:
        print("[*] Không có yêu cầu scale thêm. Thoát an toàn.", file=sys.stderr)
        sys.exit(0)

    nodes = {}
    for n in nodes_data:
        if n.get('status') == 'online':
            node_name = n['node']
            already_scaled = scaled_counts.get(node_name, 0)
            # Cộng bù 512MB cho mỗi LXC Auto-scale đã cấp trên Node (tránh ảo giác LXC mới boot ăn ít RAM RSS)
            committed_overhead_mb = already_scaled * 32.0
            nodes[node_name] = {
                "maxmem_mb": n['maxmem'] / (1024 * 1024),
                "mem_used_mb": (n['mem'] / (1024 * 1024)) + committed_overhead_mb,
                "raw_mem_mb": n['mem'] / (1024 * 1024),
                "cpu_usage": n.get('cpu', 0.0),
                "scaled_count": already_scaled
            }

    print("=== THUẬT TOÁN ĐÁNH GIÁ & CẤP PHÁT (ANTI-AFFINITY) ===", file=sys.stderr)
    allocations = []

    for task_type, ram_req, vmid in queue:
        print(f"\n>> Đang tìm Node cho {task_type} (VMID: {vmid} - Yêu cầu: {ram_req}MB)", file=sys.stderr)
        best_node = None
        best_score = -999.0

        for name in sorted(nodes.keys()):
            metrics = nodes[name]
            max_mem = metrics["maxmem_mb"]
            used_mem = metrics["mem_used_mb"]
            scaled_cnt = metrics["scaled_count"]

            ram_usable = max_mem - used_mem - args.ram_reserve_mb
            ram_after = ram_usable - ram_req

            free_cpu_ratio = max(0.0, 1.0 - metrics["cpu_usage"])
            free_ram_ratio = max(0.0, ram_usable / max_mem)

            # Phạt 0.18 điểm cho mỗi container Auto-scale đang nằm sẵn trên Node để ép rải đều (Spread)
            density_penalty = scaled_cnt * 0.18
            score = (0.55 * free_cpu_ratio) + (0.45 * free_ram_ratio) - density_penalty

            status = "ĐẠT" if ram_after >= 0 else "LOẠI (Thiếu RAM)"
            print(
                f"   - {name}: RawUsed={metrics['raw_mem_mb']:.0f}MB | EffectiveUsable={ram_usable:.0f}MB "
                f"| Sau cấp={ram_after:.0f}MB | CPU={metrics['cpu_usage']*100:.1f}% "
                f"| ScaledLXC={scaled_cnt} | Score={score:.3f} -> {status}",
                file=sys.stderr
            )

            if ram_after >= 0 and score > best_score:
                best_score = score
                best_node = name

        if not best_node:
            print(f"\n[!] TỪ CHỐI SCALE: Cụm đã cạn kiệt tài nguyên an toàn cho {task_type} VMID {vmid}!", file=sys.stderr)
            sys.exit(2)

        print(f"<< CHỌN NODE: {best_node} (Score: {best_score:.3f})", file=sys.stderr)
        allocations.append({"type": task_type, "vmid": str(vmid), "node": best_node})

        nodes[best_node]["mem_used_mb"] += ram_req
        nodes[best_node]["cpu_usage"] = min(1.0, nodes[best_node]["cpu_usage"] + 0.10)
        nodes[best_node]["scaled_count"] += 1

    print("\n=== CẬP NHẬT TRẠNG THÁI (STATE) ===", file=sys.stderr)
    for alloc in allocations:
        if alloc["type"] == "Backend":
            state["backend_count"] += 1
            state["backend_node_map"][alloc["vmid"]] = alloc["node"]
        else:
            state["frontend_count"] += 1
            state["frontend_node_map"][alloc["vmid"]] = alloc["node"]

    output_json = json.dumps(state, indent=2)

    if args.dry_run:
        print("[*] CHẾ ĐỘ DRY-RUN: In bản nháp state file:", file=sys.stderr)
        print(output_json)
    else:
        os.makedirs("/opt/meowcloud-iac/state", exist_ok=True)
        with open("/opt/meowcloud-iac/state/autoscale.tfvars.json", "w") as f:
            f.write(output_json)
        print(f"[*] Đã ghi đè {len(allocations)} máy mới vào /opt/meowcloud-iac/state/autoscale.tfvars.json", file=sys.stderr)

    sys.exit(0)

if __name__ == "__main__":
    main()
