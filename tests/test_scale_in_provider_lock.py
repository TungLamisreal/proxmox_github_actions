"""Regression contracts for scale-in's persistent Terraform provider lock."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class ProviderLockTests(unittest.TestCase):
    def test_scale_in_reconciles_before_guest_mutation(self):
        workflow = (ROOT / ".github/workflows/04-autoscale-in.yml").read_text()
        preflight = workflow.index("Preflight Terraform provider lock before scale-in")
        selection = workflow.index("Backup Elastic Pool State & Select LIFO Instance")
        draining = workflow.index("Step 1 - Graceful Connection Draining")
        self.assertLess(preflight, selection)
        self.assertLess(preflight, draining)
        self.assertIn("terraform init -upgrade -input=false", workflow[preflight:selection])
        self.assertIn("terraform validate -no-color", workflow[preflight:selection])

    def test_provider_version_held_during_plan(self):
        workflow = (ROOT / ".github/workflows/04-autoscale-in.yml").read_text()
        plan = workflow.split("Step 3 - Terraform Plan & Controlled Scale-In Gate", 1)[1]
        plan = plan.split("Step 4 - Terraform Apply", 1)[0]
        self.assertIn("terraform init -input=false -lockfile=readonly", plan)
        self.assertNotIn("terraform init -upgrade", plan)

    def test_provider_exact_constraint(self):
        provider = (ROOT / "terraform/provider.tf").read_text()
        self.assertIn('source  = "bpg/proxmox"', provider)
        self.assertIn('version = "= 0.119.0"', provider)


if __name__ == "__main__":
    unittest.main()
