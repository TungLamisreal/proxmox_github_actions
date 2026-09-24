output "backend_ips" {
  value       = [for ip in proxmox_lxc.backend[*].network[0].ip : split("/", ip)[0]]
  description = "Danh sách IP của các máy ảo Backend"
}

resource "local_file" "ansible_inventory" {
  filename = "proxmox_github_actions/ansible-iac/inventory.ini" #  ^p    ^}ng d   n file l  u tr  n con auto-vm

  content = <<-EOT
    [backend]
    ${join("\n", [for ip in proxmox_lxc.backend[*].network[0].ip : split("/", ip)[0]])}
  EOT
}
