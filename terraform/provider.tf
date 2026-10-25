# ==============================================================================
# FILE: terraform/provider.tf
# CHỨC NĂNG: Khởi tạo kết nối Proxmox API và SSH
# ==============================================================================

terraform {
  required_version = ">= 1.5.0"

  required_providers {
    proxmox = {
      source  = "bpg/proxmox"
      version = ">= 0.66.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5.0"
    }
  }

  backend "local" {
    path = "/opt/meowcloud-iac/state/terraform.tfstate"
  }
}

provider "proxmox" {
  endpoint  = var.proxmox_api_url
  api_token = "${var.proxmox_api_token_id}=${var.proxmox_api_token_secret}"
  insecure  = true

  ssh {
    agent       = false
    username    = "root"
    private_key = file(var.ssh_private_key_path)
  }
}
