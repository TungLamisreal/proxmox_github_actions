# khối terraform đã được cắt ra và đưa vào file provider.tf

# khối provider đã được tách ra và đưa vào file provider.tf

# khối variable "pve_nodes" đã được cắt ra và đưa vào file variables.tf

# ==========================================
# 1. HỆ THỐNG GIÁM SÁT (OBSERVER) - 160
# ==========================================
resource "proxmox_virtual_environment_container" "observer" {
  node_name    = var.pve_nodes[0]
  vm_id        = 160
  unprivileged = true
  started      = true

  initialization {
    hostname = "observer"
    ip_config {
      ipv4 {
        address = "192.168.150.160/24"
        gateway = "192.168.150.2"
      }
    }
    user_account {
      keys = [trimspace(file("/root/.ssh/id_ed25519.pub"))]
    }
  }

  operating_system {
    template_file_id = var.os_template
    type             = "debian" # BPG yêu cầu khai báo loại HĐH (chuẩn nhất cho template ubuntu)
  }

  cpu { cores = 2 }
  memory { dedicated = 2048 }
  disk {
    datastore_id = "Ceph-Storage"
    size         = 15
  }

  network_interface {
    name     = "eth0"
    bridge   = "vmbr0"
    firewall = true # Công tắc 1
  }
  features { nesting = true }
}

# Bật công tắc 2 (Cầu dao tổng Firewall) cho Observer
resource "proxmox_virtual_environment_firewall_options" "observer_fw" {
  node_name    = proxmox_virtual_environment_container.observer.node_name
  container_id = proxmox_virtual_environment_container.observer.vm_id
  enable       = true
}

# Gắn luật lẻ cho Observer (Vì cụm này không dùng Security Group)
resource "proxmox_virtual_environment_firewall_rules" "observer_rules" {
  node_name    = proxmox_virtual_environment_container.observer.node_name
  container_id = proxmox_virtual_environment_container.observer.vm_id

  rule {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = "22"
    source = "192.168.150.1"
    enable = true
  }
  rule {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = "3000,9090"
    source = "192.168.150.1"
    enable = true
  }
}

# ==========================================
# 2. CỤM CỔNG BẢO MẬT (TUNNELS) - 161, 162, 163
# ==========================================
resource "proxmox_virtual_environment_container" "tunnels" {
  count        = 3
  node_name    = var.pve_nodes[count.index % length(var.pve_nodes)] 
  vm_id        = 161 + count.index
  unprivileged = true
  started      = true

  initialization {
    hostname = "tunnel-${count.index + 1}"
    ip_config {
      ipv4 {
        address = "192.168.150.${161 + count.index}/24"
        gateway = "192.168.150.2"
      }
    }
    user_account { keys = [trimspace(file("/root/.ssh/id_ed25519.pub"))] }
  }

  operating_system {
    template_file_id = var.os_template
    type             = "debian"
  }

  cpu { cores = 1 }
  memory { dedicated = 512 }
  disk { 
    datastore_id = "Ceph-Storage"
    size         = 4 
  }

  network_interface {
    name     = "eth0"
    bridge   = "vmbr0"
    firewall = true
  }
  features { nesting = true }
}

resource "proxmox_virtual_environment_firewall_options" "tunnels_fw" {
  count        = 3
  node_name    = proxmox_virtual_environment_container.tunnels[count.index].node_name
  container_id = proxmox_virtual_environment_container.tunnels[count.index].vm_id
  enable       = true
}

# ỐP SECURITY GROUP VÀO TUNNELS
resource "proxmox_virtual_environment_firewall_rules" "tunnels_rules" {
  count        = 3
  node_name    = proxmox_virtual_environment_container.tunnels[count.index].node_name
  container_id = proxmox_virtual_environment_container.tunnels[count.index].vm_id

  rule {
    security_group = "sg_tunnel" # Gọi đúng tên SG bác đã tạo
    enable         = true
  }
}

# ==========================================
# 3. CỤM LOAD BALANCER (HA) - 171, 172
# ==========================================
resource "proxmox_virtual_environment_container" "loadbalancer" {
  count        = 2
  node_name    = var.pve_nodes[count.index % length(var.pve_nodes)]
  vm_id        = 171 + count.index
  unprivileged = true
  started      = true

  initialization {
    hostname = count.index == 0 ? "lb-master" : "lb-backup"
    ip_config {
      ipv4 {
        address = "192.168.150.${171 + count.index}/24"
        gateway = "192.168.150.2"
      }
    }
    user_account { keys = [trimspace(file("/root/.ssh/id_ed25519.pub"))] }
  }

  operating_system {
    template_file_id = var.os_template
    type             = "debian"
  }

  cpu { cores = 1 }
  memory { dedicated = 1024 }
  disk { 
    datastore_id = "Ceph-Storage"
    size         = 4 
  }

  network_interface {
    name     = "eth0"
    bridge   = "vmbr0"
    firewall = true
  }
  features { nesting = true }
}

resource "proxmox_virtual_environment_firewall_options" "lb_fw" {
  count        = 2
  node_name    = proxmox_virtual_environment_container.loadbalancer[count.index].node_name
  container_id = proxmox_virtual_environment_container.loadbalancer[count.index].vm_id
  enable       = true
}

# ỐP SECURITY GROUP VÀO LOAD BALANCER
resource "proxmox_virtual_environment_firewall_rules" "lb_rules" {
  count        = 2
  node_name    = proxmox_virtual_environment_container.loadbalancer[count.index].node_name
  container_id = proxmox_virtual_environment_container.loadbalancer[count.index].vm_id

  rule {
    security_group = "sg_loadbalancer"
    enable         = true
  }
}

# ==========================================
# 4. CỤM BACKEND - 181, 182, 183
# ==========================================
resource "proxmox_virtual_environment_container" "backend" {
  count        = var.instance_count
  node_name    = var.pve_nodes[count.index % length(var.pve_nodes)]
  vm_id        = 181 + count.index
  unprivileged = true
  started      = true

  initialization {
    hostname = "be-${count.index + 1}"
    ip_config {
      ipv4 {
        address = "192.168.150.${181 + count.index}/24"
        gateway = "192.168.150.2"
      }
    }
    user_account { keys = [trimspace(file("/root/.ssh/id_ed25519.pub"))] }
  }

  operating_system {
    template_file_id = var.os_template
    type             = "debian"
  }

  cpu { cores = 2 }
  memory { dedicated = 1024 }
  disk { 
    datastore_id = "Ceph-Storage"
    size         = 4 
  }

  network_interface {
    name     = "eth0"
    bridge   = "vmbr0"
    firewall = true
  }
  features { nesting = true }

  # Cập nhật Lifecycle cho hợp chuẩn BPG
  lifecycle {
    ignore_changes = [
      node_name, 
      network_interface,
      initialization[0].ip_config
    ]
  }
}

resource "proxmox_virtual_environment_firewall_options" "backend_fw" {
  count        = var.instance_count
  node_name    = proxmox_virtual_environment_container.backend[count.index].node_name
  container_id = proxmox_virtual_environment_container.backend[count.index].vm_id
  enable       = true
}

# ỐP SECURITY GROUP VÀO BACKEND
resource "proxmox_virtual_environment_firewall_rules" "backend_rules" {
  count        = var.instance_count
  node_name    = proxmox_virtual_environment_container.backend[count.index].node_name
  container_id = proxmox_virtual_environment_container.backend[count.index].vm_id

  rule {
    security_group = "sg_backend"
    enable         = true
  }
}

# ==========================================
# 5. KHO DỮ LIỆU (DATABASE) - 190 (Chờ update sau)
# ==========================================


