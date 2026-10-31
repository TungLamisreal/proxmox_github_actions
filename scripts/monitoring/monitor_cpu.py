#!/usr/bin/env python3
"""Live MeowCloud BE/FE CPU dashboard, sourced from Prometheus LXC cgroup metrics.

Run on CT120: python3 scripts/monitoring/monitor_cpu.py --interval 2 --csv /tmp/cpu.csv
No SSH and no external Python packages required. Ctrl+C to stop.
"""
import argparse
import csv
import json
import math
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

CPU = "meowcloud_lxc_cpu_usage_seconds_total"
CAP = "meowcloud_lxc_cpu_capacity_cores"
JOBS = (("lxc_backends", "BE"), ("lxc_frontends", "FE"))


def prometheus_query(base, expr, timeout):
    url = base.rstrip("/") + "/api/v1/query?" + urlencode({"query": expr})
    with urlopen(url, timeout=timeout) as response:
        payload = json.load(response)
    if payload.get("status") != "success":
        raise RuntimeError("Prometheus query did not succeed: " + str(payload.get("error", "")))
    return payload["data"]["result"]


def vectors_as_map(series):
    values = {}
    for entry in series:
        instance = entry.get("metric", {}).get("instance", "")
        if not instance:
            continue
        value = float(entry["value"][1])
        if math.isfinite(value):
            values[instance] = value
    return values


def get_cpu_snapshot(base, window, timeout):
    snapshot = {}
    for job, label in JOBS:
        selector = f'job="{job}"'
        usage = vectors_as_map(prometheus_query(
            base, f'rate({CPU}{{{selector}}}[{window}])', timeout
        ))
        capacity = vectors_as_map(prometheus_query(
            base, f'{CAP}{{{selector}}}', timeout
        ))
        up = vectors_as_map(prometheus_query(
            base, f'up{{{selector}}}', timeout
        ))
        pool_pct = None
        # Same PromQL calculation as 4 autoscale rules. Missing/down targets
        # invalidate the pool reading instead of silently undercounting CPU.
        if up and all(v == 1 for v in up.values()) and set(usage) == set(capacity) == set(up):
            cores = sum(capacity.values())
            if cores > 0:
                pool_pct = 100 * sum(usage.values()) / cores

        hosts = []
        for instance in sorted(set(up) | set(capacity) | set(usage)):
            cpu_pct = None
            if up.get(instance) == 1 and instance in usage and capacity.get(instance, 0) > 0:
                cpu_pct = 100 * usage[instance] / capacity[instance]
            hosts.append((instance, cpu_pct, up.get(instance) == 1))
        snapshot[label] = (pool_pct, hosts)
    return snapshot


def format_percent(value):
    return "  N/A " if value is None else f"{value:6.1f}%"


def main():
    parser = argparse.ArgumentParser(description="Monitor real LXC cgroup CPU from Prometheus")
    parser.add_argument("--prometheus", default="http://192.168.150.21:9090",
                        help="Prometheus endpoint (default CT121)")
    parser.add_argument("--interval", type=float, default=2.0, help="Refresh seconds (default: 2)")
    parser.add_argument("--window", default="1m", help="Prometheus rate window, same as autoscale alerts")
    parser.add_argument("--timeout", type=float, default=4.0, help="Request timeout seconds")
    parser.add_argument("--csv", type=Path, help="Optional CSV output file (per host and pool)")
    args = parser.parse_args()
    if args.interval <= 0 or args.timeout <= 0:
        parser.error("--interval and --timeout must be positive")

    file = None
    try:
        writer = None
        if args.csv:
            args.csv.parent.mkdir(parents=True, exist_ok=True)
            file = args.csv.open("w", newline="", encoding="utf-8")
            writer = csv.writer(file)
            writer.writerow(["timestamp", "tier", "instance", "cpu_percent", "up"])
            file.flush()
        print(f"MeowCloud CPU | Prometheus={args.prometheus} | interval={args.interval:g}s | rate={args.window}")
        print("LXC cgroup CPU / allocated vCPU; pool = sum(CPU usage) / sum(allocated cores). Ctrl+C to stop.\n")
        next_tick = time.monotonic()
        while True:
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            try:
                snapshot = get_cpu_snapshot(args.prometheus, args.window, args.timeout)
                parts = []
                for label in ("BE", "FE"):
                    pool, hosts = snapshot[label]
                    part = f"{label} pool {format_percent(pool)}"
                    for i, (instance, pct, is_up) in enumerate(hosts, 1):
                        suffix = "" if is_up else " DOWN"
                        part += f" | {label}{i}({instance.split(':')[0]}): {format_percent(pct)}{suffix}"
                        if writer:
                            writer.writerow([timestamp, label, instance, "" if pct is None else round(pct, 3), int(is_up)])
                    if writer:
                        writer.writerow([timestamp, label, "POOL", "" if pool is None else round(pool, 3), ""])
                    parts.append(part)
                print(f"[{timestamp[11:19]}] " + "\n           ".join(parts), flush=True)
                if writer:
                    file.flush()
            except Exception as exc:
                print(f"[{timestamp[11:19]}] PROMETHEUS ERROR: {type(exc).__name__}: {exc}", flush=True)
            next_tick += args.interval
            time.sleep(max(0, next_tick - time.monotonic()))
    except KeyboardInterrupt:
        print("\nMonitor stopped.")
    finally:
        if file:
            file.close()


if __name__ == "__main__":
    main()
