"""Regression checks for Alertmanager coalescing concurrent FE/BE CPU alerts."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class AlertBatchingContracts(unittest.TestCase):
    def test_both_tiers_share_an_alertmanager_group(self):
        observer = (ROOT / "ansible/playbooks/06-setup-observer.yml").read_text()
        self.assertIn("group_by: ['autoscale', 'scale_action']", observer)
        self.assertIn("group_wait: 25s", observer)
        self.assertIn("group_interval: 30s", observer)
        self.assertIn("repeat_interval: 60s", observer)
        self.assertIn("alert: BackendPoolHighCPU", observer)
        self.assertIn("alert: FrontendPoolHighCPU", observer)
        self.assertIn('scale_target: "backend"', observer)
        self.assertIn('scale_target: "frontend"', observer)

    def test_webhook_dispatches_one_per_tier_when_alerts_arrive_together(self):
        webhook = (ROOT / "scripts/webhook_listener.py").read_text()
        self.assertIn("if merged_be > 0 and merged_fe > 0:", webhook)
        self.assertIn("add_be, add_fe = 1, 1", webhook)
        self.assertIn('"scale_target": "both"', webhook)


if __name__ == "__main__":
    unittest.main()
