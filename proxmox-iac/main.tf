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

resource "proxmox_lxc" "backend_node" {
  count  = 3 

  target_node  = "node1"
  hostname     = "BE-${count.index + 1}"
  vmid         = 111 + count.index
  ostemplate   = "local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst"
  unprivileged = true
  start        = true
  password     = "123123"
  
  ssh_public_keys = trimspace(file("/root/.ssh/id_ed25519.pub"))
  
  cores  = 1
  memory = 512
  swap   = 512

  rootfs {
    storage = "local-lvm"
    size    = "8G"
  }

  network {
    name     = "eth0"
    bridge   = "vmbr0"
    ip       = "192.168.150.${150 + count.index}/24"
    gw       = "192.168.150.2"
    firewall = false
  }
}
