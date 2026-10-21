packer {
  required_plugins {
    proxmox = {
      version = ">= 1.1.8"
      source  = "github.com/hashicorp/proxmox"
    }
  }
}

# Đọc API Secret trực tiếp từ biến môi trường của hệ điều hành
variable "proxmox_url" { default = "https://192.168.150.49:8006/api2/json" }
variable "proxmox_token_id" { default = "root@pam!iac-token" }
variable "proxmox_token_secret" { 
  type      = string
  default   = "${env("PROXMOX_API_TOKEN_SECRET")}"
  sensitive = true 
}

variable "ssh_username" { default = "root" }
variable "ssh_private_key_path" { default = "/home/github-runner/.ssh/id_ed25519" }

source "proxmox-clone" "db_kvm" {
  proxmox_url              = var.proxmox_url
  username                 = var.proxmox_token_id
  token                    = var.proxmox_token_secret
  insecure_skip_tls_verify = true

  node                     = "node1"
  # QUAN TRỌNG: Sửa id này cho khớp với id của VM phôi thô
  clone_vm_id              = 9000 
  
  vm_id                    = 9001
  template_name            = "meowcloud-db-debian12"
  template_description     = "Golden Image KVM Database (Debian 12 + Docker + QEMU Agent)"

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

  qemu_agent = true
}

build {
  sources = ["source.proxmox-clone.db_kvm"]

  # Bơm thư mục scripts vào KVM
  provisioner "file" {
    source      = "scripts"
    destination = "/tmp/packer_scripts_kvm"
  }

  provisioner "shell" {
    inline = [
      "echo '=== Cấp quyền thực thi cho các scripts ==='",
      "chmod +x /tmp/packer_scripts_kvm/*.sh"
    ]
  }

  # Chạy tuần tự 4 Script giống hệt LXC (Tái sử dụng code)
  provisioner "shell" {
    inline = [
      "echo '=== [1/4] Chạy cài đặt Base Packages ==='",
      "LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/packer_scripts_kvm/01-base-packages.sh"
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '=== [2/4] Chạy cài đặt Docker Engine ==='",
      "LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/packer_scripts_kvm/02-docker-install.sh"
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '=== [3/4] Chạy cài đặt KVM Specific (QEMU Guest Agent & Cloud-Init) ==='",
      "LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/packer_scripts_kvm/03-kvm-specific.sh"
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '=== [4/4] Dọn dẹp và Sanitize ==='",
      "LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/packer_scripts_kvm/99-cleanup-sanitize.sh"
    ]
  }

  provisioner "shell" {
    inline = [
      "rm -rf /tmp/packer_scripts_kvm"
    ]
  }
}
