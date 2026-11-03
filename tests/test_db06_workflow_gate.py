"""Offline contract tests for Workflow 06's no-green-on-skip gate."""
import importlib.util
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts/db06_workflow_gate.py"
spec = importlib.util.spec_from_file_location("db06_workflow_gate", SOURCE)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def sample(mode, seed="false"):
    needs = {job: {"result": "skipped"} for jobs in gate.REQUIRED.values() for job in jobs}
    needs.update({job: {"result": "skipped"} for job in gate.SEED_JOBS})
    for job in gate.REQUIRED[mode]:
        needs[job] = {"result": "success"}
    if mode == "bootstrap-ha":
        needs["preflight"]["outputs"] = {"bootstrap_required": seed}
        if seed == "true":
            for job in gate.SEED_JOBS:
                needs[job]["result"] = "success"
    return needs


class Db06WorkflowGateTests(unittest.TestCase):
    def test_existing_streaming_must_still_complete_ha_takeover(self):
        needs = sample("bootstrap-ha", seed="false")
        gate.validate("bootstrap-ha", needs)

    def test_real_run20_false_green_skipped_etcd_and_patroni_is_rejected(self):
        needs = sample("bootstrap-ha", seed="false")
        for job in ("etcd-bootstrap", "etcd-verify", "prepare", "takeover",
                    "health", "hba", "proxy", "writer-smoke", "activate-vip", "final-smoke"):
            needs[job]["result"] = "skipped"
        with self.assertRaisesRegex(gate.GateError, "incomplete"):
            gate.validate("bootstrap-ha", needs)

    def test_new_replica_seeding_branch_success(self):
        gate.validate("bootstrap-ha", sample("bootstrap-ha", seed="true"))

    def test_seed_required_but_stage_skipped_fails(self):
        needs = sample("bootstrap-ha", seed="true")
        needs["standby"]["result"] = "skipped"
        with self.assertRaisesRegex(gate.GateError, "branch mismatch"):
            gate.validate("bootstrap-ha", needs)

    def test_seed_not_required_but_unexpected_destructive_job_fails(self):
        needs = sample("bootstrap-ha", seed="false")
        needs["standby"]["result"] = "success"
        with self.assertRaisesRegex(gate.GateError, "branch mismatch"):
            gate.validate("bootstrap-ha", needs)

    def test_missing_bootstrap_flag_fails_closed(self):
        needs = sample("bootstrap-ha", seed="false")
        needs["preflight"]["outputs"] = {}
        with self.assertRaisesRegex(gate.GateError, "Invalid bootstrap_required"):
            gate.validate("bootstrap-ha", needs)

    def test_health_mode_requires_real_patroni_health(self):
        gate.validate("health", sample("health"))
        needs = sample("health")
        needs["health"]["result"] = "skipped"
        with self.assertRaisesRegex(gate.GateError, "required job health"):
            gate.validate("health", needs)

    def test_vip_adoption_needs_final_smoke(self):
        gate.validate("adopt-vip", sample("adopt-vip"))
        needs = sample("adopt-vip")
        needs["final-smoke"]["result"] = "skipped"
        with self.assertRaisesRegex(gate.GateError, "final-smoke"):
            gate.validate("adopt-vip", needs)

    def test_other_modes_have_explicit_end_checks(self):
        for mode in ("diagnose", "gateway-repair", "resume"):
            with self.subTest(mode=mode):
                gate.validate(mode, sample(mode))

    def test_failed_or_cancelled_job_blocks_any_green(self):
        for status in ("failure", "cancelled"):
            with self.subTest(status=status):
                needs = sample("health")
                needs["etcd-bootstrap"]["result"] = status
                with self.assertRaises(gate.GateError):
                    gate.validate("health", needs)

    def test_unknown_mode_fails(self):
        with self.assertRaisesRegex(gate.GateError, "Unknown workflow 06 mode"):
            gate.validate("bad", {})

    def test_workflow_explicit_skip_guards_and_completion_contract(self):
        workflow = (ROOT / ".github/workflows/06-db-streaming-replication.yml").read_text()
        for job in ("verify", "etcd-bootstrap", "etcd-verify", "prepare",
                    "takeover", "health", "hba", "proxy",
                    "writer-smoke", "activate-vip", "final-smoke"):
            with self.subTest(job=job):
                match = re.search(r"(?m)^  " + re.escape(job) +
                                  r":\n(?:(?!^  [a-z]).*\n)*?    if: ([^\n]+)",
                                  workflow)
                self.assertIsNotNone(match, f"Missing conditional job: {job}")
                self.assertIn("always()", match.group(1),
                              f"Job {job} is exposed to implicit upstream skip propagation")
        self.assertIn("python3 scripts/db06_workflow_gate.py", workflow)
        self.assertIn("NEEDS_JSON: ${{ toJSON(needs) }}", workflow)


if __name__ == "__main__":
    unittest.main()
