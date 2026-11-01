"""Offline checks for DB provisioning SSH identity and safe SQL readiness retry."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import call, patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "bootstrap_meowcrud.py"


def load_bootstrap():
    spec = importlib.util.spec_from_file_location("meowcloud_bootstrap", SRC)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BootstrapReadinessTests(unittest.TestCase):
    def setUp(self):
        self.bootstrap = load_bootstrap()

    def test_psql_uses_explicit_runner_ssh_identity(self):
        module = self.bootstrap
        result = SimpleNamespace(returncode=0, stdout="1\n")
        with patch.object(module.subprocess, "run", return_value=result) as runner:
            self.assertEqual(module.psql("SELECT 1;"), "1")
        argv = runner.call_args.args[0]
        self.assertEqual(argv[0], "ssh")
        self.assertEqual(argv[argv.index("-i") + 1],
                         "/home/github-runner/.ssh/id_ed25519")
        self.assertEqual(argv[argv.index("IdentitiesOnly=yes") - 1], "-o")
        self.assertEqual(argv[argv.index("UserKnownHostsFile=/dev/null") - 1], "-o")
        self.assertEqual(runner.call_args.kwargs["input"], "SELECT 1;")
        self.assertTrue(runner.call_args.kwargs["capture_output"])

    def test_connection_not_ready_retries_select_without_ddl(self):
        module = self.bootstrap
        with patch.object(module, "psql", side_effect=[
            RuntimeError("PostgreSQL command failed (rc=2)"), "1"
        ]) as psql, patch.object(module.time, "sleep") as sleep:
            module.wait_for_sql_ready(attempts=4, interval=2)
        self.assertEqual(psql.call_args_list, [call("SELECT 1;"), call("SELECT 1;")])
        sleep.assert_called_once_with(2)

    def test_ssh_auth_failure_never_retried(self):
        module = self.bootstrap
        with patch.object(module, "psql", side_effect=RuntimeError(
            "PostgreSQL command failed (rc=255)"
        )) as psql, patch.object(module.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "rc=255"):
                module.wait_for_sql_ready(attempts=10, interval=2)
        psql.assert_called_once()
        sleep.assert_not_called()

    def test_readiness_timeout_is_bounded(self):
        module = self.bootstrap
        with patch.object(module, "psql", side_effect=RuntimeError(
            "PostgreSQL command failed (rc=2)"
        )) as psql, patch.object(module.time, "sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "retry budget"):
                module.wait_for_sql_ready(attempts=3, interval=2)
        self.assertEqual(psql.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_anible_waits_for_real_sql_and_cloud_init_injects_public_key(self):
        playbook = (ROOT / "ansible/playbooks/05-setup-database.yml").read_text()
        self.assertIn("Wait until PostgreSQL can execute SELECT 1", playbook)
        self.assertIn("until:", playbook)
        self.assertIn("retries: 45", playbook)
        terraform = (ROOT / "terraform/06-database.tf").read_text()
        self.assertIn("keys     = [trimspace(file(var.ssh_public_key_path))]", terraform)
        workflow = (ROOT / ".github/workflows/01-provision-infra.yml").read_text()
        self.assertIn('SSH_KEY_PATH="$SSH_KEY_DIR/id_ed25519"', workflow)
        self.assertIn('ssh-keygen -y -f "$SSH_KEY_PATH"', workflow)


if __name__ == "__main__":
    unittest.main()
