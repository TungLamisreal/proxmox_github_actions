#!/usr/bin/env python3
"""Small Prometheus exporter for the current LXC cgroup (including nested Docker work).

Unlike node_cpu_seconds_total inside an LXC, cpu.stat reports CPU time charged to
this container's cgroup. Capacity is the vCPU allocation declared in Terraform.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CPU_STAT = Path("/sys/fs/cgroup/cpu.stat")


def read_usage_seconds(path=CPU_STAT):
    fields = dict(line.split() for line in path.read_text().splitlines() if len(line.split()) == 2)
    usage_usec = int(fields["usage_usec"])
    if usage_usec < 0:
        raise ValueError("Negative cgroup CPU usage")
    return usage_usec / 1_000_000.0


class MetricsHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/metrics":
            self.send_error(404)
            return
        try:
            usage = read_usage_seconds(self.server.cpu_stat)
            cores = self.server.capacity_cores
        except (OSError, ValueError, KeyError) as exc:
            # Fail closed: Prometheus should mark this scrape DOWN, not see 0% CPU.
            self.send_error(503, "Cannot read LXC cgroup cpu.stat")
            print("cgroup read error:", type(exc).__name__, flush=True)
            return

        body = (
            "# HELP meowcloud_lxc_cpu_usage_seconds_total CPU seconds charged to this LXC cgroup, including descendants.\n"
            "# TYPE meowcloud_lxc_cpu_usage_seconds_total counter\n"
            f"meowcloud_lxc_cpu_usage_seconds_total {usage:.6f}\n"
            "# HELP meowcloud_lxc_cpu_capacity_cores Allocated LXC vCPU cores (Terraform cpu.cores).\n"
            "# TYPE meowcloud_lxc_cpu_capacity_cores gauge\n"
            f"meowcloud_lxc_cpu_capacity_cores {cores:g}\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        # Avoid writing a line every 5 seconds on every scrape.
        pass


def main():
    parser = argparse.ArgumentParser(description="MeowCloud LXC cgroup CPU exporter")
    parser.add_argument("--capacity-cores", type=float, required=True)
    parser.add_argument("--port", type=int, default=9101)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--cpu-stat", type=Path, default=CPU_STAT)
    args = parser.parse_args()
    if not (0 < args.capacity_cores <= 128):
        parser.error("--capacity-cores must be positive and <= 128")
    # Exit immediately if this guest does not expose cgroup v2 CPU accounting.
    read_usage_seconds(args.cpu_stat)
    srv = ThreadingHTTPServer((args.bind, args.port), MetricsHandler)
    srv.capacity_cores = args.capacity_cores
    srv.cpu_stat = args.cpu_stat
    print(f"LXC cgroup CPU exporter listening on {args.bind}:{args.port}; capacity={args.capacity_cores:g}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
