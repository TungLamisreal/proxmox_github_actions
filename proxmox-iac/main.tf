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
