# ==============================================================================
# FILE: terraform/05-frontend.tf
# CHỨC NĂNG: Khởi tạo cụm LXC Web Frontend (Linked Clone siêu tốc từ Template 9100)
# ==============================================================================
resource "proxmox_virtual_environment_container" "frontend" {
  count = var.frontend_count

  # Ưu tiên lấy Node do Python chỉ định trong frontend_node_map;
  # Các máy gốc (171-173) chưa có trong map sẽ rải đều qua phép chia dư
  node_name = lookup(
    var.frontend_node_map,
    tostring(171 + count.index),
    var.pve_nodes[count.index % length(var.pve_nodes)]
  )

  vm_id        = 171 + count.index
  unprivileged = true
  started      = true
  # Let the Proxmox node stagger booting non-HA guests.
  # HA-managed Observer is started by PVE HA Manager instead.
  start_on_boot = true

  # --- LINKED CLONE TỪ GOLDEN TEMPLATE 9100 TRÊN CEPH RBD ---
  # Đã xóa datastore_id để fix lỗi HTTP 500
  clone {
    node_name = data.external.lxc_template.result.node_name
    vm_id     = tonumber(data.external.lxc_template.result.vm_id)
    full      = false
  }

  initialization {
    hostname = "fe-${count.index + 1}"

    # Dải IP cấp phát tự động: Bắt đầu từ 192.168.150.171
    ip_config {
      ipv4 {
        address = "192.168.150.${171 + count.index}/24"
        gateway = var.network_gateway
      }
    }
  }

  cpu {
    cores = 1
  }

  memory {
    dedicated = 1024
  }

  network_interface {
    name     = "eth0"
    bridge   = var.network_bridge
    firewall = true
  }

  features {
    nesting = true
  }

  lifecycle {
    ignore_changes = [
      node_name,
      clone
    ]
  }
}

# --- CẤU HÌNH TƯỜNG LỬA CHO CỤM FRONTEND ---
resource "proxmox_virtual_environment_firewall_options" "frontend_fw" {
  count         = var.frontend_count
  node_name     = proxmox_virtual_environment_container.frontend[count.index].node_name
  container_id  = proxmox_virtual_environment_container.frontend[count.index].vm_id
  enabled       = true
  input_policy  = "DROP"
  output_policy = "ACCEPT"
  ipfilter      = true
}

resource "proxmox_virtual_environment_firewall_rules" "frontend_rules" {
  count        = var.frontend_count
  node_name    = proxmox_virtual_environment_container.frontend[count.index].node_name
  container_id = proxmox_virtual_environment_container.frontend[count.index].vm_id

  rule {
    security_group = "sg_frontend"
    iface          = "net0"
    enabled        = true
  }
}

resource "proxmox_virtual_environment_firewall_ipset" "fe_ipfilter_net0" {
  count        = var.frontend_count
  node_name    = proxmox_virtual_environment_container.frontend[count.index].node_name
  container_id = proxmox_virtual_environment_container.frontend[count.index].vm_id
  name         = "ipfilter-net0"
  comment      = "Allowed guest IPv4 for Frontend net0"

  cidr {
    name    = "192.168.150.${171 + count.index}"
    comment = "frontend-0${count.index + 1} net0 IPv4"
  }

  depends_on = [proxmox_virtual_environment_container.frontend]
}
