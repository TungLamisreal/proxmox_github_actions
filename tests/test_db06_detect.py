"""Pure state transition checks for the DB06 no-rebootstrap gate."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import shlex
import subprocess

SOURCE = Path(__file__).resolve().parents[1] / "scripts/db06_detect.py"
spec = importlib.util.spec_from_file_location("db06_detect", SOURCE)
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)

Snapshot = module.Snapshot
detect = module.detect


class DB06StateMachine(unittest.TestCase):
    primary = Snapshot("f", "1000", 1, 0)
    replica = Snapshot("t", "1000", 0, 1)

    def test_ssh_remote_psql_field_separator_is_shell_quoted(self):
        # ssh does not preserve argv boundaries past the remote hostname.
        # A bare -F | becomes a shell pipeline; quote before sending.
        result = subprocess.CompletedProcess([], 0, "f|1000|1|0\\n", "")
        with patch.object(module.subprocess, "run", return_value=result) as runner:
            self.assertEqual(module.remote_sql("192.168.150.71", "SELECT 1;"), "f|1000|1|0")
        command = runner.call_args.args[0]
        remote = command[-1]
        self.assertEqual(
            shlex.split(remote),
            ["docker", "exec", "-i", "-u", "postgres", "meow-postgres",
             "psql", "-XAt", "-F", "|", "-v", "ON_ERROR_STOP=1",
             "-U", "postgres", "-d", "postgres"],
        )
        self.assertIn("'|'", remote)
        self.assertEqual(runner.call_args.kwargs["input"], "SELECT 1;")
        self.assertEqual(runner.call_args.kwargs["check"], False)

    def test_ssh_failure_surfaces_remote_diagnostic_not_sql(self):
        bad = subprocess.CompletedProcess([], 127, "", "remote shell: command not found")
        with patch.object(module.subprocess, "run", return_value=bad):
            with self.assertRaisesRegex(RuntimeError, "exit 127.*command not found"):
                module.remote_sql("192.168.150.71", "SELECT private_value;")

    def test_existing_healthy_pair_skips_bootstrap(self):
        for mode in ("full", "verify", "ha-readiness", "patroni-stage"):
            self.assertFalse(detect(self.primary, self.replica, mode, False))

    def test_initial_standalone_requires_explicit_confirmation(self):
        empty = Snapshot("f", "2000", 0, 0)
        with self.assertRaisesRegex(ValueError, "APPLY_DB_REPLICATION"):
            detect(self.primary, empty, "full", False)
        self.assertTrue(detect(self.primary, empty, "full", True))

    def test_verify_never_initializes_standby(self):
        with self.assertRaisesRegex(ValueError, "not set up"):
            detect(self.primary, Snapshot("f", "2000", 0, 0), "verify", True)

    def test_partial_streaming_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "broken"):
            detect(self.primary, Snapshot("t", "1000", 0, 0), "full", True)

    def test_wrong_lineage_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "broken"):
            detect(self.primary, Snapshot("t", "2000", 0, 1), "full", True)

    def test_promoted_old_standby_blocks_pipeline(self):
        with self.assertRaisesRegex(ValueError, "DB01 is not"):
            detect(Snapshot("t", "1000", 0, 1), Snapshot("f", "1000", 1, 0), "full", True)

    def test_two_writers_same_lineage_are_blocked(self):
        with self.assertRaisesRegex(ValueError, "split-brain"):
            detect(self.primary, Snapshot("f", "1000", 0, 0), "full", True)


if __name__ == "__main__":
    unittest.main()
