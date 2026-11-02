"""Regression tests: etcd gRPC health is NOT Patroni HTTP API health."""
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return (ROOT / path).read_text(encoding="utf-8")


class GatewayAndResumeTests(unittest.TestCase):
    def test_etcd_config_explicitly_activates_grpc_http_gateway(self):
        conf = read("ansible/templates/meowcloud-etcd.yml.j2")
        self.assertIn("enable-grpc-gateway: true", conf)
        self.assertIn("client-cert-auth: true", conf)
        self.assertIn("peer-client-cert-auth: true", conf)
        self.assertIn("https://{{ ansible_host }}:2379", conf)

    def test_db06_health_now_checks_patroni_json_member_endpoint(self):
        pb = read("ansible/playbooks/16-verify-etcd-quorum.yml")
        self.assertIn("etcdctl", pb)
        self.assertIn("endpoint", pb)
        self.assertIn("health", pb)
        self.assertIn("/v3/cluster/member/list", pb)
        self.assertIn("method: POST", pb)
        self.assertIn("client_cert:", pb)
        self.assertIn("client_key:", pb)
        self.assertIn("validate_certs: true", pb)
        self.assertIn("status_code: 200", pb)
        self.assertIn("gateway_response.results", pb)

    def test_rolling_gateway_patch_requires_existing_quorum_no_state_reset(self):
        pb = read("ansible/playbooks/22-enable-etcd-gateway.yml")
        d = yaml.safe_load(pb)
        self.assertEqual(len(d), 4)
        self.assertIn("serial: 1", pb)
        self.assertIn("any_errors_fatal: true", pb)
        self.assertIn("db07_gateway_repair_approved", pb)
        self.assertIn(".meowcloud-db06-owned", pb)
        self.assertIn("regexp: '^enable-grpc-gateway:'", pb)
        self.assertIn("line: 'enable-grpc-gateway: true'", pb)
        self.assertIn("when: gateway_changed.changed", pb)
        self.assertIn("method: POST", pb)
        self.assertIn("/v3/cluster/member/list", pb)
        self.assertIn("/opt/meowcloud-patroni/tls/patroni-client-key.pem", pb)
        self.assertIn("validate_certs: true", pb)
        for forbidden in ("initdb", "pg_basebackup", "docker stop", "docker start",
                          "rm -rf", "etcdctl member remove", "systemctl stop postgres"):
            self.assertNotIn(forbidden, pb)

    def test_resume_uses_original_db01_patroni_image_and_never_replaces_db01(self):
        pb = read("ansible/playbooks/23-resume-patroni-lab.yml")
        self.assertIn("pg_resume_takeover_approved", pb)
        self.assertIn("original_patroni_image", pb)
        self.assertIn("hostvars['db-01'].original_patroni_image", pb)
        self.assertIn("meow-postgres", pb)
        self.assertIn("meow-patroni", pb)
        self.assertIn("http://127.0.0.1:8008/primary", pb)
        self.assertIn("db07-activate-one.yml", pb)
        self.assertLess(pb.index("hosts: db-01"), pb.index("hosts: db-02"))
        first_play = pb.split("- name: Resume DB02 only if")[0]
        for forbidden in ("docker stop", "docker start", "docker run",
                          "pg_promote(", "initdb", "pg_basebackup"):
            self.assertNotIn(forbidden, first_play)

    def test_workflow_gateway_repair_bypasses_preexisting_false_green_gate(self):
        workflow = read(".github/workflows/07-db-patroni-lab.yml")
        doc = yaml.safe_load(workflow)
        self.assertEqual(doc.get("on", doc.get(True))["workflow_dispatch"]["inputs"]["mode"]["options"],
                         ["prepare", "takeover", "health", "proxy", "diagnose", "gateway-repair", "resume"])
        self.assertIn("inputs.mode != 'gateway-repair'", workflow)
        self.assertIn("ENABLE_ETCD_HTTP_GATEWAY", workflow)
        self.assertIn("RESUME_PATRONI_LAB", workflow)
        self.assertIn("ansible/playbooks/22-enable-etcd-gateway.yml", workflow)
        self.assertIn("ansible/playbooks/23-resume-patroni-lab.yml", workflow)
        self.assertIn("needs: gate", workflow)
        self.assertIn("ansible/playbooks/20-verify-patroni-lab.yml", workflow)
        self.assertNotIn("git reset --hard", workflow)


if __name__ == "__main__":
    unittest.main()
