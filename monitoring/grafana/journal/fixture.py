"""Disposable CI source; never installed by the playbook."""
import time
from http.server import ThreadingHTTPServer
import server

def fixture(unit, start, end, limit):
    now = int(time.time() * 1e9)
    return [[str(now - i * 1000000000), ("[OK] Đã gửi Trigger tới 03-autoscale-backend.yml " if i == 0 else "[*] Đang Cooldown · ") + ("log kiểm tra " * 24)] for i in range(min(limit, 200))]

server.read_rows = fixture
http = ThreadingHTTPServer(("127.0.0.1", 5011), server.Handler)
http.token, http.unit = "ci-journal-token", "fixture.service"
http.serve_forever()
