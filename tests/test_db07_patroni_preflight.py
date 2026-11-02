"""Read-only Patroni transfer ownership contracts."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "db07_patroni_preflight", ROOT / "scripts/db07_patroni_preflight.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class PatroniPreflightTests(unittest.TestCase):
    def sample(self):
        containers = {
            db: {"running": True, "restart_policy": "always",
                 "image": "postgres:15-alpine",
                 "mounts": {"/opt/pgdata/data": "/var/lib/postgresql/data"}}
            for db in module.DB
        }
        mounts = {name: "/opt/pgdata" for name in module.DB}
        lports = {name: False for name in module.LB}
        pve = [{"sid": "vm:271", "state": "started"}]
        return containers, mounts, pve, lports

    def test_legacy_containers_and_pve_ha_are_cutover_blockers(self):
        issues = module.evaluate(*self.sample())
        self.assertTrue(any("legacy standalone" in x for x in issues))
        self.assertTrue(any("restart policy" in x for x in issues))
        self.assertTrue(any("Proxmox HA" in x for x in issues))
        self.assertTrue(any("writer proxy" in x for x in issues))

    def test_wrong_mounts_never_pass_readiness(self):
        c, m, p, lb = self.sample()
        c["db-02"]["mounts"] = {}
        m["db-01"] = "/"
        issues = module.evaluate(c, m, p, lb)
        self.assertTrue(any("PGDATA Docker bind" in x for x in issues))
        self.assertTrue(any("PGDATA filesystem" in x for x in issues))

    def test_docker_inspect_uses_only_safe_fields(self):
        source = (ROOT / "scripts/db07_patroni_preflight.py").read_text()
        self.assertIn("{{.HostConfig.RestartPolicy.Name}}", source)
        for forbidden in ("docker stop", "docker update", "pg_promote(",
                          "rm -rf", "postgresql.auto.conf", ".Config.Env", "terraform destroy"):
            self.assertNotIn(forbidden, source)
        self.assertEqual(module.parse_container(
            "true|always|postgres:15-alpine|/opt/pgdata/data=/var/lib/postgresql/data;"
        )["restart_policy"], "always")
        with self.assertRaises(module.AuditError):
            module.parse_container("invalid")

    def test_probe_uses_ssh_command_as_single_quoted_argument(self):
        from subprocess import CompletedProcess
        with patch.object(module.subprocess, "run", return_value=CompletedProcess([], 0, "OK", "")) as run:
            self.assertEqual(module.ssh("192.168.150.71", ["echo", "hello world"]), "OK")
        self.assertEqual(run.call_args.args[0][-1], "echo 'hello world'")

    def test_image_preserves_pg15_and_does_not_start_as_root(self):
        img = (ROOT / "docker/db-patroni/Dockerfile").read_text()
        self.assertIn("postgres:15-alpine", img)
        self.assertIn("pg_rewind", img)
        self.assertIn("USER postgres", img)
        self.assertIn("patroni[etcd3]==", img)
        self.assertNotIn("docker exec", img)
        self.assertNotIn("pg_basebackup", img)


if __name__ == "__main__":
    unittest.main()
