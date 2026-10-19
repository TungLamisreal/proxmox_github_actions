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

# --- 3. HẠ TẦNG & LƯU TRỮ ---
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

variable "lxc_os_template" {
  type        = string
  description = "Template Debian 12 trên shared-fs"
  default     = "shared-fs:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst"
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

# --- 5. BIẾN LƯU ID CÁC MÁY ẢO ---
variable "vm_template_id" {
  type        = number
  description = "VMID của máy ảo Debian 12 Cloud-Init Template trên Proxmox"
  default     = 9000 # Thay bằng VMID Template thực tế của bạn (nếu dùng Clone)
}
