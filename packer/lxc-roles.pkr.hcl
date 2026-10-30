variable "proxmox_host" { default = "192.168.150.49" }
variable "ssh_username" { default = "root" }
variable "ssh_private_key_path" { default = "/home/github-runner/.ssh/id_ed25519" }
variable "base_tarball" { default = "shared-fs:vztmpl/meowcloud-lxc-base-debian12-v2.tar.zst" }
variable "storage_pool" { default = "general_pool" }
variable "shared_fs_path" { default = "/mnt/pve/shared-fs/template/cache" }
source "null" "lxc_roles" {
  communicator = "ssh"
  ssh_host = var.proxmox_host
  ssh_username = var.ssh_username
  ssh_private_key_file = var.ssh_private_key_path
}
build {
  sources = ["source.null.lxc_roles"]
  provisioner "file" {
    source = "scripts"
    destination = "/tmp/packer_scripts_roles"
  }
  provisioner "shell" {
    inline = [
      "set -e",
      "bash /tmp/packer_scripts_roles/build-role-archives.sh '${var.base_tarball}' '${var.storage_pool}' '${var.shared_fs_path}'",
      "rm -rf /tmp/packer_scripts_roles"
    ]
  }
}
