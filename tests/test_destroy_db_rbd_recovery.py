"""Offline regression tests for workflow 02 partial DB RBD destroy recovery."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import destroy_db_rbd_recovery as guard


def sample_vm(vmid=271, status="stopped", name="db-01", node="node2"):
    return {"type": "qemu", "vmid": vmid, "name": name,
            "status": status, "node": node}


def sample_conf(vmid=271):
    name = f"db-0{vmid - 270}"
    return {
        "name": name, "tags": "meowcloud;database;stateful",
        "scsi0": f"general_pool:vm-{vmid}-disk-0,size=10G",
        "scsi1": f"db_pool:vm-{vmid}-disk-0,size=32G",
        "ide2": f"general_pool:vm-{vmid}-cloudinit,media=cdrom",
    }


class RbdDestroyRecoveryTests(unittest.TestCase):
    def test_only_explicit_db_ids_are_mutation_targets(self):
        self.assertEqual(set(guard.DBS), {271, 272})
        self.assertNotIn(120, guard.DBS)
        self.assertNotIn(171, guard.DBS)
        self.assertNotIn(9001, guard.DBS)

    def test_owner_verification_requires_canonical_tf_resource_and_name(self):
        state = {
            "resources": [
                {"mode": "managed", "type": "proxmox_virtual_environment_vm",
                 "name": "db", "instances": [
                     {"attributes": {"vm_id": 271, "name": "db-01"}},
                     {"attributes": {"vm_id": 272, "name": "different"}}]},
                {"mode": "managed", "type": "random_id", "name": "db",
                 "instances": [{"attributes": {"vm_id": 272, "name": "db-02"}}]},
            ]
        }
        with tempfile.TemporaryDirectory() as temp:
            state_file = Path(temp) / "terraform.tfstate"
            state_file.write_text(json.dumps(state), encoding="utf-8")
            with mock.patch.object(guard, "STATE_PATH", state_file):
                self.assertEqual(guard.check_state_ownership(), {271})

    def make_read(self, guest=None, config=None, ha=None):
        guest = guest or sample_vm()
        config = config or sample_conf()
        ha = ha or []
        def read(*args):
            endpoint = args[2] if args[:2] == ("pvesh", "get") else ""
            if endpoint == "/cluster/resources":
                return [guest]
            if endpoint == "/cluster/ha/resources":
                return ha
            if endpoint == "/nodes/node2/qemu/271/config":
                return config
            raise AssertionError(f"Unexpected PVE read: {args}")
        return read

    def test_detects_missing_disk_refs_but_never_detaches_live_images(self):
        images = {"general_pool": {"vm-271-cloudinit"},
                  "db_pool": {"vm-271-disk-0"}}
        with mock.patch.object(guard, "check_state_ownership", return_value={271}), \
             mock.patch.object(guard, "remote_json", side_effect=self.make_read()), \
             mock.patch.object(guard, "rbd_images", return_value=images):
            self.assertEqual(guard.proposed_repairs(), [
                ("node2", 271, ("scsi0",))
            ])

    def test_refuses_running_guest_even_when_disk_missing(self):
        with mock.patch.object(guard, "check_state_ownership", return_value={271}), \
             mock.patch.object(guard, "remote_json",
                               side_effect=self.make_read(guest=sample_vm(status="running"))), \
             mock.patch.object(guard, "rbd_images",
                               return_value={"general_pool": set(), "db_pool": set()}), \
             mock.patch.object(guard, "remote") as remote:
            with self.assertRaisesRegex(guard.RecoveryError, "not stopped"):
                guard.repair_missing_references()
            remote.assert_not_called()

    def test_refuses_ha_managed_guest(self):
        with mock.patch.object(guard, "check_state_ownership", return_value={271}), \
             mock.patch.object(guard, "remote_json",
                               side_effect=self.make_read(ha=[{"sid": "vm:271"}])), \
             mock.patch.object(guard, "rbd_images",
                               return_value={"general_pool": set(), "db_pool": set()}):
            with self.assertRaisesRegex(guard.RecoveryError, "HA-managed"):
                guard.proposed_repairs()

    def test_refuses_unknown_extra_volume(self):
        config = sample_conf()
        config["unused0"] = "general_pool:vm-271-disk-5"
        with mock.patch.object(guard, "check_state_ownership", return_value={271}), \
             mock.patch.object(guard, "remote_json",
                               side_effect=self.make_read(config=config)), \
             mock.patch.object(guard, "rbd_images",
                               return_value={"general_pool": set(), "db_pool": set()}):
            with self.assertRaisesRegex(guard.RecoveryError, "unknown disk"):
                guard.proposed_repairs()

    def test_refuses_storage_volume_name_drift(self):
        config = sample_conf()
        config["scsi0"] = "general_pool:vm-999-disk-0"
        with mock.patch.object(guard, "check_state_ownership", return_value={271}), \
             mock.patch.object(guard, "remote_json",
                               side_effect=self.make_read(config=config)), \
             mock.patch.object(guard, "rbd_images",
                               return_value={"general_pool": set(), "db_pool": set()}):
            with self.assertRaisesRegex(guard.RecoveryError, "unexpected volume"):
                guard.proposed_repairs()

    def test_recovery_detaches_only_missing_reference_then_verifies(self):
        config = sample_conf()
        reads = self.make_read(config=config)
        images = {"general_pool": {"vm-271-cloudinit"},
                  "db_pool": {"vm-271-disk-0"}}

        def update(*args):
            self.assertEqual(args[:3], (
                "pvesh", "set", "/nodes/node2/qemu/271/config"))
            self.assertEqual(args[3:], ("--delete", "scsi0"))
            del config["scsi0"]
            return ""

        with mock.patch.object(guard, "check_state_ownership", return_value={271}), \
             mock.patch.object(guard, "remote_json", side_effect=reads), \
             mock.patch.object(guard, "rbd_images", return_value=images), \
             mock.patch.object(guard, "remote", side_effect=update) as remote:
            guard.repair_missing_references()
            remote.assert_called_once()

    def test_no_missing_refs_means_no_automatic_retry(self):
        images = {
            "general_pool": {"vm-271-disk-0", "vm-271-cloudinit"},
            "db_pool": {"vm-271-disk-0"},
        }
        with mock.patch.object(guard, "check_state_ownership", return_value={271}), \
             mock.patch.object(guard, "remote_json", side_effect=self.make_read()), \
             mock.patch.object(guard, "rbd_images", return_value=images):
            with self.assertRaisesRegex(guard.RecoveryError, "no verified partial"):
                guard.repair_missing_references()

    def test_verify_clean_detects_orphan_171_and_does_not_mutate(self):
        with mock.patch.object(guard, "remote_json", return_value=[]), \
             mock.patch.object(guard, "rbd_images",
                               return_value={"general_pool": {"vm-171-disk-0"},
                                             "db_pool": set()}), \
             mock.patch.object(guard, "remote") as remote:
            with self.assertRaisesRegex(guard.RecoveryError, "vm-171-disk-0"):
                guard.verify_clean()
            remote.assert_not_called()

    def test_verify_clean_ignores_golden_templates_and_runner(self):
        guests = [
            {"type": "lxc", "vmid": 120, "name": "runner"},
            {"type": "qemu", "vmid": 9001, "name": "golden-db"},
        ]
        with mock.patch.object(guard, "remote_json", return_value=guests), \
             mock.patch.object(guard, "rbd_images",
                               return_value={"general_pool": {"vm-9001-disk-0"},
                                             "db_pool": set()}):
            guard.verify_clean()

    def test_workflow_parallelism_and_single_safe_retry(self):
        workflow = (Path(__file__).resolve().parents[1] /
                    ".github/workflows/02-destroy-infra.yml").read_text()
        self.assertIn('default: "2"', workflow)
        self.assertIn('1|2) echo', workflow)
        self.assertIn("repair-missing-references", workflow)
        self.assertIn("verify-clean", workflow)
        self.assertEqual(workflow.count("terraform destroy -auto-approve"), 2)


if __name__ == "__main__":
    unittest.main()
