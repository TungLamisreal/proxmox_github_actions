#!/usr/bin/env python3
import os
import re
import sys
import json
import argparse

MIN_BACKEND_COUNT = 3
MIN_FRONTEND_COUNT = 3
STATE_FILE = "/opt/meowcloud-iac/state/autoscale.tfvars.json"
INVENTORY_FILE = "/opt/meowcloud-iac/ansible/inventory.ini"

def parse_args():
    parser = argparse.ArgumentParser(description="MeowCloud Scale-In LIFO Selector & Pre-Drainer")
    parser.add_argument("--target", choices=["backend", "frontend"], required=True, help="Phân hệ cần thu hồi (backend | frontend)")
    parser.add_argument("--dry-run", action="store_true", help="Chỉ in kế hoạch thu hồi, không sửa file")
    return parser.parse_args()

def main():
    args = parse_args()

    if not os.path.exists(STATE_FILE):
        print(f"[!] Không tìm thấy {STATE_FILE}. Hệ thống đang ở mức cơ sở (3 LXC).", file=sys.stderr)
        sys.exit(3)

    with open(STATE_FILE, "r") as f:
        state = json.load(f)

    be_count = state.get("backend_count", 3)
    fe_count = state.get("frontend_count", 3)

    if args.target == "backend":
        if be_count <= MIN_BACKEND_COUNT:
            print(f"[*] TỪ CHỐI SCALE-IN: Backend đang ở mức sàn tối thiểu ({be_count}/{MIN_BACKEND_COUNT}).", file=sys.stderr)
            sys.exit(3)

        retiring_index = be_count - 1
        retiring_vmid = str(151 + retiring_index)
        retiring_ip = f"192.168.150.{101 + retiring_index}"
        retiring_host = f"backend-0{retiring_index + 1}"
        retiring_port = 5000
        retiring_node = state.get("backend_node_map", {}).get(retiring_vmid, "unknown")

        state["backend_count"] = be_count - 1
        state.get("backend_node_map", {}).pop(retiring_vmid, None)

    else:
        if fe_count <= MIN_FRONTEND_COUNT:
            print(f"[*] TỪ CHỐI SCALE-IN: Frontend đang ở mức sàn tối thiểu ({fe_count}/{MIN_FRONTEND_COUNT}).", file=sys.stderr)
            sys.exit(3)

        retiring_index = fe_count - 1
        retiring_vmid = str(171 + retiring_index)
        retiring_ip = f"192.168.150.{171 + retiring_index}"
        retiring_host = f"frontend-0{retiring_index + 1}"
        retiring_port = 80
        retiring_node = state.get("frontend_node_map", {}).get(retiring_vmid, "unknown")

        state["frontend_count"] = fe_count - 1
        state.get("frontend_node_map", {}).pop(retiring_vmid, None)

    print("=== KẾ HOẠCH THU HỒI TÀI NGUYÊN (SCALE-IN LIFO) ===", file=sys.stderr)
    print(f"[-] Phân hệ       : {args.target.upper()}", file=sys.stderr)
    print(f"[-] Máy thu hồi   : {retiring_host} (VMID: {retiring_vmid} | IP: {retiring_ip}:{retiring_port} | Node: {retiring_node})", file=sys.stderr)
    print(f"[-] Số lượng mới  : Backend={state['backend_count']} | Frontend={state['frontend_count']}", file=sys.stderr)

    result_meta = {
        "target": args.target,
        "retiring_vmid": retiring_vmid,
        "retiring_ip": retiring_ip,
        "retiring_host": retiring_host,
        "retiring_port": retiring_port,
        "retiring_node": retiring_node
    }

    if args.dry_run:
        print("[*] CHẾ ĐỘ DRY-RUN:", file=sys.stderr)
        print(json.dumps(result_meta))
        sys.exit(0)

    # 1. Ghi file autoscale.tfvars.json mới
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)
    print(f"[*] Đã cập nhật {STATE_FILE}", file=sys.stderr)

    # 2. Rút trước IP máy sắp thu hồi khỏi ansible/inventory.ini để phục vụ bước Graceful Draining
    if os.path.exists(INVENTORY_FILE):
        with open(INVENTORY_FILE, "r") as f:
            inv_lines = f.readlines()
        filtered_lines = [
            line for line in inv_lines
            if not re.search(rf"\b{re.escape(retiring_ip)}\b", line)
        ]
        with open(INVENTORY_FILE, "w") as f:
            f.writelines(filtered_lines)
        print(f"[*] Đã rút {retiring_host} ({retiring_ip}) khỏi {INVENTORY_FILE} để chuẩn bị Drain kết nối.", file=sys.stderr)

    # In metadata JSON ra stdout để GitHub Actions đọc
    print(json.dumps(result_meta))
    sys.exit(0)

if __name__ == "__main__":
    main()
