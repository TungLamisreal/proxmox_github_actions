# Grafana cho buổi demo autoscale

Grafana chạy trên runner CT120, giới hạn 0,5 CPU và 512 MiB RAM. Nguồn dữ liệu là Prometheus trên Observer CT121 tại `192.168.150.21:9090`. Dashboard dùng hai job `lxc_backends` và `lxc_frontends` theo inventory mà 01/03/04 đã tạo. Grafana không gửi webhook hoặc lệnh scale.

## Cài đặt

- Lab mới: chọn **install_grafana** trong Workflow 01. Mặc định tắt để giữ đường provision cũ.
- Lab đang chạy: tại checkout của đúng commit trên **CT120**, bằng tài khoản runner có quyền Docker, chạy `bash monitoring/grafana/install.sh`. Cần Docker Compose v2, Python 3, cổng loopback 3000 còn trống và container truy cập được Prometheus. Script có thể chạy lại độc lập nếu datasource chưa sẵn sàng. Không cần chạy lại 01/06.
- Trên máy quay, mở SSH tunnel bằng tài khoản được phép vào CT120: `ssh -N -L 127.0.0.1:13000:127.0.0.1:3000 github-runner@192.168.150.20`. Mở [dashboard autoscale](http://127.0.0.1:13000/d/meow-autoscale). Trình duyệt có quyền Viewer qua tunnel. Nếu lab dùng tài khoản SSH khác, thay tên tài khoản trong lệnh.
- Script smoke đòi tất cả exporter BE/FE UP và truy vấn ra mẫu CPU hợp lệ. Khi cần kiểm tra lại: `python3 monitoring/grafana/check.py` trên CT120.

## Quay demo

1. Trước khi tạo tải, xác nhận đủ target BE/FE và một Primary, một replica streaming. 03/04 có gate Patroni trước khi scale. Nếu replica đang lỗi, dashboard vẫn xem được nhưng workflow scale có thể dừng tại gate.
2. Khi tạo tải bằng kịch bản demo hiện có, quan sát CPU pool (rate 1 phút), alert pending rồi firing. Scale-out cần trên 80% trong 10 giây. Alertmanager còn thời gian gom nhóm trước khi gọi webhook. Dùng run 03 và sự thay đổi số target để xác nhận kết quả apply.
3. Khi hạ tải, BE dưới 10% hoặc FE dưới 20% trong 20 giây, pool tương ứng trên 3 target và toàn bộ exporter UP mới đủ điều kiện alert scale-in. Đối chiếu run 04; khi drain, inventory và số target có thể giảm trước khi Terraform xóa LXC.
4. CT120 nằm ngoài phạm vi destroy của 02. Khi Observer bị hủy, dashboard sẽ thiếu dữ liệu đến khi Prometheus mới scrape đủ mẫu. Dừng riêng Grafana nếu cần bằng `docker compose -p meowcloud-grafana -f monitoring/grafana/compose.yaml stop`. Named volume giữ trạng thái Grafana.

Grafana CI dựng image thật và Prometheus tạm trên GitHub-hosted runner, kiểm tra dashboard, các truy vấn qua datasource, cả hai job và lần cài thứ hai. Test này không chạy webhook, Terraform hoặc Patroni.
