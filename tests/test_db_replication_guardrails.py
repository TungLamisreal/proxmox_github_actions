"""Safety contracts for a staged PostgreSQL streaming replication rollout."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLAYBOOKS = ROOT / "ansible/playbooks"


class DatabaseReplicationGuardrails(unittest.TestCase):
    def test_audit_is_read_only_and_has_optional_strict_mode(self):
        audit = (PLAYBOOKS / "10-audit-db-replication.yml").read_text()
        for required in ("hosts: databases", "pg_is_in_recovery()", "pg_stat_replication",
                         "pg_stat_wal_receiver", "system_identifier",
                         "pg_expect_replication_ready | default(false) | bool"):
            self.assertIn(required, audit)
        for unsafe in ("pg_basebackup", "rm -rf", "pg_promote(", "docker stop"):
            self.assertNotIn(unsafe, audit)

    def test_source_setup_is_gated_and_never_bootstraps_standby(self):
        source = (PLAYBOOKS / "11-configure-db-primary-replication.yml").read_text()
        for required in ("hosts: db-01", "pg_apply_primary_replication | default(false) | bool",
                         "pg_backup_reference", "vault_postgres_replication_password",
                         "no_log: true", "ansible.builtin.lineinfile:"):
            self.assertIn(required, source)
        for unsafe in ("pg_basebackup", "pg_promote(", "rm -rf", "docker stop", "docker restart"):
            self.assertNotIn(unsafe, source)

    def test_replication_vars_loaded_explicitly_for_external_runtime_inventory(self):
        # GitHub Actions runs from its own checkout but uses an external CT120
        # inventory. Ansible must not depend on automatic group_vars discovery.
        vars_path = "{{ playbook_dir }}/../group_vars/databases/replication.yml"
        config = (ROOT / "ansible/group_vars/databases/replication.yml").read_text()
        self.assertIn('pg_replication_user: "meow_replication"', config)
        for filename in ("11-configure-db-primary-replication.yml",
                         "12-bootstrap-db-standby.yml"):
            with self.subTest(playbook=filename):
                source = (PLAYBOOKS / filename).read_text()
                self.assertIn("vars_files:", source)
                self.assertIn(vars_path, source)
                self.assertIn("Validate declared replication topology is loaded", source)

    def test_standby_bootstrap_is_staged_and_has_rollback(self):
        source = (PLAYBOOKS / "12-bootstrap-db-standby.yml").read_text()
        for required in ("hosts: db-02", "pg_bootstrap_standby | default(false) | bool",
                         "pg_basebackup", "pg_previous_dir", "pg_stage_dir",
                         "ansible.builtin.wait_for:", "community.docker.docker_container:",
                         "rescue:", "pg_is_in_recovery()", "pg_stat_wal_receiver",
                         "standby.signal", "system_identifier"):
            self.assertIn(required, source)
        for forbidden in ("rm -rf", "terraform destroy", "pg_promote("):
            self.assertNotIn(forbidden, source)

    def test_workflow_is_manual_and_keeps_dirty_runtime_checkout(self):
        source = (ROOT / ".github/workflows/06-db-streaming-replication.yml").read_text()
        for required in ("workflow_dispatch:", "actions/checkout@v4", "BOOTSTRAP_MEOWCLOUD_DB_HA",
                         "POSTGRES_REPLICATION_PASSWORD", "pg_bootstrap_standby=true",
                         "pg_expect_replication_ready=true"):
            self.assertIn(required, source)
        self.assertNotIn("git reset --hard", source)
        self.assertNotIn("git clean -fd", source)

    def test_backup_phase_precedes_mutations(self):
        source = (PLAYBOOKS / "09-backup-db-before-replication.yml").read_text()
        workflow = (ROOT / ".github/workflows/06-db-streaming-replication.yml").read_text()
        self.assertIn("pg_dumpall", source)
        self.assertIn("pre-replication-", source)
        self.assertLess(workflow.index('name: "02 • Preserve existing standalone DB01 SQL"'),
                        workflow.index('name: "03 • Set up WAL sender on standalone DB01"'))

    def test_six_job_workflow_and_read_only_patroni_gate(self):
        source = (ROOT / ".github/workflows/06-db-streaming-replication.yml").read_text()
        self.assertIn("    needs: [preflight, backup]", source)
        self.assertIn("    needs: [preflight, primary]", source)
        self.assertIn("    needs: [preflight, backup, primary, standby]", source)
        self.assertIn("    needs: [preflight, verify]", source)
        self.assertIn("always()", source)
        self.assertIn("    if: ${{", source)
        self.assertIn("patroni-readiness:", source)
        self.assertIn("patroni-stage:", source)
        self.assertIn("STAGE_PATRONI_CONFIG_ONLY", source)
        self.assertNotIn("patroni.service", source)

    def test_automatic_failover_is_explicitly_disabled(self):
        config = (ROOT / "ansible/group_vars/databases/replication.yml").read_text()
        self.assertIn("pg_automatic_failover_enabled: false", config)
        self.assertIn('pg_primary_ip: "192.168.150.71"', config)
        self.assertIn('pg_standby_ip: "192.168.150.72"', config)
        self.assertNotIn("password:", config)


if __name__ == "__main__":
    unittest.main()
