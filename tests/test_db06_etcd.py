"""Safeguards for deploying etcd quorum without altering live PostgreSQL."""
import importlib.util
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/db06_etcd_pki.py"
PVE_SCRIPT = ROOT / "scripts/db06_pve_placement.py"


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    obj = importlib.util.module_from_spec(spec)
    sys.modules[name] = obj
    spec.loader.exec_module(obj)
    return obj


class DBEtcdSafetyTests(unittest.TestCase):
    def test_pve_guests_are_on_distinct_nodes(self):
        placement = load_module(PVE_SCRIPT, "db06_pve_test")
        guests = [
            {"type": "qemu", "vmid": 271, "node": "node2", "status": "running"},
            {"type": "qemu", "vmid": 272, "node": "node3", "status": "running"},
            {"type": "lxc", "vmid": 121, "node": "node1", "status": "running"},
        ]
        self.assertEqual(len(placement.assert_distinct_nodes(guests)), 3)
        guests[2]["node"] = "node2"
        with self.assertRaisesRegex(RuntimeError, "share a physical PVE node"):
            placement.assert_distinct_nodes(guests)

    def test_existing_certificates_do_not_rotate(self):
        with tempfile.TemporaryDirectory() as directory:
            args = [sys.executable, str(SCRIPT), "--directory", directory]
            subprocess.run(args, capture_output=True, check=True, timeout=80)
            originals = {p.name: p.read_bytes() for p in Path(directory).glob("*.pem")}
            subprocess.run(args, capture_output=True, check=True, timeout=80)
            subprocess.run(args + ["--verify-only"], capture_output=True, check=True, timeout=80)
            self.assertEqual(originals, {
                p.name: p.read_bytes() for p in Path(directory).glob("*.pem")
            })
            self.assertEqual(len(originals), 5)
            self.assertTrue((Path(directory) / "ca.key").is_file())

    def test_partial_ca_blocks_reinitialization(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "ca.key").write_text("incomplete")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--directory", directory],
                capture_output=True, text=True, timeout=15,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertFalse((Path(directory) / "ca.pem").exists())

    def test_deployment_playbook_never_manages_database_or_wipes_data(self):
        play = (ROOT / "ansible/playbooks/15-bootstrap-etcd-quorum.yml").read_text()
        for expected in ("hosts: db-01:db-02:monitoring", "db_enable_etcd",
                         "meowcloud-etcd", "etcd-server", "etcd-client",
                         "etcd-server-key.pem", "client-cert-auth"):
            if expected == "client-cert-auth":
                template = (ROOT / "ansible/templates/meowcloud-etcd.yml.j2").read_text()
                self.assertIn(expected, template)
            else:
                self.assertIn(expected, play)
        for forbidden in ("docker stop", "pg_basebackup", "pg_promote(",
                          "rm -rf", "/opt/pgdata", "terraform destroy"):
            self.assertNotIn(forbidden, play)

    def test_dcs_jobs_are_separate_and_patroni_stage_waits_for_quorum(self):
        content = (ROOT / ".github/workflows/06-db-streaming-replication.yml").read_text()
        self.assertIn("  etcd-bootstrap:", content)
        self.assertIn("  etcd-verify:", content)
        self.assertIn("BOOTSTRAP_ETCD_QUORUM", content)
        self.assertIn("needs: [preflight, patroni-readiness, etcd-verify]", content)
        self.assertIn("ansible/playbooks/15-bootstrap-etcd-quorum.yml", content)
        self.assertIn("ansible/playbooks/16-verify-etcd-quorum.yml", content)
        self.assertIn("db06_pve_placement.py", content)
        self.assertIn("MEOW_ETCD_PKI: /home/github-runner/.local/state/meowcloud/db-ha-etcd-pki", content)
        self.assertNotIn("MEOW_ETCD_PKI: /opt/meowcloud-iac/state", content)
        self.assertNotIn("git reset --hard", content)


if __name__ == "__main__":
    unittest.main()
