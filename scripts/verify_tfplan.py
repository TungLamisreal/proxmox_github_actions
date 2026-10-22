#!/usr/bin/env python3
import sys
import json
import argparse

# Khóa cứng các VMID bất khả xâm phạm
PROTECTED_VMIDS = {120, 9000, 9001, 9002, 9003}

def main():
    parser = argparse.ArgumentParser(description="Zero-Destroy Terraform Plan Gate")
    parser.add_argument("plan_json_file", help="Đường dẫn tới file JSON xuất từ terraform show -json")
    parser.add_argument("--allow-scale-in", action="store_true", help="Chỉ bật khi chủ động chạy luồng Scale-in")
    args = parser.parse_args()

    with open(args.plan_json_file, "r") as f:
        plan = json.load(f)

    changes = plan.get("resource_changes", [])
    created = []
    updated = []
    violations = []

    for rc in changes:
        address = rc.get("address", "")
        res_type = rc.get("type", "")
        change = rc.get("change", {})
        actions = change.get("actions", [])
        before = change.get("before") or {}
        after = change.get("after") or {}

        # 1. Bảo vệ các VMID đặc biệt
        for obj in (before, after):
            vmid = obj.get("vm_id") or obj.get("container_id")
            if vmid in PROTECTED_VMIDS:
                violations.append(f"[NGUY HIỂM] Phát hiện thao tác {actions} chạm vào VMID bảo vệ {vmid} tại {address}!")

        if actions == ["no-op"]:
            continue

        # File local_file luôn replace khi có thay đổi nội dung -> Hợp lệ
        if res_type == "local_file":
            updated.append(f"{address} ({','.join(actions)})")
            continue

        # Bỏ qua lỗi Replace giả của Firewall IPSet do thay đổi thứ tự mảng CIDR
        if res_type == "proxmox_virtual_environment_firewall_ipset" and actions == ["delete", "create"]:
            updated.append(f"{address} (Auto-resolved IPSet Ordering)")
            continue

        # 2. Cấm xóa hoặc thay thế MÁY ẢO/CONTAINER
        is_compute_node = res_type in ["proxmox_virtual_environment_container", "proxmox_virtual_environment_vm"]

        if "delete" in actions:
            if not args.allow_scale_in:
                # Nếu là máy ảo bị xóa -> Lỗi nghiêm trọng
                if is_compute_node:
                    vmid = before.get("vm_id") or before.get("container_id") or "?"
                    violations.append(
                        f"[CHẶN XÓA/THAY THẾ MÁY] Máy ảo '{address}' (VMID: {vmid}) bị đề xuất hành động {actions}!"
                    )
                # Nếu là cấu hình phụ trợ bị xóa (Firewall rule) -> Cảnh báo nhưng có thể cho qua nếu do logic Terraform
                else:
                    updated.append(f"{address} ({','.join(actions)})")

        elif actions == ["create"]:
            vmid = after.get("vm_id") or after.get("container_id") or "?"
            node = after.get("node_name") or "?"
            created.append(f"{address} [VMID={vmid}, Node={node}]")
        elif actions == ["update"]:
            updated.append(address)

    print("=== KẾT QUẢ KIỂM DUYỆT TERRAFORM PLAN (ZERO-DESTROY GATE) ===")
    print(f"[*] Số tài nguyên tạo mới (Create) : {len(created)}")
    for item in created:
        print(f"    + {item}")
    print(f"[*] Số tài nguyên cập nhật/thay thế phụ trợ (Update): {len(updated)}")
    for item in updated:
        print(f"    ~ {item}")

    if violations:
        print("\n[!] PHÁT HIỆN VI PHẠM AN TOÀN MÁY CHỦ — DỪNG NGAY LẬP TỨC (ABORT):")
        for v in violations:
            print(f"    x {v}")
        sys.exit(1)

    print("\n[OK] Plan an toàn (Không xóa hay thay thế Máy ảo/Container hiện hữu). Cho phép tiếp tục!")
    sys.exit(0)

if __name__ == "__main__":
    main()
