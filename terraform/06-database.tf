# ==============================================================================
# 06-DATABASE.TF - CỤM KVM DATABASE PRIMARY/STANDBY (2 Ổ ĐĨA PHÂN TÁCH CEPH)
# Quy hoạch Bảng 3.3 & 3.4: 2 Cores, 2048MB RAM (No Ballooning), OS general_pool + 32GB db_pool
# ==============================================================================

resource "proxmox_virtual_environment_vm" "db" {
  count       = var.db_count
  name        = "db-0${count.index + 1}"
  description = "MeowCloud Database KVM (${count.index == 0 ? "Primary" : "Standby"})"
  tags        = ["meowcloud", "database", "stateful"]

  # Anti-Affinity: Rải đều Primary lên node2, Standby lên node3
  node_name = var.pve_nodes[(count.index + 1) % length(var.pve_nodes)]

  # Quy hoạch VMID 271 - 272 (tránh đụng CTID 171+ của Frontend)
  vm_id = 271 + count.index

  on_boot = true
  started = true

  # Bật QEMU Guest Agent phục vụ đóng băng I/O (fsfreeze) khi PBS sao lưu
  agent {
    enabled = true
    timeout = "15m"
  }

  # Nhân bản cục bộ từ Golden Image KVM (9001/9002/9003) ngay trên Node đích (Chống SPOF)
  clone {
    node_name = var.pve_nodes[(count.index + 1) % length(var.pve_nodes)]
    vm_id     = var.vm_template_ids[var.pve_nodes[(count.index + 1) % length(var.pve_nodes)]]
    full      = true
  }

  # Tận dụng tập lệnh phần cứng VT-x/AMD-V
  cpu {
    cores   = 2
    sockets = 1
    type    = "host"
  }

  # Vô hiệu hóa Memory Ballooning (floating = 0) để bảo vệ DB khỏi OOM
  memory {
    dedicated = 2048
    floating  = 0
  }

  # Khởi tạo Controller VirtIO SCSI Single
  scsi_hardware = "virtio-scsi-single"

  # Ổ đĩa 1 (OS Disk): 10GB trên general_pool
  disk {
    datastore_id = var.general_pool
    interface    = "scsi0"
    size         = 10
    file_format  = "raw"
    iothread     = true
    ssd          = true
    discard      = "on"
  }

  # Ổ đĩa 2 (Data Disk): 32GB trên db_pool
  disk {
    datastore_id = var.db_pool
    interface    = "scsi1"
    size         = 32
    file_format  = "raw"
    iothread     = true
    ssd          = true
    discard      = "on"
    backup       = true
  }

  # Mạng gắn vào vmbr0, kích hoạt Tường lửa phân tán
  network_device {
    bridge   = var.network_bridge
    model    = "virtio"
    firewall = true
  }

  # Cloud-Init: Cấp IP tĩnh (.71 - .72) và nhúng Public SSH Key
  initialization {
    datastore_id = var.general_pool
    interface    = "ide2"

    dns {
      servers = ["1.1.1.1", "8.8.8.8"]
    }

    ip_config {
      ipv4 {
        address = "192.168.150.${71 + count.index}/24"
        gateway = var.network_gateway
      }
    }

    user_account {
      username = "root"
      keys     = [trimspace(file(var.ssh_public_key_path))]
    }
  }

  operating_system {
    type = "l26"
  }
}

# ==============================================================================
# FIREWALL: THIẾT LẬP BỘ LỌC MAC/IP & GẮN NHÓM BẢO MẬT (sg_db)
# ==============================================================================

resource "proxmox_virtual_environment_firewall_options" "db_fw_opts" {
  count     = var.db_count
  node_name = proxmox_virtual_environment_vm.db[count.index].node_name
  vm_id     = proxmox_virtual_environment_vm.db[count.index].vm_id

  enabled       = true
  ipfilter      = true
  macfilter     = true
  dhcp          = false
  input_policy  = "DROP"
  output_policy = "ACCEPT"
}

resource "proxmox_virtual_environment_firewall_rules" "db_fw_rules" {
  count     = var.db_count
  node_name = proxmox_virtual_environment_vm.db[count.index].node_name
  vm_id     = proxmox_virtual_environment_vm.db[count.index].vm_id

  rule {
    security_group = "sg_db"
    iface          = "net0"
    enabled        = true
  }
}

resource "proxmox_virtual_environment_firewall_ipset" "db_ipfilter_net0" {
  count     = var.db_count
  node_name = var.pve_nodes[(count.index + 1) % length(var.pve_nodes)]
  vm_id     = 271 + count.index
  name      = "ipfilter-net0"
  comment   = "CR-MEOWCLOUD-DB-001: Allowed guest IPv4 for net0"

  cidr {
    name    = "192.168.150.${71 + count.index}"
    comment = "db-0${count.index + 1} net0 IPv4"
  }

  depends_on = [proxmox_virtual_environment_vm.db]
}
