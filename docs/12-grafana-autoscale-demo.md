# Dashboard autoscale trên Observer CT121

Grafana được cài cùng Prometheus và Alertmanager qua playbook Observer hiện có. CT120 chỉ chạy Ansible.

## Lab hiện hữu

Từ checkout đúng branch trên runner CT120:

```sh
cd ansible
ansible-playbook -i /opt/meowcloud-iac/ansible/inventory.ini playbooks/06-setup-observer.yml
```

Nếu Observer đã là voter, playbook yêu cầu dịch vụ etcd đang active và cả ba endpoint khỏe trước khi cài. Sau lần cài, kể cả khi Grafana lỗi, playbook kiểm lại sức khỏe ba endpoint. Khi dựng mới bằng Workflow 01, chưa có marker etcd nên playbook cài dashboard sau khi cấu hình Prometheus và Workflow 06 sẽ dựng DCS sau đó.

Grafana chạy bằng systemd với giới hạn 512 MiB RAM và 50% một CPU, lắng nghe trên cổng TCP 3000. Truy cập trực tiếp từ mạng quản trị qua [Grafana Observer](http://192.168.150.21:3000/d/meow-autoscale) (không cần SSH tunnel).

Firewall `sg_observer` phải cho phép TCP 3000 **chỉ từ IP/mạng quản trị tin cậy**. Grafana mặc định bật anonymous Viewer, nên **không mở cổng 3000 ra Internet hoặc mạng không tin cậy**. Nếu sử dụng ngoài mạng quản trị, phải cấu hình xác thực và TLS phù hợp.

Sau khi chạy playbook `06-setup-observer.yml`, kiểm tra `systemctl is-active meowcloud-grafana` và `curl -fsS http://192.168.150.21:3000/api/health` ngay trên Observer. Quan sát CPU pool, tổng target, target UP và alert pending hoặc firing. Đối chiếu kết quả với run Workflow 03 hoặc 04.
