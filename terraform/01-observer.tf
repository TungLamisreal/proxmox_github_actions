# ==============================================================================
# FILE: terraform/01-observer.tf
# CHỨC NĂNG: Khởi tạo LXC Observer (Giám sát Prometheus/Grafana)
# ==============================================================================

resource "proxmox_virtual_environment_container" "observer" {
  node_name    = var.pve_nodes[0]
  vm_id        = 121
  unprivileged = true
  started      = true
  # Let the Proxmox node stagger booting non-HA guests.
  # HA-managed Observer is started by PVE HA Manager instead.
  start_on_boot = true

  initialization {
    hostname = "observer"
    ip_config {
      ipv4 {
        address = "192.168.150.21/24"
        gateway = var.network_gateway
      }
    }
    user_account {
      keys = [trimspace(file(var.ssh_public_key_path))]
    }
  }

  operating_system {
    template_file_id = var.lxc_observer_template
    type             = "debian"
  }

  cpu { cores = 2 }
  memory { dedicated = 2048 }
  disk {
    datastore_id = var.general_pool
    size         = 15
  }

  network_interface {
    name     = "eth0"
    bridge   = var.network_bridge
    firewall = true
  }

  features {
    nesting = true
  }

  # Observer can be HA-restarted on another node; do not force node1 on reapply.
  lifecycle {
    ignore_changes = [node_name]
  }
}

# --- CẤU HÌNH ZERO TRUST FIREWALL ---

# 1. Bật tường lửa cho CT 121: input_policy và output_policy
resource "proxmox_virtual_environment_firewall_options" "observer_fw" {
  node_name     = proxmox_virtual_environment_container.observer.node_name
  container_id  = proxmox_virtual_environment_container.observer.vm_id
  enabled       = true
  input_policy  = "DROP"
  output_policy = "ACCEPT"
  ipfilter      = true
}

# 2. Gắn nhóm bảo mật sg_observer vào card mạng net0
resource "proxmox_virtual_environment_firewall_rules" "observer_rules" {
  node_name    = proxmox_virtual_environment_container.observer.node_name
  container_id = proxmox_virtual_environment_container.observer.vm_id

  rule {
    security_group = "sg_observer"
    iface          = "net0"
    enabled        = true
  }
}

# --- CẤU HÌNH IPFILTER NET0 CHO OBSERVER ---
# Khai báo IP tĩnh để Proxmox Firewall không chặn (Anti-Spoofing)
resource "proxmox_virtual_environment_firewall_ipset" "observer_ipfilter_net0" {
  node_name    = proxmox_virtual_environment_container.observer.node_name
  container_id = proxmox_virtual_environment_container.observer.vm_id
  name         = "ipfilter-net0"
  comment      = "Allowed IP for Observer net0"

  cidr {
    name    = "192.168.150.21"
    comment = "Observer IPv4"
  }

  depends_on = [proxmox_virtual_environment_container.observer]
}
