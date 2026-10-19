# ==============================================================================
# FILE: terraform/03-loadbalancer.tf
# CHỨC NĂNG: Khởi tạo cụm 2 Nginx Load Balancers (Master / Backup)
# ==============================================================================

resource "proxmox_virtual_environment_container" "loadbalancer" {
  count = 2

  # Phân tách 2 node LB sang 2 node PVE vật lý khác nhau
  node_name    = var.pve_nodes[count.index % length(var.pve_nodes)]
  vm_id        = 141 + count.index
  unprivileged = true
  started      = true

  initialization {
    hostname = count.index == 0 ? "lb-master" : "lb-backup"

    # Dải IP cấp phát tĩnh: 192.168.150.61 và 192.168.150.62
    ip_config {
      ipv4 {
        address = "192.168.150.${61 + count.index}/24"
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
    keyctl  = true
  }
}

# --- CẤU HÌNH TƯỜNG LỬA CHO CỤM LOAD BALANCER ---

resource "proxmox_virtual_environment_firewall_options" "lb_fw" {
  count        = 2
  node_name    = proxmox_virtual_environment_container.loadbalancer[count.index].node_name
  container_id = proxmox_virtual_environment_container.loadbalancer[count.index].vm_id
  enabled      = true
  input_policy = "DROP"
  output_policy = "ACCEPT"
  ipfilter     = true
}

resource "proxmox_virtual_environment_firewall_rules" "lb_rules" {
  count        = 2
  node_name    = proxmox_virtual_environment_container.loadbalancer[count.index].node_name
  container_id = proxmox_virtual_environment_container.loadbalancer[count.index].vm_id

  rule {
    security_group = "sg_lb"
    iface          = "net0"
    enabled        = true
  }
}
