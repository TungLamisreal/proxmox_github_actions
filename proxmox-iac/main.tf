terraform {
  required_providers {
    proxmox = {
      source  = "telmate/proxmox"
      version = "3.0.2-rc10"
    }
  }
}

provider "proxmox" {
  pm_api_url          = "https://192.168.150.10:8006/api2/json"
  pm_api_token_id     = "root@pam!terraform"
  pm_api_token_secret = "fed223eb-1d17-4708-a9ad-cc5607ce31c6"
  pm_tls_insecure     = true
}

variable "pve_nodes" {
  type    = list(string)
  default = ["node1", "pve2", "pve3"] # <--- THAY TÊN 3 NODE CỦA BẠN VÀO ĐÂY
}

# ==========================================
# 1. HỆ THỐNG GIÁM SÁT (OBSERVER) - 160
# ==========================================
resource "proxmox_lxc" "observer" {
  target_node  = var.pve_nodes[0] # Nằm cố định ở Node 1
  vmid         = 160
  hostname     = "observer"
  ostemplate   = "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst"
  unprivileged = true
  cores        = 2
  memory       = 2048 
  ssh_public_keys = trimspace(file("/root/.ssh/id_ed25519.pub"))

  features { nesting = true }

  rootfs {
    storage = "local-lvm"
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
  ostemplate   = "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst"
  unprivileged = true
  cores        = 1
  memory       = 512
  ssh_public_keys = trimspace(file("/root/.ssh/id_ed25519.pub"))

  features { nesting = true }

  rootfs {
    storage = "local-lvm"
    size    = "4G" ��
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
  ostemplate   = "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst"
  unprivileged = true
  cores        = 1
  memory       = 1024
  ssh_public_keys = trimspace(file("/root/.ssh/id_ed25519.pub"))

  features { nesting = true }

  rootfs {
    storage = "local-lvm"
    size    = "4G" ��
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
variable "backend_count" {
  default = 3 
}

resource "proxmox_lxc" "backend" {
  count        = var.backend_count
  target_node  = var.pve_nodes[count.index % length(var.pve_nodes)] # Rải ra đủ 3 node
  vmid         = 181 + count.index
  hostname     = "be-${count.index + 1}"
  ostemplate   = "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst"
  unprivileged = true
  cores        = 2
  memory       = 1024
  ssh_public_keys = trimspace(file("/root/.ssh/id_ed25519.pub"))

  features { nesting = true }

  rootfs {
    storage = "local-lvm"
    size    = "4G" ��
  }

  network {
    name   = "eth0"
    bridge = "vmbr0"
    ip     = "192.168.150.${181 + count.index}/24"
    gw     = "192.168.150.2"
  }
}

# ==========================================
# 5. KHO DỮ LIỆU (DATABASE) - 190
# ==========================================
