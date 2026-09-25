output "backend_ips" {
  value       = [for ip in proxmox_virtual_environment_container.backend[*].initialization[0].ip_config[0].ipv4[0].address : split("/", ip)[0]]
  description = "Danh sách IP của các máy ảo Backend"
}

resource "local_file" "ansible_inventory" {
  filename = "../ansible-iac/inventory.ini"

  # Nhét toàn bộ tâm huyết của bác vào đây để Terraform tự động sinh ra file chuẩn chỉnh
  content = <<-EOT
    [observer]
    192.168.150.160

    [tunnels]
    ${join("\n", [for ip in proxmox_virtual_environment_container.tunnels[*].initialization[0].ip_config[0].ipv4[0].address : split("/", ip)[0]])}

    [loadbalancers]
    ${join("\n", [for ip in proxmox_virtual_environment_container.loadbalancer[*].initialization[0].ip_config[0].ipv4[0].address : split("/", ip)[0]])}

    [backends]
    ${join("\n", [for ip in proxmox_virtual_environment_container.backend[*].initialization[0].ip_config[0].ipv4[0].address : split("/", ip)[0]])}

    [database]
    192.168.150.190

    [all:vars]
    ansible_user=root
    ansible_ssh_private_key_file=/root/.ssh/id_ed25519
    ansible_ssh_common_args='-o StrictHostKeyChecking=no'

    [proxmox_project:children]
    observer
    tunnels
    loadbalancers
    backends
  EOT
}
