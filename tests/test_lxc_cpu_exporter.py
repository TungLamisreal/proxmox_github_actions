"""Offline tests for the MeowCloud LXC CPU exporter (no Proxmox required)."""
import importlib.util
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "ansible/files/lxc_cpu_exporter.py"
spec = importlib.util.spec_from_file_location("lxc_cpu_exporter", SOURCE)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class LxcCpuExporterTests(unittest.TestCase):
    def test_usage_usec_converts_to_seconds(self):
        with tempfile.TemporaryDirectory() as tmp:
            stat = Path(tmp) / "cpu.stat"
            stat.write_text("usage_usec 2500000\nuser_usec 2000000\nsystem_usec 500000\n")
            self.assertEqual(exporter.read_usage_seconds(stat), 2.5)

    def test_missing_usage_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            stat = Path(tmp) / "cpu.stat"
            stat.write_text("user_usec 2000000\n")
            with self.assertRaises(KeyError):
                exporter.read_usage_seconds(stat)

    def test_http_prometheus_metrics_and_unreadable_cpu_stat(self):
        with tempfile.TemporaryDirectory() as tmp:
            stat = Path(tmp) / "cpu.stat"
            stat.write_text("usage_usec 4000000\n")
            srv = ThreadingHTTPServer(("127.0.0.1", 0), exporter.MetricsHandler)
            srv.cpu_stat = stat
            srv.capacity_cores = 2.0
            thread = threading.Thread(target=srv.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{srv.server_port}/metrics"
                with urllib.request.urlopen(url, timeout=3) as r:
                    body = r.read().decode()
                self.assertIn("meowcloud_lxc_cpu_usage_seconds_total 4.000000", body)
                self.assertIn("meowcloud_lxc_cpu_capacity_cores 2", body)
                stat.unlink()
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(url, timeout=3)
                self.assertEqual(error.exception.code, 503)
            finally:
                srv.shutdown()
                srv.server_close()
                thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
