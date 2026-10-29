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

COOLDOWN_SCALE_OUT = 50          # Cooldown giữa 2 đợt Scale-Out
COOLDOWN_SCALE_IN = 70           # Cooldown giữa 2 đợt Scale-In
SCALE_OUT_PRIORITY_LOCK = 120    # Khóa cấm Scale-In trong 120s sau khi vừa Scale-Out

last_scale_out_time = 0
last_scale_in_time = 0

# Bộ nhớ đệm ngắn hạn (30s) để nếu FE và BE báo lệch nhau vài giây thì tự gộp chung, không đè mất nhau
recent_overload = {
    "frontend": {"count": 0, "ts": 0},
    "backend": {"count": 0, "ts": 0}
}
MERGE_WINDOW = 75

class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == '/webhook':
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            try:
                payload = json.loads(post_data.decode('utf-8'))
                alerts = payload.get('alerts', [])
                print(f"[*] Nhận Webhook từ Alertmanager: {len(alerts)} alert(s)", flush=True)

                if alerts:
                    self.process_alerts(alerts)

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

    def process_alerts(self, alerts):
        global last_scale_out_time, last_scale_in_time, recent_overload
        now = time.time()

        fe_out, be_out = 0, 0
        fe_in, be_in = 0, 0

        for alert in alerts:
            if alert.get('status') != 'firing':
                continue
            labels = alert.get('labels', {})
            if labels.get('autoscale') != 'true':
                continue

            target = labels.get('scale_target')
            action = labels.get('scale_action', 'scale_out')

            if action == 'scale_out':
                if target == 'frontend':
                    fe_out += 1
                elif target == 'backend':
                    be_out += 1
            elif action == 'scale_in':
                if target == 'frontend':
                    fe_in += 1
                elif target == 'backend':
                    be_in += 1

        # ---------------------------------------------------------------------
        # 1. XỬ LÝ SCALE-OUT (LUÔN ĐƯỢC ƯU TIÊN TUYỆT ĐỐI)
        # ---------------------------------------------------------------------
        if fe_out > 0 or be_out > 0:
            if fe_out > 0:
                prev_fe = recent_overload["frontend"]["count"] if (now - recent_overload["frontend"]["ts"] <= MERGE_WINDOW) else 0
                recent_overload["frontend"] = {"count": max(prev_fe, fe_out), "ts": now}
            if be_out > 0:
                prev_be = recent_overload["backend"]["count"] if (now - recent_overload["backend"]["ts"] <= MERGE_WINDOW) else 0
                recent_overload["backend"] = {"count": max(prev_be, be_out), "ts": now}

            # Gộp cả FE và BE nếu cả hai cùng báo quá tải trong cửa sổ 30s gần nhất
            merged_fe = recent_overload["frontend"]["count"] if (now - recent_overload["frontend"]["ts"] <= MERGE_WINDOW) else 0
            merged_be = recent_overload["backend"]["count"] if (now - recent_overload["backend"]["ts"] <= MERGE_WINDOW) else 0

            since_last_out = now - last_scale_out_time
            if since_last_out < COOLDOWN_SCALE_OUT:
                print(
                    f"[*] Từ chối SCALE-OUT (FE={merged_fe}, BE={merged_be}): "
                    f"Đang Cooldown ({since_last_out:.0f}s / {COOLDOWN_SCALE_OUT}s)",
                    flush=True
                )
                return

            # Tính toán số máy đẻ thêm (Trần tối đa: Tổng FE + BE <= 3)
            if merged_fe + merged_be <= 3:
                add_fe, add_be = merged_fe, merged_be
            else:
                if merged_fe > 0 and merged_be > 0:
                    if merged_fe >= merged_be:
                        add_fe, add_be = 2, 1
                    else:
                        add_fe, add_be = 1, 2
                else:
                    add_fe = min(3, merged_fe)
                    add_be = min(3, merged_be)

            last_scale_out_time = now
            # Reset bộ đếm sau khi đã chốt đơn
            recent_overload["frontend"]["count"] = 0
            recent_overload["backend"]["count"] = 0

            inputs = {
                "scale_target": "both",
                "add_backend_count": str(add_be),
                "add_frontend_count": str(add_fe),
                "ram_reserve_mb": "1280"
            }
            print(f"[!] QUÁ TẢI CPU (BATCH): Gửi lệnh Scale-Out +{add_fe} FE, +{add_be} BE...", flush=True)
            if not self.dispatch_github(WORKFLOW_SCALE_OUT, inputs):
                last_scale_out_time = 0
            return

        # ---------------------------------------------------------------------
        # 2. XỬ LÝ SCALE-IN (CHỈ CHẠY KHI KHÔNG CÓ SCALE-OUT TRONG 120 GIÂY QUA)
        # ---------------------------------------------------------------------
        if fe_in > 0 or be_in > 0:
            since_last_out = now - last_scale_out_time
            if since_last_out < SCALE_OUT_PRIORITY_LOCK:
                print(
                    f"[*] CHẶN SCALE-IN: Hệ thống vừa Scale-Out {since_last_out:.0f}s trước "
                    f"(< {SCALE_OUT_PRIORITY_LOCK}s). Bảo vệ luồng Scale-Out không bị đè!",
                    flush=True
                )
                return

            since_last_in = now - last_scale_in_time
            if since_last_in < COOLDOWN_SCALE_IN:
                print(
                    f"[*] Từ chối SCALE-IN: Đang Cooldown ({since_last_in:.0f}s / {COOLDOWN_SCALE_IN}s)",
                    flush=True
                )
                return

            target_scale = "frontend" if fe_in > 0 else "backend"
            last_scale_in_time = now
            inputs = {"scale_target": target_scale}
            print(f"[-] TẢI THẤP: Gửi lệnh Scale-In {target_scale.upper()}...", flush=True)
            if not self.dispatch_github(WORKFLOW_SCALE_IN, inputs):
                last_scale_in_time = 0

    def dispatch_github(self, workflow_id, inputs):
        url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/workflows/{workflow_id}/dispatches"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
            "Content-Type": "application/json"
        }
        data = {"ref": "main", "inputs": inputs}
        req = urllib.request.Request(url, headers=headers, data=json.dumps(data).encode('utf-8'))
        try:
            urllib.request.urlopen(req, timeout=10)
            print(f"[OK] Đã gửi Trigger tới {workflow_id} với inputs={inputs}!", flush=True)
            return True
        except Exception as e:
            print(f"[ERROR] Lỗi khi gọi GitHub API: {e}", flush=True)
            return False

if __name__ == '__main__':
    if not GITHUB_TOKEN:
        print("[!] Lỗi: Chưa cấu hình biến môi trường GITHUB_PAT_TOKEN.", flush=True)
        exit(1)
    server = HTTPServer(('0.0.0.0', 5000), WebhookHandler)
    print("=== MeowCloud Auto-Scale Webhook Listener (Priority & Batching Mode) Khởi Động ===", flush=True)
    server.serve_forever()
