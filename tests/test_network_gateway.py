"""Regression contracts for the canonical pfSense LAN gateway."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
TERRAFORM = ROOT / "terraform"
PFSENSE_GATEWAY = "192.168.150.1"
OLD_GATEWAY_PATTERN = r"192\.168\.150\.2(?!\d)"


class NetworkGatewayTests(unittest.TestCase):
    def test_network_gateway_is_pfsense_and_cannot_be_overridden(self):
        variables = (TERRAFORM / "variables.tf").read_text()
        self.assertIn(f'default     = "{PFSENSE_GATEWAY}"', variables)
        self.assertIn(
            f'condition     = var.network_gateway == "{PFSENSE_GATEWAY}"',
            variables,
        )
        self.assertNotRegex(variables, OLD_GATEWAY_PATTERN)

    def test_every_routed_guest_uses_the_canonical_gateway_variable(self):
        routed_guest_files = [
            "01-observer.tf",
            "02-tunnels.tf",
            "03-loadbalancer.tf",
            "04-backend.tf",
            "05-frontend.tf",
            "06-database.tf",
        ]
        for name in routed_guest_files:
            with self.subTest(name=name):
                source = (TERRAFORM / name).read_text()
                self.assertIn("gateway = var.network_gateway", source)
                self.assertNotRegex(source, OLD_GATEWAY_PATTERN)

    def test_ansible_inventory_renderers_preserve_the_gateway(self):
        terraform_inventory = (TERRAFORM / "07-ansible-generator.tf").read_text()
        scale_in_inventory = (ROOT / "scripts/scale_in_node.py").read_text()
        self.assertIn("network_gateway=${var.network_gateway}", terraform_inventory)
        self.assertIn(f"network_gateway={PFSENSE_GATEWAY}", scale_in_inventory)

    def test_tunnel_playbook_repairs_and_verifies_the_running_route(self):
        playbook = (ROOT / "ansible/playbooks/01-setup-tunnels.yml").read_text()
        self.assertIn("ip -4 route show default", playbook)
        self.assertIn("- replace", playbook)
        self.assertIn("ip -4 route get 1.1.1.1", playbook)
        self.assertIn(PFSENSE_GATEWAY, playbook)

    def test_provision_workflow_pins_and_checks_the_gateway(self):
        workflow = (ROOT / ".github/workflows/01-provision-infra.yml").read_text()
        self.assertIn(f'TF_VAR_network_gateway: "{PFSENSE_GATEWAY}"', workflow)
        self.assertIn('expected_gateway = "192.168.150.1"', workflow)
        self.assertIn("invalid_gateways", workflow)

    def test_generated_plan_artifacts_are_not_committed(self):
        self.assertFalse((TERRAFORM / "tfplan_scale_in").exists())
        self.assertFalse((TERRAFORM / "tfplan_scale_in.json").exists())


if __name__ == "__main__":
    unittest.main()
