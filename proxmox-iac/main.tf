# khối terraform đã được cắt ra và đưa vào file provider.tf

# khối provider đã được tách ra và đưa vào file provider.tf

# khối variable "pve_nodes" đã được cắt ra và đưa vào file variables.tf

# ==========================================
# 1. HỆ THỐNG GIÁM SÁT (OBSERVER) - 160
# ==========================================
resource "proxmox_lxc" "observer" {
  target_node  = var.pve_nodes[0] # Nằm cố định ở Node 1
  vmid         = 160
  hostname     = "observer"
  ostemplate   = var.os_template
  unprivileged = true
  cores        = 2
  memory       = 2048 
  ssh_public_keys = trimspace(file("/root/.ssh/id_ed25519.pub"))
  firewall = true
  
  start  = true
  onboot = true

  features { nesting = true }

  rootfs {
    storage = "Ceph-Storage"
    size    = "15G" 
  }

  network {
    name   = "eth0"
    bridge = "vmbr0"
    ip     = "192.168.150.160/24"
    gw     = "192.168.150.2"
    firewall = true
  }

  # MỞ CỔNG QUẢN TRỊ (SSH)
  firewall {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = 22
    source = "192.168.150.1" # Chỉ cho IP quản trị / GitHub Runner vào cài đặt
  }

  # MỞ CỔNG XEM DASHBOARD CHO ADMIN
  firewall {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = "3000,9090" # 3000 (Grafana) và 9090 (Prometheus UI)
    source = "192.168.150.1" # Tránh việc ai trong LAN cũng mò được vào xem log
  }
}

# ==========================================
# 2. CỤM CỔNG BẢO MẬT (TUNNELS) - 161, 162, 163
# ==========================================
resource "proxmox_lxc" "tunnels" {
  count        = 3
  # Trò ảo thuật ở đây: rải đều 3 máy ra 3 node vật lý khác nhau
  target_node  = var.pve_nodes[count.index % length(var.pve_nodes)] 
  vmid         = 161 + count.index
  hostname     = "tunnel-${count.index + 1}"
  ostemplate   = var.os_template
  unprivileged = true
  cores        = 1
  memory       = 512
  ssh_public_keys = trimspace(file("/root/.ssh/id_ed25519.pub"))
  firewall = true

  start  = true
  onboot = true

  features { nesting = true }

  rootfs {
    storage = "Ceph-Storage"
    size    = "4G"
  }
 
  network {
    name   = "eth0"
    bridge = "vmbr0"
    ip     = "192.168.150.${161 + count.index}/24"
    gw     = "192.168.150.2"
    firewall = true
  }

  # RULE MỞ CỔNG 22 CHO ĐÚNG IP ADMIN
  firewall {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = 22
    source = "192.168.150.1"  # Ép cứng đúng IP này mới được SSH
  }
}

# ==========================================
# 3. CỤM LOAD BALANCER (HA) - 171, 172
# ==========================================
resource "proxmox_lxc" "loadbalancer" {
  count        = 2
  target_node  = var.pve_nodes[count.index % length(var.pve_nodes)] # Rải ra node 1 và 2
  vmid         = 171 + count.index
  hostname     = count.index == 0 ? "lb-master" : "lb-backup"
  ostemplate   = var.os_template
  unprivileged = true
  cores        = 1
  memory       = 1024
  ssh_public_keys = trimspace(file("/root/.ssh/id_ed25519.pub"))
  firewall = true

  start  = true
  onboot = true

  features { nesting = true }

  rootfs {
    storage = "Ceph-Storage"
    size    = "4G"
  }

  network {
    name   = "eth0"
    bridge = "vmbr0"
    ip     = "192.168.150.${171 + count.index}/24"
    gw     = "192.168.150.2"
    firewall = true
  }
 
  # Rule 1: Cho phép Ansible SSH vào cài đặt (Mở từ dải mạng GitHub Runner/Admin)
  firewall {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = 22
    source = "192.168.150.0/24" # Thay bằng var.admin_cidr
  }

  # Rule 2: Cứu sống Keepalived - Giao thức VRRP (Quan trọng nhất)
  firewall {
    action = "ACCEPT"
    type   = "in"
    proto  = "vrrp" 
  }

  # Rule 3: Đón luồng HTTP/HTTPS từ Cloudflare Tunnel
  firewall {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = "80,443"
    # source có thể giới hạn chỉ nhận từ IP của các con Tunnel
  }
}

# ==========================================
# 4. CỤM BACKEND - 181, 182, 183
# ==========================================
resource "proxmox_lxc" "backend" {
  count        = var.instance_count
  target_node  = var.pve_nodes[count.index % length(var.pve_nodes)] # Rải ra đủ 3 node
  vmid         = 181 + count.index
  hostname     = "be-${count.index + 1}"
  ostemplate   = var.os_template
  unprivileged = true
  cores        = 2
  memory       = 1024
  ssh_public_keys = trimspace(file("/root/.ssh/id_ed25519.pub"))
  firewall = true

  start  = true
  onboot = true

  features { nesting = true }

  rootfs {
    storage = "Ceph-Storage"
    size    = "4G"
  }

  network {
    name   = "eth0"
    bridge = "vmbr0"
    ip     = "192.168.150.${181 + count.index}/24"
    gw     = "192.168.150.2"
    firewall = true
  }

  # Rule 1: SSH cho Ansible
  firewall {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = 22
  }

  # Rule 2: Chỉ nhận traffic web từ cụm Load Balancer
  firewall {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = "80"
    # Bí quyết IaC: Bơm thẳng dải IP của subnet nội bộ chứa LB vào đây
    source = "192.168.150.0/24" 
  }

  # Rule 3: Mở cổng cho Grafana/Prometheus (Cổng 9100 Node Exporter)
  firewall {
    action = "ACCEPT"
    type   = "in"
    proto  = "tcp"
    dport  = 9100
  }
  
  lifecycle {
    ignore_changes = [
      target_node, 
      network      
    ]
  }
}

# ==========================================
# 5. KHO DỮ LIỆU (DATABASE) - 190
# ==========================================
