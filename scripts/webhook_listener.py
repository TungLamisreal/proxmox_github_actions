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

COOLDOWN_PERIOD = 300
last_trigger_time = {
    "backend": 0,
    "frontend": 0
}

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/webhook':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                payload = json.loads(post_data.decode('utf-8'))
                alerts = payload.get('alerts', [])
                print(f"[*] Nhận Webhook từ Alertmanager: {len(alerts)} alert(s)", flush=True)

                for alert in alerts:
                    status = alert.get('status')
                    labels = alert.get('labels', {})
                    alertname = labels.get('alertname', 'Unknown')
                    target = labels.get('scale_target')
                    action = labels.get('scale_action', 'scale_out')
                    autoscale_flag = labels.get('autoscale')

                    print(
                        f"    -> Alert: {alertname} | status={status} "
                        f"| action={action} | target={target} | autoscale={autoscale_flag}",
                        flush=True
                    )

                    if status == 'firing' and autoscale_flag == 'true' and target:
                        self.handle_scale_request(target, action)

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

    def handle_scale_request(self, target, action):
        global last_trigger_time
        current_time = time.time()
        time_since_last = current_time - last_trigger_time.get(target, 0)

        # Khóa Cooldown chung cho từng phân hệ (chống Spam & chống Flapping Scale-out <-> Scale-in)
        if time_since_last < COOLDOWN_PERIOD:
            print(
                f"[*] Từ chối {action.upper()} {target.upper()}: Đang trong thời gian Cooldown "
                f"({time_since_last:.0f}s / {COOLDOWN_PERIOD}s)",
                flush=True
            )
            return

        last_trigger_time[target] = current_time

        if action == "scale_in":
            workflow_id = WORKFLOW_SCALE_IN
            inputs = {
                "scale_target": target
            }
            print(f"[-] TẢI THẤP: Yêu cầu Scale-In {target.upper()} hợp lệ. Gọi {workflow_id}...", flush=True)
        else:
            workflow_id = WORKFLOW_SCALE_OUT
            inputs = {
                "scale_target": target,
                "add_backend_count": "1" if target == "backend" else "0",
                "add_frontend_count": "1" if target == "frontend" else "0",
                "ram_reserve_mb": "1280"
            }
            print(f"[!] QUÁ TẢI CPU: Yêu cầu Scale-Out {target.upper()} hợp lệ. Gọi {workflow_id}...", flush=True)

        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{workflow_id}/dispatches"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }
        data = {
            "ref": "main",
            "inputs": inputs
        }

        req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
        try:
            urllib.request.urlopen(req)
            print(f"[OK] Đã gửi lệnh Trigger {action.upper()} ({target.upper()}) tới GitHub Actions!", flush=True)
        except Exception as e:
            print(f"[ERROR] Lỗi khi gọi GitHub API: {e}", flush=True)
            last_trigger_time[target] = 0

if __name__ == '__main__':
    if not GITHUB_TOKEN:
        print("[!] Lỗi: Chưa cấu hình biến môi trường GITHUB_PAT_TOKEN.", flush=True)
        exit(1)
    server = HTTPServer(('0.0.0.0', 5000), WebhookHandler)
    print("=== MeowCloud Auto-Scale Webhook Listener (Scale-Out & Scale-In) Khởi Động ===", flush=True)
    print("Lắng nghe cảnh báo từ Alertmanager tại http://0.0.0.0:5000/webhook", flush=True)
    server.serve_forever()
