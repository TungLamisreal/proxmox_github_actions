# Dashboard autoscale: ba khung cho video chia đôi màn hình

Dashboard `meow-autoscale` có đúng ba panel:
1. Alert & nhật ký autoscale, full chiều rộng, cao 10 đơn vị lưới.
2. CPU từng Backend và CPU từng Frontend, đặt cạnh nhau, cao 7 đơn vị lưới.

Bảng log cuộn riêng; mỗi lần refresh đưa dòng mới nhất lên đầu. Pending/Firing vẫn lấy từ Prometheus. Log trống và lỗi nguồn log có thông báo riêng, không giả thành số alert bằng 0. CPU giữ nguyên PromQL theo quota LXC và tự cập nhật danh sách máy từ inventory. Màu đỏ đổi sang 75% đồng bộ ngưỡng scale-out. Không còn biểu đồ targets, CPU pool hoặc lịch sử trong dashboard này.

Mục tiêu quay video: màn 1920×1080 chia đôi, browser zoom 100%, cửa sổ Grafana khoảng 920×940 CSS px. CI mở Grafana thật với 5 BE + 5 FE và 200 dòng log dài để kiểm tra vị trí và cuộn. Số máy lớn hơn hoặc cửa sổ thấp hơn có thể cần chế độ kiosk; không thể đảm bảo mọi số lượng máy trên mọi màn hình. Chọn Last 15 minutes và refresh 10s khi quay.

## Thay đổi autoscale duy nhất

Hai rule BackendPoolHighCPU và FrontendPoolHighCPU đổi `> 80` thành `> 75`. Giữ `for: 10s`, điều kiện exporter UP, scale-in, Alertmanager grouping, cooldown, batching, số máy/dispatch và workflow gates. `scripts/webhook_listener.py` không đổi.

## Nguồn log

Panel riêng `meowcloud-alertlog-panel` dùng React/Grafana có sẵn, không tải script ngoài. Chỉ plugin này được cho phép chạy unsigned; không tắt HTML sanitization. Mã nguồn SystemJS nằm ngay trong repo, không có bước bundle ẩn.

Dịch vụ `meowcloud-journal` trên CT120 đọc journal của **một unit webhook do người vận hành chỉ định**, không restart hay sửa listener. Adapter chỉ hỗ trợ GET `/ready` và Loki `/loki/api/v1/query_range` với selector cố định `{service="meowcloud-webhook"}`, tối đa 500 dòng / 24 giờ, timeout journalctl 4s. Panel yêu cầu 200 dòng. Đây là adapter đọc journal có định dạng Loki, không phải Loki đầy đủ; Explore/Save & test của datasource không được hỗ trợ.

Token riêng được tạo một lần trên CT120 và đưa vào Grafana secureJsonData. Cổng 5011 bind IP quản trị CT120; firewall chỉ cho Observer truy cập. Không mở ra Internet. Token đi trong mạng quản trị HTTP hiện hữu. User dịch vụ có quyền đọc journal hệ thống nhưng HTTP API luôn cố định một unit, không nhận unit từ request.

Log chỉ có khi listener thực tế ghi stdout/stderr vào journal. Nếu listener chạy trong terminal hoặc ghi ra file riêng thì chưa thể dùng cách này; cần chọn cách thu thập phù hợp trước khi triển khai, không tự đổi cách chạy listener. Không có chức năng truy hồi log đã bị journal vacuum. Quyền xem log kế thừa anonymous Viewer của Grafana hiện tại.

## Triển khai sau khi merge — chạy thủ công từ CT120

PR không tự deploy. Chờ các workflow IaC đang chạy kết thúc. Pull main vào checkout trên CT120, rồi:

```sh
cd ansible
ansible-playbook -i /opt/meowcloud-iac/ansible/inventory.ini playbooks/06-setup-observer.yml
ansible-playbook -i /opt/meowcloud-iac/ansible/inventory.ini playbooks/28-setup-webhook-journal.yml -e webhook_journal_unit=meowcloud-webhook.service
```

Người vận hành đã xác nhận unit trên VM120 là `meowcloud-webhook.service`; đây là mặc định của playbook. Unit phải có ExecStart chứa `webhook_listener.py`. Playbook 28 kiểm tra unit trước khi cài, tạo token idempotent, cài dịch vụ đọc journal trên chính máy chạy Ansible và datasource trên Observer. Vì vậy **chỉ chạy playbook 28 từ CT120**, không từ laptop hoặc runner khác. Không đưa playbook này vào workflows 03/04.

Playbook 06 vẫn giữ các kiểm tra etcd quorum trước/sau Grafana. Nó cập nhật rule 75%, panel và dashboard; lần đầu đổi panel sẽ restart Grafana. Playbook 28 restart Grafana một lần nếu datasource thay đổi. Các lần chạy lại không thay đổi cấu hình sẽ không restart Grafana. Trước khi có datasource, panel báo nguồn log không khả dụng; CPU và Prometheus alert vẫn hoạt động.

Sau triển khai: mở dashboard, xác nhận cả ba panel nằm trong viewport, so sánh log với `journalctl -u meowcloud-webhook.service`, xác nhận Pending/Firing thay đổi và máy mới xuất hiện. Thử mất kết nối dịch vụ journal phải hiển thị lỗi riêng; không gây dispatch hay thay đổi autoscale.

## Hoàn tác

Revert PR trên repo rồi chạy playbook 06 để phục hồi dashboard và rule 80%. Playbook 28 là opt-in, không nằm trong deploy tự động. Nếu không cần log, dừng/disable `meowcloud-journal` trên CT120 và xóa datasource `webhook-journal.yaml` trên Observer rồi restart Grafana lúc bảo trì. Không dừng listener webhook. Unit cũ sau revert sẽ không allowlist plugin nữa; thư mục plugin dư không ảnh hưởng quyết định scale.
