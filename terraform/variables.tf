# ==============================================================================
# FILE: terraform/variables.tf
# ==============================================================================

# --- 1. XÁC THỰC API & PROXMOX ---
variable "proxmox_api_url" {
  type        = string
  description = "Endpoint Proxmox API"
  default     = "https://192.168.150.49:8006/api2/json"
}

variable "proxmox_api_token_id" {
  type        = string
  description = "Token ID root@pam"
  default     = "root@pam!iac-token"
}

variable "proxmox_api_token_secret" {
  type        = string
  description = "Secret UUID của API Token"
  sensitive   = true
  default     = ""
}

# --- 2. ĐƯỜNG DẪN KHÓA SSH ---
variable "ssh_public_key_path" {
  type        = string
  description = "Đường dẫn SSH Public Key"
  default     = "/home/github-runner/.ssh/id_ed25519.pub"
}

variable "ssh_private_key_path" {
  type        = string
  description = "Đường dẫn SSH Private Key"
  default     = "/home/github-runner/.ssh/id_ed25519"
}

# --- 3. HẠ TẦNG, LƯU TRỮ & LXC GOLDEN TEMPLATES ---
variable "pve_nodes" {
  type        = list(string)
  description = "Danh sách Node PVE"
  default     = ["node1", "node2", "node3"]
}

variable "network_gateway" {
  type        = string
  description = "Gateway dải mạng 192.168.150.0/24"
  default     = "192.168.150.2"
}

variable "network_bridge" {
  type        = string
  description = "Bridge mạng"
  default     = "vmbr0"
}

variable "general_pool" {
  type        = string
  description = "Ceph Pool cho OS"
  default     = "general_pool"
}

variable "db_pool" {
  type        = string
  description = "Ceph Pool cho Data Database"
  default     = "db_pool"
}

# Image B: Dùng cho Observer, Tunnels, Load Balancers
variable "lxc_base_template" {
  type        = string
  description = "Golden Image LXC Base trên CephFS (shared-fs)"
  default     = "shared-fs:vztmpl/meowcloud-lxc-base-debian12.tar.zst"
}

# Image C: Dùng cho Backend, Frontend (Đã tích hợp Docker CE)
variable "lxc_docker_template" {
  type        = string
  description = "Golden Image LXC Docker trên CephFS (shared-fs)"
  default     = "shared-fs:vztmpl/meowcloud-lxc-docker-debian12.tar.zst"
}

# --- 4. CO GIÃN TẢI (AUTO-SCALING) ---
variable "backend_count" {
  type        = number
  description = "Số lượng Backend LXC"
  default     = 3
}

variable "frontend_count" {
  type        = number
  description = "Số lượng Frontend LXC"
  default     = 3
}

variable "db_count" {
  type        = number
  description = "Số lượng Database KVM"
  default     = 2
}

# --- 5. GOLDEN IMAGE KVM DATABASE (PHÂN TÁN ĐA NODE CHỐNG SPOF) ---
variable "vm_template_ids" {
  type        = map(number)
  description = "Ánh xạ VMID của Golden Image KVM Database theo từng Node vật lý"
  default = {
    "node1" = 9001
    "node2" = 9002
    "node3" = 9003
  }
}
