"""Read-only Patroni diagnostic safeguards; no live database access."""
from pathlib import Path
from subprocess import CompletedProcess
import importlib.util
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "db07_patroni_diagnose", ROOT / "scripts/db07_patroni_diagnose.py"
)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class PatroniDiagnosticsTests(unittest.TestCase):
    def test_sensitive_fields_are_redacted(self):
        dirty = (
            "password=superSecret123; "
            "postgres://admin:top-secret@localhost:5432/db "
            "token: abcDEF123 error"
        )
        clean = mod.redact(dirty)
        for secret in ("superSecret123", "top-secret", "abcDEF123"):
            self.assertNotIn(secret, clean)
        self.assertIn("[REDACTED]", clean)

    def test_read_only_probe_never_invokes_mutating_commands(self):
        source = (ROOT / "scripts/db07_patroni_diagnose.py").read_text()
        for forbidden in ("docker stop", "docker start", "docker rm",
                          "docker update", "pg_ctl promote", "pg_promote(",
                          "rm -rf", "initdb", "pg_basebackup",
                          "terraform apply"):
            self.assertNotIn(forbidden, source)
        self.assertNotIn(".Config.Env", source)
        self.assertNotIn("cat /opt/meowcloud-patroni/patroni.yml", source)
        self.assertEqual(set(mod.HOSTS), {"db-01", "db-02"})

    def test_ssh_uses_explicit_runner_identity_and_redacts_remote_output(self):
        with patch.object(
            mod.subprocess, "run",
            return_value=CompletedProcess([], 0, "FATAL password=hide-me\n", "")
        ) as runner:
            value = mod.ssh_read("192.168.150.71", [
                "docker", "logs", "--tail", "140", "meow-patroni"
            ])
        self.assertIn("[REDACTED]", value)
        self.assertNotIn("hide-me", value)
        argv = runner.call_args.args[0]
        self.assertEqual(argv[:5], ["ssh", "-T", "-n", "-i", mod.KEY])
        self.assertIn("IdentitiesOnly=yes", argv)
        self.assertEqual(argv[-2], "root@192.168.150.71")
        self.assertIn("docker logs --tail 140 meow-patroni", argv[-1])

    def test_ssh_255_does_not_dump_arbitrary_stderr(self):
        with patch.object(
            mod.subprocess, "run",
            return_value=CompletedProcess([], 255, "", "password=do-not-print")
        ):
            result = mod.ssh_read("192.168.150.72", ["docker", "ps"])
        self.assertIn("SSH transport unavailable", result)
        self.assertNotIn("do-not-print", result)

    def test_logs_focus_on_traces_not_excess_noise(self):
        logs = "uninformative\n2026 FATAL etcd TLS handshake refused\nnetwork=ok\n"
        excerpts = mod.patroni_log_excerpt(logs)
        self.assertIn("FATAL", excerpts)
        self.assertNotIn("uninformative", excerpts)

    def test_diagnose_mode_is_decoupled_from_failing_etcd_gate(self):
        workflow = (ROOT / ".github/workflows/07-db-patroni-lab.yml").read_text()
        self.assertIn("options: [prepare, takeover, health, proxy, diagnose]", workflow)
        self.assertIn("inputs.mode != 'diagnose'", workflow)
        self.assertIn("inputs.mode == 'diagnose'", workflow)
        self.assertIn("if: ${{ failure() }}", workflow)
        self.assertIn("python3 scripts/db07_patroni_diagnose.py", workflow)


if __name__ == "__main__":
    unittest.main()
