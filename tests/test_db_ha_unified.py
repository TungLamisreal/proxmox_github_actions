"""Offline contracts for the single DB06 HA workflow and stable writer VIP."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/db_ha_vip.py"
WORKFLOW = ROOT / ".github/workflows/06-db-streaming-replication.yml"


def load_module():
    spec = importlib.util.spec_from_file_location("db_ha_vip_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class DBHAUnifiedTests(unittest.TestCase):
    def test_exactly_one_dispatch_workflow_for_db_ha(self):
        self.assertTrue(WORKFLOW.exists())
        self.assertFalse((ROOT / ".github/workflows/07-db-patroni-lab.yml").exists())
        doc = yaml.safe_load(WORKFLOW.read_text())
        mode = doc.get("on", doc.get(True))["workflow_dispatch"]["inputs"]["mode"]
        self.assertEqual(
            mode["options"],
            ["bootstrap-ha", "health", "diagnose", "adopt-vip",
             "gateway-repair", "resume"],
        )
        self.assertEqual(mode["default"], "health")
        self.assertEqual(doc["concurrency"]["group"], "meowcloud-iac-state")

    def test_stage_graph_never_reenters_patroni_when_checking_health(self):
        wf = yaml.safe_load(WORKFLOW.read_text())
        jobs = wf["jobs"]
        self.assertIn("24-guard-patroni-reentry.yml",
                      str(jobs["preflight"]["steps"]))
        self.assertIn("bootstrap-ha", jobs["preflight"]["if"])
        self.assertIn("needs", jobs["prepare"])
        self.assertEqual(jobs["prepare"]["needs"], "etcd-verify")
        self.assertEqual(jobs["takeover"]["needs"], "prepare")
        self.assertIn("takeover", jobs["health"]["needs"])
        self.assertIn("gate", jobs["health"]["needs"])
        self.assertIn("bootstrap-ha", jobs["proxy"]["if"])
        self.assertEqual(jobs["final-smoke"]["needs"], "activate-vip")
        self.assertNotIn("needs", jobs["diagnose"])
        self.assertIn("diagnose", jobs["diagnose"]["if"])
        self.assertIn("failure-diagnostic", jobs)

    def test_backend_and_release_paths_enforce_vip(self):
        bootstrap = (ROOT / "scripts/bootstrap_meowcrud.py").read_text()
        setup = (ROOT / "ansible/playbooks/03-setup-backend.yml").read_text()
        migration = (ROOT / "ansible/playbooks/09-migrate-crud.yml").read_text()
        self.assertIn("WRITER_VIP = '192.168.150.100'", bootstrap)
        self.assertIn("cfg['db_host'] = WRITER_VIP", bootstrap)
        self.assertIn("'DB_HOST': meow_crud_config.db_host", setup)
        self.assertIn("meow_db_config.db_host == '192.168.150.100'", migration)
        for path in [
            ".github/workflows/05-app-deploy.yml",
            ".github/workflows/05-replay-demo.yml",
        ]:
            source = (ROOT / path).read_text()
            self.assertNotIn('db_host == "192.168.150.71"', source)
            self.assertIn('db_host == "192.168.150.100"', source)
            self.assertNotIn("host=192.168.150.71 port=5432", source)
        for path in [
            ".github/workflows/03-autoscale-backend.yml",
            ".github/workflows/04-autoscale-in.yml",
        ]:
            source = (ROOT / path).read_text()
            self.assertIn("db_ha_vip.py", source)
            self.assertIn("20-verify-patroni-lab.yml", source)

    def test_atomic_vip_state_preserves_authentication_secrets(self):
        vip = load_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meowcrud.json"
            cfg = {
                "initialized": True, "db_host": "192.168.150.71",
                "db_port": 5432, "db_name": "meowcloud_demo",
                "db_user": "meowcrud", "db_password": "do-not-print",
                "demo_token": "also-secret", "extra": {"saved": True},
            }
            path.write_text(json.dumps(cfg))
            self.assertTrue(vip.activate(path))
            stored = json.loads(path.read_text())
            self.assertEqual(stored["db_host"], vip.WRITER_VIP)
            self.assertEqual(stored["db_password"], cfg["db_password"])
            self.assertEqual(stored["demo_token"], cfg["demo_token"])
            self.assertEqual(stored["extra"], cfg["extra"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertFalse(vip.activate(path))
            vip.validate(stored, require_vip=True)

    def test_malformed_vip_config_cannot_overwrite_saved_state(self):
        vip = load_module()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "meowcrud.json"
            cfg = {
                "initialized": True, "db_host": "192.168.150.72",
                "db_port": 5432, "db_name": "meowcloud_demo",
                "db_user": "meowcrud", "db_password": "keep-me",
                "demo_token": "keep-token",
            }
            initial = json.dumps(cfg)
            path.write_text(initial)
            with self.assertRaisesRegex(ValueError, "Unexpected"):
                vip.activate(path)
            self.assertEqual(path.read_text(), initial)


if __name__ == "__main__":
    unittest.main()
