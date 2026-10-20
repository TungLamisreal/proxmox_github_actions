# ==============================================================================
# FILE: terraform/02-tunnels.tf
# CHỨC NĂNG: Khởi tạo cụm 3 Cloudflare Tunnel Connectors (HA Ingress)
# ==============================================================================

resource "proxmox_virtual_environment_container" "tunnels" {
  count = 3

  # Rải đều 3 container trên 3 Node PVE vật lý để chống SPOF
  node_name    = var.pve_nodes[count.index % length(var.pve_nodes)]
  vm_id        = 131 + count.index
  unprivileged = true
  started      = true

  initialization {
    hostname = "tunnel-${count.index + 1}"

    # Dải IP cấp phát tĩnh: 192.168.150.51 -> 192.168.150.53
    ip_config {
      ipv4 {
        address = "192.168.150.${51 + count.index}/24"
        gateway = var.network_gateway
      }
    }

    user_account {
      keys = [trimspace(file(var.ssh_public_key_path))]
    }
  }

  operating_system {
    template_file_id = var.lxc_os_template
    type             = "debian"
  }

  # Cấu hình tài nguyên theo Bảng 3.3: 1 vCPU / 512MB RAM / 4GB Disk
  cpu { cores = 1 }
  memory { dedicated = 512 }
  disk {
    datastore_id = var.general_pool
    size         = 4
  }

  network_interface {
    name     = "eth0"
    bridge   = var.network_bridge
    firewall = true
  }

  features {
    nesting = true
  }
}

# --- CẤU HÌNH TƯỜNG LỬA CHO CỤM TUNNELS ---

resource "proxmox_virtual_environment_firewall_options" "tunnels_fw" {
  count         = 3
  node_name     = proxmox_virtual_environment_container.tunnels[count.index].node_name
  container_id  = proxmox_virtual_environment_container.tunnels[count.index].vm_id
  enabled       = true
  input_policy  = "DROP"
  output_policy = "ACCEPT"
  ipfilter      = true
}

resource "proxmox_virtual_environment_firewall_rules" "tunnels_rules" {
  count        = 3
  node_name    = proxmox_virtual_environment_container.tunnels[count.index].node_name
  container_id = proxmox_virtual_environment_container.tunnels[count.index].vm_id

  rule {
    security_group = "sg_tunnel"
    iface          = "net0"
    enabled        = true
  }
}
