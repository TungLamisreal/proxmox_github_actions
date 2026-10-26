variable "proxmox_host" { default = "192.168.150.49" }
variable "ssh_username" { default = "root" }
variable "ssh_private_key_path" { default = "/home/github-runner/.ssh/id_ed25519" }
variable "ssh_public_key_path" { default = "/home/github-runner/.ssh/id_ed25519.pub" }
variable "base_tarball" { default = "shared-fs:vztmpl/meowcloud-lxc-base-debian12.tar.zst" }
variable "storage_pool" { default = "general_pool" }
variable "target_vmid" { default = 9100 }

source "null" "lxc_docker" {
  communicator         = "ssh"
  ssh_host             = var.proxmox_host
  ssh_username         = var.ssh_username
  ssh_private_key_file = var.ssh_private_key_path
}

build {
  sources = ["source.null.lxc_docker"]

  provisioner "shell" {
    inline = [
      "echo '=== [1/3] Khởi tạo LXC 9100 trực tiếp trên Ceph RBD ==='",
      "pct stop ${var.target_vmid} >/dev/null 2>&1 || true",
      "pct destroy ${var.target_vmid} --purge >/dev/null 2>&1 || true",
      "pct create ${var.target_vmid} ${var.base_tarball} -rootfs ${var.storage_pool}:8 -memory 1024 -cores 2 -net0 name=eth0,bridge=vmbr0,ip=dhcp,firewall=1 -unprivileged 1 -features nesting=1 -hostname meowcloud-lxc-docker-tpl",
      "pct start ${var.target_vmid}",
      "sleep 15"
    ]
  }

  # Copy Scripts & Public Key lên Proxmox
  provisioner "file" {
    source      = "scripts"
    destination = "/tmp/packer_scripts_docker"
  }
  provisioner "file" {
    source      = "${var.ssh_public_key_path}"
    destination = "/tmp/runner_id_ed25519.pub"
  }

  provisioner "shell" {
    inline = [
      "echo '=== [2/3] Thực thi cài đặt Docker & Kiểm chứng cấu hình SSH ==='",
      "pct exec ${var.target_vmid} -- mkdir -p /tmp/scripts",
      "pct push ${var.target_vmid} /tmp/packer_scripts_docker/02-docker-install.sh /tmp/scripts/02-docker-install.sh",
      "pct push ${var.target_vmid} /tmp/packer_scripts_docker/99-cleanup-sanitize.sh /tmp/scripts/99-cleanup-sanitize.sh",
      "pct push ${var.target_vmid} /tmp/runner_id_ed25519.pub /tmp/runner_id_ed25519.pub",
      "pct exec ${var.target_vmid} -- bash -c 'chmod +x /tmp/scripts/*.sh'",
      
      "pct exec ${var.target_vmid} -- bash -c 'LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/scripts/02-docker-install.sh'",
      
      "echo '=> Nhúng Public SSH Key từ Runner...'",
      "pct exec ${var.target_vmid} -- mkdir -p /root/.ssh",
      "pct exec ${var.target_vmid} -- mv /tmp/runner_id_ed25519.pub /root/.ssh/authorized_keys",
      "pct exec ${var.target_vmid} -- chmod 700 /root/.ssh",
      "pct exec ${var.target_vmid} -- chmod 600 /root/.ssh/authorized_keys",
      "pct exec ${var.target_vmid} -- chown -R root:root /root/.ssh",

      "echo '=> TEST: Đảm bảo SSH service hoạt động trơn tru TRƯỚC KHI đóng gói...'",
      "pct exec ${var.target_vmid} -- systemctl daemon-reload",
      "pct exec ${var.target_vmid} -- systemctl restart ssh",
      "pct exec ${var.target_vmid} -- systemctl is-active ssh",
      "pct exec ${var.target_vmid} -- ss -lntp | grep :22",

      "echo '=> Sanitize: Dọn dẹp vân tay SSH để Linked Clone tự sinh khóa độc lập...'",
      "pct exec ${var.target_vmid} -- bash -c 'LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/scripts/99-cleanup-sanitize.sh'"
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '=== [3/3] Tắt máy và Convert thành Template 9100 ==='",
      "pct stop ${var.target_vmid}",
      "pct template ${var.target_vmid}",
      "rm -rf /tmp/packer_scripts_docker"
    ]
  }
}
