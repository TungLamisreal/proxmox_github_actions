#!/usr/bin/env python3
import json
import urllib.request
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

# ======================================================================
# CẤU HÌNH GITHUB API (Đọc từ biến môi trường của systemd)
# ======================================================================
GITHUB_TOKEN = os.getenv("GITHUB_PAT_TOKEN")
REPO_OWNER = os.getenv("GITHUB_REPO_OWNER", "TungLamIsReal")
REPO_NAME = os.getenv("GITHUB_REPO_NAME", "proxmox_github_actions")
WORKFLOW_ID = "03-autoscale-backend.yml"

# ======================================================================
# BỘ NHỚ COOLDOWN (Chống Spam Webhook từ Alertmanager)
# ======================================================================
# Thiết lập thời gian khóa (giây) sau khi vừa trigger luồng scale
COOLDOWN_PERIOD = 300
last_trigger_time = {
    "backend": 0,
    "frontend": 0
}

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        # Chỉ nhận đường dẫn /webhook
        if self.path == '/webhook':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)

            try:
                payload = json.loads(post_data.decode('utf-8'))

                # Duyệt qua các alert đang ở trạng thái 'firing' (báo động)
                alerts = payload.get('alerts', [])
                for alert in alerts:
                    if alert.get('status') == 'firing':
                        labels = alert.get('labels', {})

                        # Chỉ xử lý nếu alert có nhãn autoscale="true"
                        if labels.get('autoscale') == 'true':
                            target = labels.get('scale_target')  # 'backend' hoặc 'frontend'
                            if target:
                                self.handle_scale_request(target)

                # Trả lời 200 OK cho Alertmanager
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            except Exception as e:
                print(f"[!] Lỗi phân tích payload: {e}")
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def handle_scale_request(self, target):
        global last_trigger_time
        current_time = time.time()
        time_since_last = current_time - last_trigger_time.get(target, 0)

        # Nếu chưa qua thời gian Cooldown (5 phút), từ chối scale
        if time_since_last < COOLDOWN_PERIOD:
            print(f"[*] Từ chối Scale {target.upper()}: Đang trong thời gian Cooldown ({time_since_last:.0f}s / {COOLDOWN_PERIOD}s)", flush=True)
            return

        print(f"[!] QUÁ TẢI CPU: Yêu cầu Scale {target.upper()} hợp lệ. Bắt đầu gọi GitHub Actions...", flush=True)

        # Cập nhật mốc thời gian vừa trigger
        last_trigger_time[target] = current_time

        # Gọi GitHub API
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{WORKFLOW_ID}/dispatches"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }

        # Payload truyền vào file 03-autoscale-backend.yml
        data = {
            "ref": "main",
            "inputs": {
                "scale_target": target,
                "add_backend_count": "1" if target == "backend" else "0",
                "add_frontend_count": "1" if target == "frontend" else "0",
                "ram_reserve_mb": "2048"
            }
        }

        req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
        try:
            urllib.request.urlopen(req)
            print(f"[OK] Đã gửi lệnh Trigger Scale-out {target.upper()} tới GitHub Actions!")
        except Exception as e:
            print(f"[ERROR] Lỗi khi gọi GitHub API: {e}")
            # Nếu gọi lỗi, reset cooldown để thử lại ngay khi có webhook kế tiếp
            last_trigger_time[target] = 0

if __name__ == '__main__':
    if not GITHUB_TOKEN:
        print("[!] Lỗi: Chưa cấu hình biến môi trường GITHUB_PAT_TOKEN.")
        exit(1)

    server = HTTPServer(('0.0.0.0', 5000), WebhookHandler)
    print("=== MeowCloud Auto-Scale Webhook Listener Khởi Động ===")
    print("Lắng nghe cảnh báo từ Alertmanager tại http://0.0.0.0:5000/webhook")
    server.serve_forever()
