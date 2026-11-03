"""Verify the provisioned dashboard and query Observer through Grafana as a Viewer."""
import json
import math
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen

BASE = "http://127.0.0.1:3000"


def get(path):
    with urlopen(BASE + path, timeout=5) as response:
        return json.load(response)


def check():
    if get("/api/health").get("database") != "ok":
        raise ValueError("Grafana database is not ready")
    board = get("/api/dashboards/uid/meow-autoscale")["dashboard"]
    proxy = "/api/datasources/proxy/uid/meow-prom/api/v1/query?"
    for panel in board["panels"]:
        for target in panel["targets"]:
            reply = get(proxy + urlencode({"query": target["expr"]}))
            rows = reply["data"]["result"]
            if reply["status"] != "success" or not rows:
                raise ValueError("No data for " + panel["title"])
            if not all(math.isfinite(float(row["value"][1])) for row in rows):
                raise ValueError("Invalid sample for " + panel["title"])
    for job in ("lxc_backends", "lxc_frontends"):
        reply = get(proxy + urlencode({"query": f'min(up{{job="{job}"}})'}))
        rows = reply["data"]["result"]
        if len(rows) != 1 or float(rows[0]["value"][1]) != 1:
            raise ValueError("Missing or DOWN exporter: " + job)


if __name__ == "__main__":
    for attempt in range(24):
        try:
            check()
            break
        except (OSError, URLError, KeyError, ValueError) as exc:
            if attempt == 23:
                raise SystemExit(f"Grafana smoke failed: {exc}") from None
            time.sleep(5)
    print("PASS: dashboard, datasource proxy, panel queries and both exporter pools")
