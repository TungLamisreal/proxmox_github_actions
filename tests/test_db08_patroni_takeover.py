"""Guardrails for lab-only Patroni adoption and DB writer proxy rollout."""
from pathlib import Path
import re
import unittest

import yaml
from jinja2 import Environment, StrictUndefined

ROOT = Path(__file__).resolve().parents[1]


def source(path):
    return (ROOT / path).read_text()


class DB08TakeoverContracts(unittest.TestCase):
    def test_day0_refuses_to_resurrect_legacy_db_after_ownership_transfer(self):
        p = source("ansible/playbooks/05-setup-database.yml")
        self.assertIn("takeover.lock", p)
        self.assertLess(p.index("takeover.lock"), p.index("Run PostgreSQL Docker Container"))
        self.assertIn("not db07_takeover_marker.stat.exists", p)

    def test_takeover_has_no_competing_postgresql_and_orders_db01_first(self):
        core = source("ansible/tasks/db07-activate-one.yml")
        pb = source("ansible/playbooks/19-takeover-patroni-lab.yml")
        for required in ("docker", "pg_is_in_recovery()", "takeover.lock",
                         "--restart=no", "meow-postgres", "meow-patroni",
                         "/opt/pgdata/data:/var/lib/postgresql/data",
                         "role_ready", "http://127.0.0.1:8008/"):
            self.assertIn(required, core)
        for forbidden in ("pg_basebackup", "pg_promote(", "initdb", "rm -rf",
                          "docker rm", "docker volume rm", "terraform destroy"):
            self.assertNotIn(forbidden, core)
            self.assertNotIn(forbidden, pb)
        self.assertIn("hosts: db-01", pb)
        self.assertIn("hosts: db-02", pb)
        self.assertLess(pb.index("hosts: db-01"), pb.index("hosts: db-02"))
        self.assertIn("delegate_to: db-01", pb)
        self.assertNotIn("docker update --restart=always", core)

    def test_staging_keeps_etcd_secret_copies_private(self):
        pb = source("ansible/playbooks/18-prepare-patroni-lab.yml")
        for value in ("owner: '70'", "group: '70'", "mode: '0600'",
                      "remote_src: true", "no_log: true",
                      "meowcloud-etcd", "PG_VERSION", "pg_prepare_patroni"):
            # etcd ownership marker uses 'meowcloud-etcd' string.
            self.assertIn(value, pb)
        for forbidden in ("docker stop", "docker update", "pg_promote(",
                          "pg_basebackup", "initdb", "rm -rf"):
            self.assertNotIn(forbidden, pb)

    def test_render_patroni_yaml_with_complex_password_and_correct_paths(self):
        template = source("ansible/templates/patroni.yml.j2")
        for name, host in (("db-01", "192.168.150.71"), ("db-02", "192.168.150.72")):
            with self.subTest(name=name):
                env = Environment(undefined=StrictUndefined)
                env.filters["to_json"] = __import__("json").dumps
                data = env.from_string(template).render(
                    inventory_hostname=name, ansible_host=host,
                    vault_postgres_password='abc:\"#! foo',
                    vault_postgres_replication_password="sample-repl-secret",
                    pg_replication_user="meow_replication",
                )
                parsed = yaml.safe_load(data)
                self.assertEqual(parsed["postgresql"]["data_dir"],
                                 "/var/lib/postgresql/data")
                self.assertEqual(parsed["postgresql"]["bin_dir"], "/usr/local/bin")
                self.assertEqual(parsed["postgresql"]["parameters"]["wal_log_hints"], "on")
                self.assertEqual(parsed["postgresql"]["authentication"]["superuser"]["password"],
                                 'abc:\"#! foo')
                self.assertEqual(parsed["etcd3"]["protocol"], "https")
                self.assertEqual(parsed["etcd3"]["key"],
                                 "/etc/meowcloud/tls/patroni-client-key.pem")
                self.assertIn("192.168.150.21:2379", parsed["etcd3"]["hosts"])
                self.assertEqual(parsed["bootstrap"]["dcs"]["postgresql"]["parameters"]["wal_log_hints"], "on")

    def test_health_does_not_assume_db01_is_primary_after_failover(self):
        pb = source("ansible/playbooks/20-verify-patroni-lab.yml")
        self.assertIn("pg_is_in_recovery()", pb)
        self.assertIn("pg_stat_wal_receiver", pb)
        self.assertIn("pg_stat_replication", pb)
        self.assertIn("wal_log_hints", pb)
        self.assertIn("hostvars['db-02'].patroni_primary_api.status == 200", pb)
        self.assertIn("hostvars['db-01'].patroni_primary_api.status == 200", pb)
        self.assertIn("false|no", pb)
        self.assertIn("true|always", pb)

    def test_proxy_routes_only_to_patroni_primary_and_preserves_existing_web_vip(self):
        template = source("ansible/templates/haproxy-db-standalone.cfg.j2")
        pb = source("ansible/playbooks/21-configure-db-writer-proxy.yml")
        self.assertIn("bind 0.0.0.0:5432", template)
        self.assertIn("option httpchk GET /primary", template)
        self.assertIn("check port 8008", template)
        self.assertIn("http://192.168.150.71:8008/primary", pb)
        self.assertIn("http://192.168.150.72:8008/primary", pb)
        self.assertIn("pg_enable_writer_proxy", pb)
        self.assertNotIn("keepalived.conf", pb)
        self.assertNotIn("nginx.conf", pb)

    def test_current_patroni_cannot_be_prepared_or_taken_over_again(self):
        wf = source(".github/workflows/07-db-patroni-lab.yml")
        guard = source("ansible/playbooks/24-guard-patroni-reentry.yml")
        self.assertIn("default: health", wf)
        self.assertIn("24-guard-patroni-reentry.yml", wf)
        self.assertLess(wf.index("24-guard-patroni-reentry.yml"),
                        wf.index('name: "02 • Build'))
        self.assertIn("takeover.lock", guard)
        self.assertIn("meow-patroni", guard)
        self.assertIn("changed_when: false", guard)
        for forbidden in ("docker stop", "docker start", "docker rm",
                          "pg_basebackup", "initdb", "rm -rf"):
            self.assertNotIn(forbidden, guard)

    def test_localhost_replication_hba_is_canonical_and_readonly_audited(self):
        template = source("ansible/templates/patroni.yml.j2")
        audit = source("ansible/playbooks/25-verify-patroni-local-hba.yml")
        wf = source(".github/workflows/07-db-patroni-lab.yml")
        for cidr in ("127.0.0.1/32", "::1/128"):
            self.assertIn("host replication {{ pg_replication_user }} " + cidr,
                          template)
        self.assertIn("FROM pg_hba_file_rules", audit)
        self.assertIn("changed_when: false", audit)
        self.assertIn("25-verify-patroni-local-hba.yml", wf)
        self.assertIn("needs: [gate, health, hba]", wf)
        self.assertIn("failure-diagnostic:", wf)
        self.assertIn("writer-smoke:", wf)
        self.assertIn("api/db-health", wf)

    def test_new_workflow_is_opt_in_with_deploy_then_health_and_retry_path(self):
        wf = source(".github/workflows/07-db-patroni-lab.yml")
        d = yaml.safe_load(wf)
        inputs = d.get("on", d.get(True))["workflow_dispatch"]["inputs"]
        self.assertEqual(inputs["mode"]["options"], ["prepare", "takeover", "health", "proxy", "diagnose", "gateway-repair", "resume"])
        for token in ("TAKEOVER_PATRONI_LAB", "ENABLE_DB_WRITER_PROXY"):
            self.assertIn(token, wf)
        self.assertIn("group: meowcloud-iac-state", wf)
        self.assertIn("needs: [gate, prepare]", wf)
        self.assertIn("ansible/playbooks/16-verify-etcd-quorum.yml", wf)
        self.assertIn("ansible/playbooks/19-takeover-patroni-lab.yml", wf)
        self.assertIn("ansible/playbooks/20-verify-patroni-lab.yml", wf)
        self.assertIn("ansible/playbooks/21-configure-db-writer-proxy.yml", wf)
        self.assertNotIn("git reset --hard", wf)

if __name__ == "__main__":
    unittest.main()
