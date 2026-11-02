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

    def test_automatic_failover_is_explicitly_disabled(self):
        config = (ROOT / "ansible/group_vars/databases/replication.yml").read_text()
        self.assertIn("pg_automatic_failover_enabled: false", config)
        self.assertIn('pg_primary_ip: "192.168.150.71"', config)
        self.assertIn('pg_standby_ip: "192.168.150.72"', config)
        self.assertNotIn("password:", config)


if __name__ == "__main__":
    unittest.main()
