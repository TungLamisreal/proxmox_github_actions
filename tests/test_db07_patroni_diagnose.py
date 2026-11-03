"""Read-only Patroni diagnostic safeguards; no live database access."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
from subprocess import CompletedProcess
import importlib.util
import sys
import unittest
from unittest.mock import patch
from urllib import error

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
        self.assertIn("StrictHostKeyChecking=yes", argv)
        self.assertIn("UserKnownHostsFile=/home/github-runner/.ssh/known_hosts", argv)
        self.assertNotIn("StrictHostKeyChecking=no", argv)
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

    def test_primary_503_is_not_interpreted_as_postgresql_failure_or_leadership(self):
        body = json.dumps({"/primary_http": 503, "/replica_http": 503,
                           "/_http": 200, "local": {"state": "running", "role": "uninitialized"}})
        message = mod.interpret_patroni_rest(body)
        self.assertIn("NOT PRIMARY", message)
        self.assertIn("role=uninitialized", message)
        self.assertNotIn("DATABASE LOST", message)
        self.assertIn("PRIMARY", mod.interpret_patroni_rest(
            json.dumps({"/primary_http": 200, "/replica_http": 503})))
        self.assertIn("REPLICA", mod.interpret_patroni_rest(
            json.dumps({"/primary_http": 503, "/replica_http": 200})))
        self.assertIn("not reachable", mod.interpret_patroni_rest(
            json.dumps({"/primary_http": 0})))
        self.assertIn("unavailable", mod.interpret_patroni_rest("not-json"))

    def test_remote_rest_probe_allowlists_roles_and_drops_passwords(self):
        # Executes the exact remote Python probe without making any network calls.
        class Response:
            def __init__(self, code, data):
                self.status = code
                self._data = data

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self, count):
                return self._data[:count]

        def fake_urlopen(url, timeout):
            if url.endswith("/primary") or url.endswith("/replica"):
                raise error.HTTPError(url, 503, "Not primary", {}, None)
            if url.endswith("/cluster"):
                return Response(200, json.dumps({
                    "members": [
                        {"name": "db-01", "state": "running",
                         "role": "uninitialized", "conn_url": "postgresql://user:password@host"},
                        {"name": "db-02", "state": "running", "role": "replica"},
                    ],
                    "password": "do-not-print"
                }).encode())
            return Response(200, json.dumps({
                "role": "uninitialized", "state": "running",
                "password": "do-not-print", "patroni": {"private_key": "secret"},
                "dcs_last_seen": 12345
            }).encode())
        output = StringIO()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), redirect_stdout(output):
            exec(compile(mod.PATRONI_REST_PROBE, "<probe>", "exec"), {"__name__": "__main__"})
        result = json.loads(output.getvalue())
        self.assertEqual(result["/primary_http"], 503)
        self.assertEqual(result["/replica_http"], 503)
        self.assertEqual(result["/_http"], 200)
        self.assertEqual(result["local"], {"role": "uninitialized", "state": "running"})
        self.assertEqual(result["members"][0], {
            "name": "db-01", "role": "uninitialized", "state": "running"
        })
        self.assertNotIn("do-not-print", output.getvalue())
        self.assertNotIn("postgresql://", output.getvalue())
        self.assertNotIn("private_key", output.getvalue())

    def test_remote_patroni_503_json_body_keeps_only_allowlisted_status(self):
        def fake_urlopen(url, timeout):
            payload = json.dumps({
                "role": "uninitialized", "state": "stopped",
                "secret": "password-for-logs", "conn_url": "postgres://admin:hide-me@db",
                "pending_restart": False,
            }).encode()
            if url.endswith("/"):
                raise error.HTTPError(url, 503, "Unavailable", {}, BytesIO(payload))
            raise error.HTTPError(url, 503, "Unavailable", {}, BytesIO(b"{}"))

        output = StringIO()
        with patch("urllib.request.urlopen", side_effect=fake_urlopen), redirect_stdout(output):
            exec(compile(mod.PATRONI_REST_PROBE, "<rest-503>", "exec"),
                 {"__name__": "__main__"})
        data = json.loads(output.getvalue())
        self.assertEqual(data["/_http"], 503)
        self.assertEqual(data["local"], {
            "role": "uninitialized", "state": "stopped", "pending_restart": False
        })
        self.assertNotIn("hide-me", output.getvalue())
        self.assertNotIn("password-for-logs", output.getvalue())

    def test_diagnostic_checks_sql_readiness_without_role_changes(self):
        from inspect import getsource
        src = getsource(mod.main)
        self.assertIn("PATRONI_REST_PROBE", src)
        self.assertIn("pg_isready", src)
        self.assertIn("pg_is_in_recovery()", src)
        self.assertIn("pg_stat_wal_receiver", src)
        self.assertIn("PATRONI INTERPRETATION", src)
        self.assertNotIn("pg_ctl", src)
        self.assertNotIn("pg_promote", src)
        self.assertNotIn("docker restart", src)

    def test_diagnose_mode_is_decoupled_from_failing_etcd_gate(self):
        workflow = (ROOT / ".github/workflows/06-db-streaming-replication.yml").read_text()
        self.assertIn("options: [bootstrap-ha, health, diagnose, adopt-vip, gateway-repair, resume]", workflow)
        self.assertIn("inputs.mode != 'diagnose'", workflow)
        self.assertIn("inputs.mode == 'diagnose'", workflow)
        self.assertIn("if: ${{ failure() }}", workflow)
        self.assertIn("python3 scripts/db07_patroni_diagnose.py", workflow)


if __name__ == "__main__":
    unittest.main()
