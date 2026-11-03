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

Playbook `06-setup-observer.yml` chủ động tạo systemd drop-in `99-meowcloud-http-listen.conf` để đảm bảo cấu hình hiệu lực `GF_SERVER_HTTP_ADDR=0.0.0.0`, sau đó reload systemd, restart Grafana, kiểm tra API trên IP Observer và kiểm tra TCP 3000 từ CT120. Cách này khắc phục trường hợp dịch vụ còn dùng cấu hình loopback cũ. Sau khi chạy playbook, kiểm tra `systemctl is-active meowcloud-grafana`, `ss -lntp | grep ':3000'` (phải lắng nghe `0.0.0.0:3000`) và `curl -fsS http://192.168.150.21:3000/api/health` ngay trên Observer. Quan sát CPU pool, tổng target, target UP và alert pending hoặc firing. Đối chiếu kết quả với run Workflow 03 hoặc 04.
