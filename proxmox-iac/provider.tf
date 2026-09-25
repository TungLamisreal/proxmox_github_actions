terraform {
  # Giữ nguyên cấu hình lưu state nội bộ của bác
  backend "local" {
    path = "/root/terraform-state/terraform.tfstate"
  }

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = "0.66.1" # Nâng cấp lên lõi BPG mới nhất
    }
  }
}

provider "proxmox" {
  # Cú pháp của BPG khác Telmate một chút, nó gom chung token và đổi tên biến
  endpoint  = var.proxmox_api_url
  api_token = "${var.proxmox_api_token_id}=${var.proxmox_api_token_secret}"
  insecure  = true
}
