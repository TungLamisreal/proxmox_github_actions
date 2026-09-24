terraform {
  # B  ^u sung 3 d  ng n  y  ^q  ^c c   t s  ^u b  ^y nh  ^{ ra ch  ^w an to  n tuy  ^gt  ^q  ^qi
  backend "local" {
    path = "/root/terraform-state/terraform.tfstate"
  }

  required_providers {
    proxmox = {
      source  = "telmate/proxmox"
      version = "3.0.2-rc10"
    }
  }
}

provider "proxmox" {
  pm_api_url          = var.proxmox_api_url
  pm_api_token_id     = var.proxmox_api_token_id
  pm_api_token_secret = var.proxmox_api_token_secret
  pm_tls_insecure     = true
}
