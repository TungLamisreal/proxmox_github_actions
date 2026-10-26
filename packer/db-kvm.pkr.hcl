packer {
  required_plugins {
    proxmox = {
      version = ">= 1.1.8"
      source  = "github.com/hashicorp/proxmox"
    }
  }
}

variable "proxmox_url" { default = "https://192.168.150.49:8006/api2/json" }
variable "proxmox_token_id" { default = "root@pam!iac-token" }
variable "proxmox_token_secret" { 
  type      = string
  default   = "${env("PROXMOX_API_TOKEN_SECRET")}"
  sensitive = true 
}

variable "ssh_username" { default = "root" }
variable "ssh_private_key_path" { default = "/home/github-runner/.ssh/id_ed25519" }

source "proxmox-clone" "db_node1" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_token_id
  token                    = var.proxmox_token_secret
  insecure_skip_tls_verify = true

  node                     = "node1"
  clone_vm_id              = 9000 
  vm_id                    = 9001
  template_name            = "meowcloud-db-debian12-node1"
  template_description     = "Golden Image KVM Database Node 1"

  cloud_init_disable_upgrade_packages = true
  
  network_adapters { 
    model  = "virtio"
    bridge = "vmbr0" 
  }
  
  ipconfig { 
    ip = "dhcp" 
  }
  
  os                       = "l26"
  scsi_controller          = "virtio-scsi-single"
  full_clone               = true

  ssh_username             = var.ssh_username
  ssh_private_key_file     = var.ssh_private_key_path
  ssh_timeout              = "10m"
  qemu_agent               = true
}

source "proxmox-clone" "db_node2" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_token_id
  token                    = var.proxmox_token_secret
  insecure_skip_tls_verify = true

  node                     = "node2"
  clone_vm_id              = 9000 
  vm_id                    = 9002
  template_name            = "meowcloud-db-debian12-node2"
  template_description     = "Golden Image KVM Database Node 2"

  cloud_init_disable_upgrade_packages = true
  
  network_adapters { 
    model  = "virtio"
    bridge = "vmbr0" 
  }
  
  ipconfig { 
    ip = "dhcp" 
  }
  
  os                       = "l26"
  scsi_controller          = "virtio-scsi-single"
  full_clone               = true

  ssh_username             = var.ssh_username
  ssh_private_key_file     = var.ssh_private_key_path
  ssh_timeout              = "10m"
  qemu_agent               = true
}

source "proxmox-clone" "db_node3" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_token_id
  token                    = var.proxmox_token_secret
  insecure_skip_tls_verify = true

  node                     = "node3"
  clone_vm_id              = 9000 
  vm_id                    = 9003
  template_name            = "meowcloud-db-debian12-node3"
  template_description     = "Golden Image KVM Database Node 3"

  cloud_init_disable_upgrade_packages = true
  
  network_adapters { 
    model  = "virtio"
    bridge = "vmbr0" 
  }
  
  ipconfig { 
    ip = "dhcp" 
  }
  
  os                       = "l26"
  scsi_controller          = "virtio-scsi-single"
  full_clone               = true

  ssh_username             = var.ssh_username
  ssh_private_key_file     = var.ssh_private_key_path
  ssh_timeout              = "10m"
  qemu_agent               = true
}

build {
  sources = [
    "source.proxmox-clone.db_node1",
    "source.proxmox-clone.db_node2",
    "source.proxmox-clone.db_node3"
  ]

  provisioner "file" {
    source      = "scripts"
    destination = "/tmp/packer_scripts_kvm"
  }

  provisioner "shell" {
    inline = [
      "chmod +x /tmp/packer_scripts_kvm/*.sh",
      "LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/packer_scripts_kvm/01-base-packages.sh",
      "LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/packer_scripts_kvm/02-docker-install.sh",
      "LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/packer_scripts_kvm/03-kvm-specific.sh",
      "LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/packer_scripts_kvm/99-cleanup-sanitize.sh",
      "rm -rf /tmp/packer_scripts_kvm"
    ]
  }
}
