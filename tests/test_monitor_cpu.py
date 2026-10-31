"""Offline tests of CPU dashboard calculations; no Prometheus required."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch

source = Path(__file__).resolve().parents[1] / "scripts/monitoring/monitor_cpu.py"
spec = importlib.util.spec_from_file_location("monitor_cpu", source)
mon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mon)


class CpuMonitorTests(unittest.TestCase):
    def test_pool_weighted_by_cores(self):
        fixtures = {
            'rate(meowcloud_lxc_cpu_usage_seconds_total{job="lxc_backends"}[1m])':
                [{"metric": {"instance": "a:9101"}, "value": [0, "1"]},
                 {"metric": {"instance": "b:9101"}, "value": [0, "0.5"]}],
            'meowcloud_lxc_cpu_capacity_cores{job="lxc_backends"}':
                [{"metric": {"instance": "a:9101"}, "value": [0, "2"]},
                 {"metric": {"instance": "b:9101"}, "value": [0, "2"]}],
            'up{job="lxc_backends"}':
                [{"metric": {"instance": "a:9101"}, "value": [0, "1"]},
                 {"metric": {"instance": "b:9101"}, "value": [0, "1"]}],
            'rate(meowcloud_lxc_cpu_usage_seconds_total{job="lxc_frontends"}[1m])': [],
            'meowcloud_lxc_cpu_capacity_cores{job="lxc_frontends"}': [],
            'up{job="lxc_frontends"}': [],
        }
        with patch.object(mon, "prometheus_query", side_effect=lambda _, expr, __: fixtures[expr]):
            result = mon.get_cpu_snapshot("http://127.0.0.1:9090", "1m", 3)
        self.assertAlmostEqual(result["BE"][0], 37.5)
        self.assertEqual([round(item[1], 1) for item in result["BE"][1]], [50, 25])
        self.assertIsNone(result["FE"][0])

    def test_down_target_fails_closed(self):
        def response(_, expr, __):
            if expr.startswith("up{"):
                return [{"metric": {"instance": "a"}, "value": [0, "0"]}]
            return [{"metric": {"instance": "a"}, "value": [0, "1"]}]
        with patch.object(mon, "prometheus_query", side_effect=response):
            result = mon.get_cpu_snapshot("http://localhost:9090", "1m", 3)
        self.assertIsNone(result["BE"][0])
        self.assertIsNone(result["BE"][1][0][1])


if __name__ == "__main__":
    unittest.main()
