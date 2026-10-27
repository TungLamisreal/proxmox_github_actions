#!/usr/bin/env python3
import json
import urllib.request
import os
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

GITHUB_TOKEN = os.getenv("GITHUB_PAT_TOKEN")
REPO_OWNER = os.getenv("GITHUB_REPO_OWNER", "TungLamIsReal")
REPO_NAME = os.getenv("GITHUB_REPO_NAME", "proxmox_github_actions")

WORKFLOW_SCALE_OUT = "03-autoscale-backend.yml"
WORKFLOW_SCALE_IN = "04-autoscale-in.yml"

# Giảm cooldown xuống 45 giây để khớp nhịp Ansible CI/CD siêu tốc mới
COOLDOWN_PERIOD = 45
last_trigger_time = 0

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/webhook':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                payload = json.loads(post_data.decode('utf-8'))
                alerts = payload.get('alerts', [])
                print(f"[*] Nhận Webhook Batch từ Alertmanager: {len(alerts)} alert(s)", flush=True)

                if alerts:
                    # Lấy action của alert đầu tiên (vì đã group_by scale_action)
                    action = alerts[0].get('labels', {}).get('scale_action', 'scale_out')
                    self.handle_batch_scale_request(alerts, action)

                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            except Exception as e:
                print(f"[!] Lỗi phân tích payload: {e}", flush=True)
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def handle_batch_scale_request(self, alerts, action):
        global last_trigger_time
        current_time = time.time()
        time_since_last = current_time - last_trigger_time

        if time_since_last < COOLDOWN_PERIOD:
            print(f"[*] Từ chối {action.upper()} BATCH: Đang Cooldown ({time_since_last:.0f}s / {COOLDOWN_PERIOD}s)", flush=True)
            return

        fe_firing = 0
        be_firing = 0

        # Đếm số lượng máy thực sự quá tải trong batch
        for alert in alerts:
            if alert.get('status') == 'firing' and alert.get('labels', {}).get('autoscale') == 'true':
                target = alert.get('labels', {}).get('scale_target')
                if target == 'frontend':
                    fe_firing += 1
                elif target == 'backend':
                    be_firing += 1

        if fe_firing == 0 and be_firing == 0:
            print("[-] Batch trống hoặc không có alert firing hợp lệ. Bỏ qua.")
            return

        last_trigger_time = current_time
        add_fe = 0
        add_be = 0

        if action == "scale_in":
            # Scale-in: Rất thận trọng, chỉ thu hồi 1 máy mỗi lần cho hệ thống an toàn, ưu tiên Frontend trước
            target_scale = "frontend" if fe_firing > 0 else "backend"
            workflow_id = WORKFLOW_SCALE_IN
            inputs = {"scale_target": target_scale}
            print(f"[-] TẢI THẤP: Gửi lệnh Scale-In {target_scale.upper()} (FE={fe_firing}, BE={be_firing})", flush=True)
        else:
            # Scale-out: Đẻ máy dựa trên số alert, TRẦN TỐI ĐA LÀ 3 MÁY/LẦN
            # Nếu cả FE và BE đều quá tải, chia tỷ lệ tối đa 3 máy
            total_req = fe_firing + be_firing
            if total_req <= 3:
                add_fe = fe_firing
                add_be = be_firing
            else:
                # Nếu > 3 máy báo động, ưu tiên cứu vớt đều nhau
                if fe_firing > 0 and be_firing > 0:
                    # Tình huống xấu nhất: Cứu 2 FE + 1 BE hoặc ngược lại (ưu tiên thằng nào kêu to hơn)
                    if fe_firing > be_firing:
                        add_fe = 2
                        add_be = 1
                    else:
                        add_be = 2
                        add_fe = 1
                else:
                    # Bão chỉ tập trung vào 1 hệ
                    add_fe = min(3, fe_firing)
                    add_be = min(3, be_firing)

            workflow_id = WORKFLOW_SCALE_OUT
            inputs = {
                "scale_target": "both",
                "add_backend_count": str(add_be),
                "add_frontend_count": str(add_fe),
                "ram_reserve_mb": "1280"
            }
            print(f"[!] QUÁ TẢI CPU BATCH: Yêu cầu đẻ +{add_fe} FE, +{add_be} BE. Gọi {workflow_id}...", flush=True)

        # Bắn lệnh tới GitHub API
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{workflow_id}/dispatches"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }
        data = {"ref": "main", "inputs": inputs}
        req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
        try:
            urllib.request.urlopen(req)
            print(f"[OK] Đã gửi lệnh Trigger thành công tới GitHub Actions!", flush=True)
        except Exception as e:
            print(f"[ERROR] Lỗi khi gọi GitHub API: {e}", flush=True)
            last_trigger_time = 0

if __name__ == '__main__':
    if not GITHUB_TOKEN:
        print("[!] Lỗi: Chưa cấu hình biến môi trường GITHUB_PAT_TOKEN.", flush=True)
        exit(1)
    server = HTTPServer(('0.0.0.0', 5000), WebhookHandler)
    print("=== MeowCloud Auto-Scale Webhook Listener (Batching Mode) Khởi Động ===", flush=True)
    server.serve_forever()
