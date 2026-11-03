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

Playbook `06-setup-observer.yml` chủ động tạo systemd drop-in `99-meowcloud-http-listen.conf` để đảm bảo cấu hình hiệu lực `GF_SERVER_HTTP_ADDR=0.0.0.0`. Nếu override đổi thì reload systemd/restart Grafana; các lần chạy sau chỉ giữ service `started`, không restart khi Workflow 03/04 cập nhật Prometheus. Playbook kiểm tra API trên IP Observer và kiểm tra TCP 3000 từ CT120. Cách này khắc phục trường hợp dịch vụ còn dùng cấu hình loopback cũ. Sau khi chạy playbook, kiểm tra `systemctl is-active meowcloud-grafana`, `ss -lntp | grep ':3000'` (phải lắng nghe `0.0.0.0:3000`) và `curl -fsS http://192.168.150.21:3000/api/health` ngay trên Observer. Quan sát CPU pool, tổng target, target UP và alert pending hoặc firing. Đối chiếu kết quả với run Workflow 03 hoặc 04.


## Bố cục dashboard cho video Workflow 03/04

Dashboard `MeowCloud · Autoscale` nằm trong `monitoring/grafana/autoscale.json`, được Grafana file provisioning nạp tự động qua `monitoring/grafana/provider.yaml`. Đây không phải dashboard mặc định có sẵn của Grafana.

- **Hàng 1:** Hai thẻ trạng thái **Alert · PENDING** (màu cam nếu có alert) và **Alert · FIRING** (màu đỏ nếu có alert), cùng biểu đồ số lượng exporter targets; giá trị là số alert hiện đang ở trạng thái tương ứng. Trạng thái được đọc trực tiếp từ Prometheus, mặc định 0.
- **Hàng 2:** Bar gauge CPU từng Backend và từng Frontend, mỗi máy một thanh với tên `backend-01`, `frontend-01`, v.v. Màu xanh/cam/đỏ tương ứng mức CPU dưới 55%, từ 55% đến dưới 80%, và từ 80% trở lên. Hiện tối thiểu 3 BE + 3 FE và tự bổ sung BE/FE mới theo inventory khi chạy scale-out.
- **Hàng 3:** Biểu đồ CPU pool Backend và Frontend (logic autoscale đã có, giữ nguyên).
- **Hàng 4:** Hai biểu đồ lịch sử 15 phút, mỗi máy một đường, dùng để chứng minh thời điểm CPU tăng/giảm trước/sau 03/04.

PromQL CPU theo máy: `100 * sum by(machine)(rate(meowcloud_lxc_cpu_usage_seconds_total{job="lxc_backends"}[1m])) / on(machine) sum by(machine)(meowcloud_lxc_cpu_capacity_cores{job="lxc_backends"})` (tương tự `lxc_frontends`). Vì chia cho số vCPU cấp cho LXC, đây là **% CPU theo quota của mỗi LXC**, không phải tổng CPU Proxmox node.

Playbook Observer gắn label `machine` vào từng target Prometheus theo tên host trong Ansible inventory. Workflow 03 và 04 vốn đã chạy lại `06-setup-observer.yml` khi đổi danh sách máy; dashboard không cần sửa thủ công lúc scale. Nếu máy mất kết nối exporter, không coi ô CPU bị thiếu là 0%; hãy đối chiếu panel targets UP.

Sau khi merge thay đổi dashboard, chỉ cần pull code trên CT120 rồi chạy lại playbook Observer như phần đầu tài liệu. Chạy khi không có workflow autoscale đang triển khai; playbook cũng cập nhật Prometheus/Alertmanager và kiểm tra etcd quorum.

Dashboard đang được file-provisioning quản lý (`editable: false`, `allowUiUpdates: false`) và web UI chỉ bật Anonymous Viewer, do đó không kéo-thả/sắp xếp panel trực tiếp trên trình duyệt được. Cần sửa `gridPos` trong `monitoring/grafana/autoscale.json` rồi deploy qua Ansible để giữ layout ổn định giữa Workflow 01/03/04. Muốn tự chỉnh trong UI phải bật xác thực phù hợp, quyền Editor/Admin và thay đổi chính sách provisioning; không khuyến khích ngay trước buổi demo.
