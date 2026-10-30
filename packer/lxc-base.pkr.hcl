variable "proxmox_host" { default = "192.168.150.49" }
variable "ssh_username" { default = "root" }
variable "ssh_private_key_path" { default = "/home/github-runner/.ssh/id_ed25519" }
variable "base_tarball" { default = "shared-fs:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst" }
variable "storage_pool" { default = "general_pool" }
variable "shared_fs_path" { default = "/mnt/pve/shared-fs/template/cache" }

source "null" "lxc_base" {
  communicator         = "ssh"
  ssh_host             = var.proxmox_host
  ssh_username         = var.ssh_username
  ssh_private_key_file = var.ssh_private_key_path
}

build {
  sources = ["source.null.lxc_base"]

  provisioner "shell" {
    inline = [
      "set -e",
      "echo '=== [1/5] Khởi tạo LXC Build Môi trường (CTID 998) ==='",
      "if pct config 998 >/dev/null 2>&1; then echo 'Refuse to delete existing CTID 998' >&2; exit 1; fi",
      "pct create 998 ${var.base_tarball} -rootfs ${var.storage_pool}:8 -net0 name=eth0,bridge=vmbr0,ip=dhcp,firewall=0 -unprivileged 1 -features nesting=1",
      "pct start 998",
      "echo 'Chờ LXC nhận mạng...'",
      "sleep 15"
    ]
  }

  provisioner "file" {
    source      = "scripts"
    destination = "/tmp/packer_scripts_base"
  }

  provisioner "shell" {
    inline = [
      "echo '=== [2/5] Cài đặt Locale & zstd (Force) ==='",
      "pct exec 998 -- bash -c 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zstd locales'",
      "pct exec 998 -- bash -c 'echo \"LC_ALL=C.UTF-8\" >> /etc/environment && echo \"LANG=C.UTF-8\" >> /etc/environment'",
      "echo '=== [3/5] Thực thi kịch bản Provisioning ==='",
      "pct exec 998 -- mkdir -p /tmp/scripts",
      "pct push 998 /tmp/packer_scripts_base/01-base-packages.sh /tmp/scripts/01-base-packages.sh",
      "pct push 998 /tmp/packer_scripts_base/99-cleanup-sanitize.sh /tmp/scripts/99-cleanup-sanitize.sh",
      "pct push 998 /tmp/packer_scripts_base/40-image-role-gate.sh /tmp/scripts/40-image-role-gate.sh",
      "pct exec 998 -- bash -c 'chmod +x /tmp/scripts/*.sh'",
      "pct exec 998 -- bash -c 'LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/scripts/01-base-packages.sh'",
      "pct exec 998 -- bash /tmp/scripts/40-image-role-gate.sh base",
      "pct exec 998 -- bash -c 'LC_ALL=C.UTF-8 LANG=C.UTF-8 /tmp/scripts/99-cleanup-sanitize.sh'"
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '=== [4/5] Đóng gói thành RootFS Archive ==='",
      "pct exec 998 -- bash -c 'tar -I zstd -cpf /tmp/meowcloud-lxc-base-debian12-v2.tar.zst --exclude=/tmp/meowcloud-lxc-base-debian12-v2.tar.zst --exclude=/lost+found --exclude=/dev/* --exclude=/proc/* --exclude=/sys/* --exclude=/run/* --exclude=/mnt/* --exclude=/media/* / '"
    ]
  }

  provisioner "shell" {
    inline = [
      "echo '=== [5/5] Lưu trữ vào CephFS và dọn dẹp ==='",
      "pct pull 998 /tmp/meowcloud-lxc-base-debian12-v2.tar.zst ${var.shared_fs_path}/meowcloud-lxc-base-debian12-v2.tar.zst",
      "pct stop 998",
      "pct destroy 998 --purge",
      "rm -rf /tmp/packer_scripts_base"
    ]
  }
}
