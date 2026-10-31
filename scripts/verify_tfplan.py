#!/usr/bin/env python3
import sys
import json
import argparse

# Khóa cứng các VMID bất khả xâm phạm (Auto-VM, Observer, Tunnels, LB, Base BE, Base FE, DB, Golden Templates)
PROTECTED_VMIDS = {
    120, 121,
    131, 132, 133,
    141, 142,
    151, 152, 153,
    171, 172, 173,
    271, 272,
    9000, 9001, 9002, 9003
}

def main():
    parser = argparse.ArgumentParser(description="Zero-Destroy / Controlled Scale-In Terraform Plan Gate")
    parser.add_argument("plan_json_file", help="Đường dẫn tới file JSON xuất từ terraform show -json")
    parser.add_argument("--allow-scale-in", action="store_true", help="Chỉ bật khi chủ động chạy luồng Scale-in")
    parser.add_argument("--expected-vmid", type=int, help="Exact elastic LXC VMID selected by scale_in_node.py")
    args = parser.parse_args()
    if args.allow_scale_in and args.expected_vmid is None:
        parser.error("--expected-vmid is required for controlled scale-in")

    with open(args.plan_json_file, "r") as f:
        plan = json.load(f)

    changes = plan.get("resource_changes", [])
    created = []
    deleted_compute = []
    deleted_vmids = []
    updated = []
    violations = []

    for rc in changes:
        address = rc.get("address", "")
        res_type = rc.get("type", "")
        change = rc.get("change", {})
        actions = change.get("actions", [])
        before = change.get("before") or {}
        after = change.get("after") or {}

        # 1. Bảo vệ tuyệt đối các VMID cốt lõi và Base Pool
        for obj in (before, after):
            vmid = obj.get("vm_id") or obj.get("container_id")
            if str(vmid).isdigit() and int(vmid) in PROTECTED_VMIDS and "delete" in actions and res_type in [
                "proxmox_virtual_environment_container",
                "proxmox_virtual_environment_vm"
            ]:
                violations.append(
                    f"[NGUY HIỂM] Phát hiện thao tác {actions} chạm vào VMID bảo vệ cứng {vmid} tại {address}!"
                )

        if actions == ["no-op"]:
            continue

        if res_type == "local_file":
            updated.append(f"{address} ({','.join(actions)})")
            continue

        if res_type == "proxmox_virtual_environment_firewall_ipset" and actions == ["delete", "create"]:
            updated.append(f"{address} (Auto-resolved IPSet Ordering)")
            continue

        is_compute_node = res_type in [
            "proxmox_virtual_environment_container",
            "proxmox_virtual_environment_vm"
        ]

        if "delete" in actions:
            if is_compute_node:
                vmid = before.get("vm_id") or before.get("container_id") or 0
                # Cấm tuyệt đối hành vi Replace (["delete", "create"]) máy đang chạy ở cả Scale-out lẫn Scale-in
                if "create" in actions:
                    violations.append(
                        f"[CHẶN REPLACE MÁY] '{address}' (VMID: {vmid}) bị đề xuất tạo lại {actions}!"
                    )
                elif not args.allow_scale_in:
                    violations.append(
                        f"[CHẶN XÓA MÁY] '{address}' (VMID: {vmid}) bị đề xuất xóa trong luồng Scale-out!"
                    )
                else:
                    # Kiểm tra VMID có thuộc diện Elastic Pool (BE >= 154 hoặc FE >= 174) hay không
                    is_elastic_be = (154 <= int(vmid) <= 170)
                    is_elastic_fe = (174 <= int(vmid) <= 250)
                    if is_elastic_be or is_elastic_fe:
                        deleted_compute.append(f"{address} [VMID={vmid}]")
                        deleted_vmids.append(int(vmid))
                    else:
                        violations.append(
                            f"[VI PHẠM PHẠM VI SCALE-IN] VMID {vmid} ({address}) không thuộc vùng co giãn!"
                        )
            else:
                updated.append(f"{address} ({','.join(actions)})")
        elif actions == ["create"]:
            vmid = after.get("vm_id") or after.get("container_id") or "?"
            node = after.get("node_name") or "?"
            created.append(f"{address} [VMID={vmid}, Node={node}]")
        elif actions == ["update"]:
            updated.append(address)

    # Trong 1 lượt Scale-in chỉ cho phép thu hồi đúng 1 LXC
    if args.allow_scale_in and deleted_vmids != [args.expected_vmid]:
        violations.append(f"[SAI VMID] Expected exactly one LXC VMID {args.expected_vmid}, actual deleted VMIDs: {deleted_vmids}")

    if args.allow_scale_in and created:
        violations.append(f"[SCALE-IN CREATE] Unexpected creates during scale-in: {created}")

    if args.allow_scale_in and len(deleted_compute) > 1:
        violations.append(
            f"[CHẶN XÓA HÀNG LOẠT] Phát hiện {len(deleted_compute)} LXC bị xóa cùng lúc ({deleted_compute}). Chỉ cho phép thu hồi 1 máy/lượt!"
        )

    print("=== KẾT QUẢ KIỂM DUYỆT TERRAFORM PLAN (GUARDRAIL GATE) ===")
    print(f"[*] Số tài nguyên tạo mới (Create)               : {len(created)}")
    for item in created:
        print(f"    + {item}")
    print(f"[*] Số LXC đàn hồi thu hồi hợp lệ (Scale-In)      : {len(deleted_compute)}")
    for item in deleted_compute:
        print(f"    - {item}")
    print(f"[*] Số tài nguyên cập nhật/phụ trợ (Update/FW)    : {len(updated)}")
    for item in updated:
        print(f"    ~ {item}")

    if violations:
        print("\n[!] PHÁT HIỆN VI PHẠM AN TOÀN MÁY CHỦ — DỪNG NGAY LẬP TỨC (ABORT):")
        for v in violations:
            print(f"    x {v}")
        sys.exit(1)

    print("\n[OK] Plan hợp lệ và vượt qua toàn bộ chốt chặn bảo vệ!")
    sys.exit(0)

if __name__ == "__main__":
    main()
