# ==============================================================================
# FILE: terraform/04-backend.tf
# CHỨC NĂNG: Khởi tạo cụm LXC Backend App (Co giãn đàn hồi qua biến backend_count)
# ==============================================================================

resource "proxmox_virtual_environment_container" "backend" {
  count = var.backend_count

  # Phân bổ rải rác qua các Node theo thuật toán chia dư (Anti-Affinity)
  node_name    = var.pve_nodes[count.index % length(var.pve_nodes)]
  vm_id        = 151 + count.index
  unprivileged = true
  started      = true

  initialization {
    hostname = "be-${count.index + 1}"
    
    # Dải IP cấp phát tự động: Bắt đầu từ 192.168.150.101
    ip_config {
      ipv4 {
        address = "192.168.150.${101 + count.index}/24"
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

  # Cấu hình tài nguyên theo Bảng 3.3: 2 vCPU / 1024MB RAM / 8GB Disk
  cpu { cores = 2 }
  memory { dedicated = 1024 }
  disk {
    datastore_id = var.general_pool
    size         = 8
  }

  network_interface {
    name     = "eth0"
    bridge   = var.network_bridge
    firewall = true
  }

  features {
    nesting = true
    keyctl  = true
  }

  # Tránh Terraform xóa máy nếu Proxmox HA tự động Live Migrate sang Node khác
  lifecycle {
    ignore_changes = [
      node_name
    ]
  }
}

# --- CẤU HÌNH TƯỜNG LỬA CHO CỤM BACKEND ---

resource "proxmox_virtual_environment_firewall_options" "backend_fw" {
  count        = var.backend_count
  node_name    = proxmox_virtual_environment_container.backend[count.index].node_name
  container_id = proxmox_virtual_environment_container.backend[count.index].vm_id
  enabled      = true
  input_policy = "DROP"
  output_policy = "ACCEPT"
  
  # Cực kỳ quan trọng cho các dải Elastic Pool: Chống giả mạo MAC/IP
  ipfilter     = true 
}

resource "proxmox_virtual_environment_firewall_rules" "backend_rules" {
  count        = var.backend_count
  node_name    = proxmox_virtual_environment_container.backend[count.index].node_name
  container_id = proxmox_virtual_environment_container.backend[count.index].vm_id

  rule {
    security_group = "sg_backend"
    iface          = "net0"
    enabled        = true
  }
}
