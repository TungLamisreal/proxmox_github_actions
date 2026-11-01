"""Offline regressions for a clean, one-shot Golden Images demo build."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKER = ROOT / "packer"

APT_SCRIPTS = [
    "01-base-packages.sh", "02-docker-install.sh", "03-kvm-specific.sh",
    "20-lb-packages.sh", "21-observer-packages.sh",
    "22-tunnel-packages.sh", "99-cleanup-sanitize.sh",
]


class GoldenBuildTests(unittest.TestCase):
    def test_all_packer_apt_operations_use_bounded_lock_wait(self):
        for name in APT_SCRIPTS:
            with self.subTest(name=name):
                content = (PACKER / "scripts" / name).read_text()
                lines = [line.strip() for line in content.splitlines()
                         if line.lstrip().startswith("apt-get ")]
                self.assertTrue(lines, f"No apt-get commands found: {name}")
                for line in lines:
                    self.assertRegex(
                        line, r"^apt-get -o DPkg::Lock::Timeout=600 "
                    )

    def test_sanitize_does_not_delete_dpkg_lock_or_kill_apt(self):
        content = (PACKER / "scripts" / "99-cleanup-sanitize.sh").read_text()
        self.assertIn("apt-daily.timer apt-daily-upgrade.timer", content)
        self.assertNotIn("rm -f /var/lib/dpkg/lock", content)
        self.assertNotIn("killall apt", content)

    def test_db_builder_quiesces_apt_timers_inside_disposable_clone(self):
        content = (PACKER / "db-kvm.pkr.hcl").read_text()
        self.assertIn("systemctl stop apt-daily.timer apt-daily-upgrade.timer", content)
        self.assertIn("/tmp/packer_scripts_kvm/99-cleanup-sanitize.sh", content)

    def test_00_builds_db_one_by_one_with_fail_fast_and_acceptance(self):
        workflow = (ROOT / ".github/workflows/00-build-golden-images.yml").read_text()
        self.assertIn("set -euo pipefail", workflow)
        self.assertIn("for target in node1 node2 node3; do", workflow)
        self.assertIn('-only="proxmox-clone.db_${target}"', workflow)
        self.assertIn("4. Post-build acceptance", workflow)
        for vmid in (9000, 9001, 9002, 9003, 9100, 9101, 9102):
            self.assertIn(str(vmid), workflow)

    def test_00_refuses_existing_templates_instead_of_auto_deleting(self):
        workflow = (ROOT / ".github/workflows/00-build-golden-images.yml").read_text()
        self.assertIn("Fail-closed preflight: reject stale image IDs", workflow)
        self.assertIn("Never reuse or destroy old templates automatically", workflow)


if __name__ == "__main__":
    unittest.main()
