variable "proxmox_host" { default = "192.168.150.49" }
variable "ssh_username" { default = "root" }
variable "ssh_private_key_path" { default = "/home/github-runner/.ssh/id_ed25519" }
variable "base_tarball" { default = "shared-fs:vztmpl/meowcloud-lxc-base-debian12.tar.zst" } # đã sửa theo gpt
variable "storage_pool" { default = "general_pool" }
variable "shared_fs_path" { default = "/mnt/pve/shared-fs/template/cache" }

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
      "echo '=== [1/5] Khởi tạo LXC Build Môi trường (CTID 999) ==='",
      "pct stop 999 >/dev/null 2>&1 || true",
      "pct destroy 999 --purge >/dev/null 2>&1 || true",
      "pct create 999 ${var.base_tarball} -rootfs ${var.storage_pool}:8 -net0 name=eth0,bridge=vmbr0,ip=dhcp,firewall=0 -unprivileged 1 -features nesting=1",
      "pct start 999",
      "echo 'Chờ LXC nhận mạng...'",
      "sleep 15"
    ]
  }

  provisioner "file" {
    source      = "scripts"
    destination = "/tmp/packer_scripts_docker"
  }

  provisioner "shell" {
    inline = [
      "echo '=== [2/5] Cài đặt Locale & zstd (Force) ==='",
      "pct exec 999 -- bash -c 'echo \"LC_ALL=C.UTF-8\" >> /etc/environment && echo \"LANG=C.UTF-8\" >> /etc/environment'",
      "echo '=== [3/5] Thực thi kịch bản Provisioning ==='",
      "pct exec 999 -- mkdir -p /tmp/scripts",
      "pct push 999 /tmp/packer_scripts_docker/01-base-packages.sh /tmp/scripts/01-base-packages.sh",
      "pct push 999 /tmp/packer_scripts_docker/02-docker-install.sh /tmp/scripts/02-docker-install.sh",
      "pct push 999 /tmp/packer_scripts_docker/99-cleanup-sanitize.sh /tmp/scripts/99-cleanup-sanitize.sh",
      "pct exec 999 -- bash -c 'chmod +x /tmp/scripts/*.sh'",
      "pct exec 999 -- bash -c 'LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/scripts/02-docker-install.sh'",
      "pct exec 999 -- bash -c 'LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/scripts/99-cleanup-sanitize.sh'"
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '=== [4/5] Đóng gói thành RootFS Archive ==='",
      "pct exec 999 -- bash -c 'tar -I zstd -cpf /tmp/meowcloud-lxc-docker-debian12.tar.zst --exclude=/tmp/meowcloud-lxc-docker-debian12.tar.zst --exclude=/lost+found --exclude=/dev/* --exclude=/proc/* --exclude=/sys/* --exclude=/run/* --exclude=/mnt/* --exclude=/media/* / '"
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '=== [5/5] Lưu trữ vào CephFS và dọn dẹp ==='",
      "pct pull 999 /tmp/meowcloud-lxc-docker-debian12.tar.zst ${var.shared_fs_path}/meowcloud-lxc-docker-debian12.tar.zst",
      "pct stop 999",
      "pct destroy 999 --purge",
      "rm -rf /tmp/packer_scripts_docker"
    ]
  }
}
