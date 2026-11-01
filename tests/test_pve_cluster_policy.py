"""Safe offline contracts for the Proxmox HA/startup policy: no PVE access required."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pve_cluster_policy as ha


class PveClusterPolicyTests(unittest.TestCase):
    def test_only_pets_are_ha_managed(self):
        self.assertEqual({item[0] for item in ha.HA}, {"ct:121", "vm:271"})
        self.assertNotIn("ct:120", [item[0] for item in ha.HA])
        self.assertNotIn("vm:272", [item[0] for item in ha.HA])
        for _, _, nodes, _, _ in ha.HA:
            self.assertEqual(set(ha.normalize_nodes(nodes)), set(ha.NODES))

    def test_ha_groups_require_nofailback_and_allow_any_survivor(self):
        _, _, nodes, _, _ = ha.HA[0]
        group = {"nodes": nodes, "nofailback": 1, "restricted": 0}
        self.assertTrue(ha.group_matches(group, nodes))
        self.assertFalse(ha.group_matches({**group, "nofailback": 0}, nodes))
        self.assertFalse(ha.group_matches({**group, "restricted": 1}, nodes))
        self.assertFalse(ha.group_matches({**group, "nodes": "node1:2,node2:1"}, nodes))

    def test_apply_refuses_foreign_resource_before_any_write(self):
        conflicting = {"ct:121": {"group": "custom-user-group", "state": "started"}}
        with mock.patch.object(ha, "check_cluster"), mock.patch.object(ha, "check_guests"), \
             mock.patch.object(ha, "ha_groups", return_value={}), \
             mock.patch.object(ha, "ha_resources", return_value=conflicting), \
             mock.patch.object(ha, "remote") as remote:
            with self.assertRaises(ha.PolicyError):
                ha.apply_ha()
            remote.assert_not_called()

    def test_remove_ha_leaves_user_managed_autovm_untouched(self):
        resources = {
            "ct:120": {"group": "user-autovm", "state": "started"},
            "ct:121": {"group": "meow-observer", "state": "started"},
            "vm:271": {"group": "meow-db-primary", "state": "started"},
        }
        groups = {
            "meow-observer": {"group": "meow-observer"},
            "meow-db-primary": {"group": "meow-db-primary"},
        }
        with mock.patch.object(ha, "check_cluster"), \
             mock.patch.object(ha, "ha_resources", return_value=resources), \
             mock.patch.object(ha, "ha_groups", return_value=groups), \
             mock.patch.object(ha, "remote") as remote:
            ha.remove_ha()
            calls = [" ".join(str(v) for v in call.args) for call in remote.call_args_list]
            self.assertEqual(len(calls), 4)
            self.assertTrue(all("ct:120" not in call for call in calls))
            self.assertTrue(any("ct:121" in call for call in calls))
            self.assertTrue(any("vm:271" in call for call in calls))

    def test_guest_startup_contains_only_previously_confirmed_db_values(self):
        self.assertEqual(ha.DB_GUEST_STARTUP, {
            271: "order=5,up=60",
            272: "order=4,up=60",
        })
        self.assertNotIn(120, ha.DB_GUEST_STARTUP)
        self.assertEqual(ha.parse_startup("up=60,order=4"), {"order": 4, "up": 60})

    def test_guest_startup_idempotent_and_does_not_touch_autovm(self):
        values = {
            271: {"startup": ""},
            272: {"startup": ""},
        }
        resources = [
            {"vmid": 271, "type": "qemu", "node": "node2"},
            {"vmid": 272, "type": "qemu", "node": "node3"},
            {"vmid": 120, "type": "lxc", "node": "node1"},
        ]

        def get_pve(*args):
            if args[1] == "/cluster/resources":
                return resources
            vmid = int(args[1].split("/")[-2])
            return values[vmid]

        def update(*args):
            vmid = int(args[2].split("/")[-2])
            self.assertIn(vmid, (271, 272))
            values[vmid]["startup"] = args[4]
            return ""

        with mock.patch.object(ha, "check_cluster"), \
             mock.patch.object(ha, "pvesh_json", side_effect=get_pve), \
             mock.patch.object(ha, "remote", side_effect=update) as remote:
            ha.set_guest_startup()
            self.assertEqual(remote.call_count, 2)
            ha.set_guest_startup()
            self.assertEqual(remote.call_count, 2)
            self.assertEqual(values[271]["startup"], "order=5,up=60")
            self.assertEqual(values[272]["startup"], "order=4,up=60")

    def test_guest_startup_refuses_missing_vm_without_mutation(self):
        with mock.patch.object(ha, "check_cluster"), \
             mock.patch.object(ha, "pvesh_json", return_value=[]), \
             mock.patch.object(ha, "remote") as remote:
            with self.assertRaises(ha.PolicyError):
                ha.set_guest_startup()
            remote.assert_not_called()

    def test_startup_delay_is_idempotently_applied_to_all_pve_nodes(self):
        values = {name: {"startall-onboot-delay": 0} for name in ha.NODES}
        def get_node(*args):
            node = args[1].split("/")[2]
            return values[node]
        def update(*args):
            node = args[2].split("/")[2]
            values[node]["startall-onboot-delay"] = ha.BOOT_DELAYS[node]
            return ""
        with mock.patch.object(ha, "check_cluster"), \
             mock.patch.object(ha, "pvesh_json", side_effect=get_node), \
             mock.patch.object(ha, "remote", side_effect=update) as remote:
            ha.set_startup_delay()
            self.assertEqual(remote.call_count, len(ha.NODES))
            ha.set_startup_delay()
            self.assertEqual(remote.call_count, len(ha.NODES))
        self.assertEqual({name: values[name]["startall-onboot-delay"] for name in ha.NODES}, ha.BOOT_DELAYS)


if __name__ == "__main__":
    unittest.main()
