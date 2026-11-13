"""Read-only Patroni transfer ownership contracts."""
import importlib.util
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import shlex
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "db07_patroni_preflight", ROOT / "scripts/db07_patroni_preflight.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class PatroniPreflightTests(unittest.TestCase):
    def make_inventory(self):
        """Create an isolated Terraform-like fixture; CI has no runtime inventory."""
        directory = tempfile.TemporaryDirectory(prefix="meow-db07-inventory-")
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "inventory.ini"
        path.write_text(
            "[databases]\n"
            "db-01 ansible_host=192.168.150.71\n"
            "db-02 ansible_host=192.168.150.72\n"
            "[loadbalancers]\n"
            "lb-01 ansible_host=192.168.150.61\n"
            "lb-02 ansible_host=192.168.150.62\n"
            "[monitoring]\n"
            "observer ansible_host=192.168.150.21\n",
            encoding="utf-8",
        )
        return path

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

    def test_ssh_matches_proven_db06_transport_after_golden_vm_rebuild(self):
        from subprocess import CompletedProcess
        with patch.object(module.subprocess, "run",
                          return_value=CompletedProcess([], 0, "healthy", "")) as run:
            self.assertEqual(module.ssh("192.168.150.71",
                                        ["docker", "inspect", "--format", module.DOCKER_FMT,
                                         "meow-postgres"]), "healthy")
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[:5], ["ssh", "-T", "-n", "-i", module.SSH_IDENTITY])
        self.assertEqual(cmd[-2], "root@192.168.150.71")
        self.assertEqual(shlex.split(cmd[-1]),
                         ["docker", "inspect", "--format", module.DOCKER_FMT,
                          "meow-postgres"])
        for required in ("BatchMode=yes", "IdentitiesOnly=yes",
                         "StrictHostKeyChecking=no", "UserKnownHostsFile=/dev/null"):
            self.assertIn(required, cmd)
            self.assertIn(required, (ROOT / "scripts/db06_detect.py").read_text())
        self.assertEqual(run.call_args.kwargs["check"], False)
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_ssh_failure_reports_cause_without_exposing_remote_stderr(self):
        from subprocess import CompletedProcess
        cases = (
            ("WARNING: REMOTE HOST IDENTIFICATION HAS CHANGED!", "host-key mismatch"),
            ("Permission denied (publickey).", "public-key authentication"),
            ("connect to host failed: Connection timed out", "TCP timeout"),
            ("ssh: connect to host: Connection refused", "refused connection"),
        )
        for stderr, reason in cases:
            with self.subTest(reason=reason), patch.object(
                module.subprocess, "run",
                return_value=CompletedProcess([], 255, "", stderr)
            ):
                with self.assertRaises(module.AuditError) as ctx:
                    module.ssh("192.168.150.71", ["true"])
                self.assertIn(reason, str(ctx.exception))
                self.assertNotIn("WARNING:", str(ctx.exception))

    def test_runtime_inventory_matches_day0_generated_host_contract(self):
        good = self.make_inventory()
        module.verify_runtime_inventory(good)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.ini"
            path.write_text(good.read_text().replace("db-01 ansible_host=192.168.150.71",
                                                     "db-01 ansible_host=192.168.150.99"))
            with self.assertRaisesRegex(module.AuditError, "Stale or unexpected"):
                module.verify_runtime_inventory(path)
            path.write_text(good.read_text() + "\n[databases]\ndb-01 ansible_host=192.168.150.71\n")
            with self.assertRaisesRegex(module.AuditError, "Duplicate"):
                module.verify_runtime_inventory(path)

    def test_inventory_only_mode_never_connects_or_requires_private_key(self):
        good = self.make_inventory()
        argv = ["db07_patroni_preflight.py", "--inventory", str(good),
                "--validate-inventory-only"]
        output = StringIO()
        with patch.object(sys, "argv", argv), patch.object(
            module, "ssh", side_effect=AssertionError("SSH is not allowed")
        ), redirect_stdout(output):
            module.main()
        self.assertIn("no SSH", output.getvalue())

    def test_full_readonly_cli_simulates_fresh_db_hosts_and_missing_lb_proxy(self):
        from subprocess import CompletedProcess
        outputs = []
        def fake_remote(cmd, **kwargs):
            host = cmd[-2]
            remote = shlex.split(cmd[-1])
            outputs.append((host, remote))
            if remote[:2] == ["docker", "inspect"]:
                value = ("true|always|postgres:15-alpine|"
                         "/opt/pgdata/data=/var/lib/postgresql/data;")
            elif remote[0] == "findmnt":
                value = "/opt/pgdata"
            elif remote[0] == "pvesh":
                value = '[{"sid":"vm:271","state":"started"}]'
            elif remote[:2] == ["python3", "-c"]:
                self.assertIn("/proc/net/tcp", remote[2])
                value = "closed"
            else:
                raise AssertionError("Unexpected remote probe: " + repr(remote))
            return CompletedProcess(cmd, 0, value, "")

        with tempfile.TemporaryDirectory() as directory:
            key = Path(directory) / "id_ed25519"
            key.write_text("mock-key-for-offline-test")
            argv = ["db07_patroni_preflight.py", "--inventory",
                    str(self.make_inventory())]
            output = StringIO()
            with patch.object(module, "SSH_IDENTITY", str(key)), patch.object(
                module.subprocess, "run", side_effect=fake_remote
            ), patch.object(sys, "argv", argv), redirect_stdout(output):
                module.main()
        self.assertEqual(len(outputs), 7)
        for ip in ("192.168.150.71", "192.168.150.72", "192.168.150.49",
                   "192.168.150.61", "192.168.150.62"):
            self.assertTrue(any("root@" + ip == host for host, _ in outputs))
        result = output.getvalue()
        self.assertIn("READINESS: BLOCKED", result)
        self.assertIn("Docker restart policy", result)
        self.assertIn("Proxmox HA still starts DB01", result)
        self.assertIn("writer proxy port 5432 not listening", result)

    def test_workflow_06_patroni_image_transfer_uses_job_scoped_host_keys(self):
        """Regression for run #19: rebuilt DB VM keys must not fail on stale runner keys."""
        workflow = (ROOT / ".github/workflows/06-db-streaming-replication.yml").read_text()
        transfer = workflow.split("Build exact commit image and import on both DB hosts", 1)[1]
        transfer = transfer.split("Stage Patroni config without restarting legacy PostgreSQL", 1)[0]
        self.assertIn('transfer_known_hosts="$(mktemp)"', transfer)
        self.assertIn('chmod 0600 "$transfer_known_hosts"', transfer)
        self.assertIn("trap 'rm -f \"$transfer_known_hosts\"' EXIT", transfer)
        self.assertIn('for ip in 192.168.150.71 192.168.150.72; do', transfer)
        self.assertIn('UserKnownHostsFile=$transfer_known_hosts', transfer)
        self.assertIn('StrictHostKeyChecking=accept-new', transfer)
        self.assertIn('BatchMode=yes', transfer)
        self.assertIn('IdentitiesOnly=yes', transfer)
        self.assertNotIn('StrictHostKeyChecking=no', transfer)
        self.assertNotIn('UserKnownHostsFile=/dev/null', transfer)
        self.assertNotIn('UserKnownHostsFile=/home/github-runner/.ssh/known_hosts', transfer)
        self.assertIn('docker save "$image" | ssh', transfer)
        self.assertIn('"root@$ip" docker load', transfer)

    def test_day0_workflow_publishes_inventory_and_canonical_key(self):
        provision = (ROOT / ".github/workflows/01-provision-infra.yml").read_text()
        build = (ROOT / ".github/workflows/00-build-golden-images.yml").read_text()
        self.assertIn("3b. Publish fresh Terraform-generated runtime inventory", provision)
        self.assertIn("install -m 0644 ./ansible/inventory.ini /opt/meowcloud-iac/ansible/inventory.ini", provision)
        self.assertIn("ssh-keygen -y -f", build)
        self.assertIn(module.SSH_IDENTITY, provision)
        self.assertIn(module.DEFAULT_INVENTORY, provision)
        self.assertNotIn("rm -rf /opt/pgdata", (ROOT / "scripts/db07_patroni_preflight.py").read_text())

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
