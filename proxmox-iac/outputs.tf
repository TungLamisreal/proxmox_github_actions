output "backend_ips" {
  value       = proxmox_lxc.backend[*].default_ipv4_address
  description = "Danh sách IP của các máy ảo Backend"
}

resource "local_file" "ansible_inventory" {
  filename = "proxmox_github_actions/ansible-iac/inventory.ini" #  ^p    ^}ng d   n file l  u tr  n con auto-vm

  content = <<-EOT
    [backend]
    ${join("\n", proxmox_lxc.backend[*].default_ipv4_address)}
  EOT
}
