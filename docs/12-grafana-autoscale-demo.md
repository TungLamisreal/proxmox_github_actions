# Dashboard autoscale trên Observer CT121

Trong Workflow 01 trên branch của PR, chọn **grafana-only** cho lab hiện hữu hoặc **provision-with-grafana** cho lab mới. CT120 chạy Ansible. Grafana chạy bằng systemd trên CT121 và đọc Prometheus tại localhost:9090. Cài riêng trên lab hiện hữu yêu cầu cả ba endpoint etcd khỏe trước khi cài và kiểm lại sau lần cài. Cài Day-0 trước Workflow 06 bỏ qua bước etcd nếu chưa có thư mục DCS.

```sh
gh workflow run 01-provision-infra.yml -R TungLamisreal/proxmox_github_actions --ref fix/db07-safe-patroni-ha-20261103 -f operation=grafana-only
```

Mở SSH tunnel tới Observer bằng tài khoản SSH được phép. Nếu phải đi qua CT120, thêm tùy chọn ProxyJump theo cấu hình SSH của lab.

```sh
ssh -N -L 127.0.0.1:13000:127.0.0.1:3000 root@192.168.150.21
```

Mở [dashboard autoscale](http://127.0.0.1:13000/d/meow-autoscale). Kiểm CPU pool, tổng target và target UP, trạng thái pending/firing và run 03/04. Hai workflow scale cần replica streaming. Workflow 02 xóa CT121 cùng dữ liệu Grafana cục bộ. Bản dashboard trong Git được provision lại khi dựng mới.
