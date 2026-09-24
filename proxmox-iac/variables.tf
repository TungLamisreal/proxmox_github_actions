variable "proxmox_api_url" {
  type = string
}

variable "proxmox_api_token_id" {
  type = string
}

variable "proxmox_api_token_secret" {
  type = string
  sensitive = true # Khai báo sensitive để Terraform giấu nó đi, không in ra màn hình console
}

variable "os_template" {
  type    = string
  default = "local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst"
}

variable "pve_nodes" {
  type    = list(string)
  default = ["node1", "pve2", "pve3"] # Thay bằng tên node thực tế của bạn
}

variable "instance_count" {
  type = number
}
