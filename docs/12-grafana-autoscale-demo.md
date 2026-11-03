# Dashboard autoscale trên Observer CT121

Grafana được cài cùng Prometheus và Alertmanager qua playbook Observer hiện có. CT120 chỉ chạy Ansible.

## Lab hiện hữu

Từ checkout đúng branch trên runner CT120:

```sh
cd ansible
ansible-playbook -i /opt/meowcloud-iac/ansible/inventory.ini playbooks/06-setup-observer.yml
```

Nếu Observer đã là voter, playbook yêu cầu dịch vụ etcd đang active và cả ba endpoint khỏe trước khi cài. Sau lần cài, kể cả khi Grafana lỗi, playbook kiểm lại sức khỏe ba endpoint. Khi dựng mới bằng Workflow 01, chưa có marker etcd nên playbook cài dashboard sau khi cấu hình Prometheus và Workflow 06 sẽ dựng DCS sau đó.

Grafana chạy bằng systemd với giới hạn 512 MiB RAM và 50% một CPU, lắng nghe trên loopback cổng 3000. Mở tunnel tới Observer rồi truy cập dashboard.

```sh
ssh -N -L 127.0.0.1:13000:127.0.0.1:3000 root@192.168.150.21
```

Mở [dashboard autoscale](http://127.0.0.1:13000/d/meow-autoscale). Quan sát CPU pool, tổng target, target UP và alert pending hoặc firing. Đối chiếu kết quả với run Workflow 03 hoặc 04.
