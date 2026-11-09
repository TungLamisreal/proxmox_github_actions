#!/usr/bin/env python3
"""Read-only, bounded Loki query_range adapter for one webhook systemd journal.
No HTTP writes, dispatches, arbitrary LogQL, shell commands or journal mutation.
"""
import hmac
import json
import os
import re
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

SELECTOR = '{service="meowcloud-webhook"}'


def read_rows(unit, start, end, limit, runner=subprocess.run):
    result = runner(
        ["journalctl", "--unit=" + unit, "--since=@" + str(start / 1e9),
         "--until=@" + str(end / 1e9), "--output=json", "--no-pager",
         "--reverse", "--lines=" + str(limit)],
        capture_output=True, text=True, check=True, timeout=4)
    rows = []
    for line in result.stdout.splitlines():
        entry = json.loads(line)
        stamp = int(entry["__REALTIME_TIMESTAMP"]) * 1000
        message = entry.get("MESSAGE", "")
        if isinstance(message, str) and start <= stamp <= end:
            rows.append([str(stamp), message[:16384]])
    return sorted(rows, key=lambda row: int(row[0]), reverse=True)[:limit]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if not hmac.compare_digest(self.headers.get("Authorization", ""),
                                   "Bearer " + self.server.token):
            return self.reply(401, {"error": "Unauthorized"})
        url = urlsplit(self.path)
        if url.path == "/ready":
            return self.reply(200, {"ready": True})
        if url.path != "/loki/api/v1/query_range":
            return self.reply(404, {"error": "Only bounded webhook log queries are supported"})
        try:
            q = parse_qs(url.query, strict_parsing=True)
            if q.get("query") != [SELECTOR] or q.get("direction", ["backward"]) != ["backward"]:
                raise ValueError("Unsupported query")
            start, end = int(q["start"][0]), int(q["end"][0])
            limit = int(q.get("limit", ["200"])[0])
            if not (0 <= start <= end and end - start <= 86400 * 10**9 and 1 <= limit <= 500):
                raise ValueError("Range must be <=24 hours; limit must be 1..500")
        except (ValueError, KeyError, IndexError) as exc:
            return self.reply(400, {"error": str(exc)})
        try:
            rows = read_rows(self.server.unit, start, end, limit)
        except (subprocess.SubprocessError, OSError, ValueError, KeyError):
            return self.reply(503, {"error": "Webhook journal unavailable"})
        streams = [{"stream": {"service": "meowcloud-webhook"}, "values": rows}] if rows else []
        self.reply(200, {"status": "success", "data": {"resultType": "streams", "result": streams}})


def main():
    with open("/etc/meowcloud-journal/config.json") as f:
        config = json.load(f)
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+\.service", config["unit"]):
        raise SystemExit("Invalid unit")
    if len(config["token"]) < 32:
        raise SystemExit("Token too short")
    server = ThreadingHTTPServer((config["bind"], 5011), Handler)
    server.token, server.unit = config["token"], config["unit"]
    server.serve_forever()


if __name__ == "__main__":
    main()
