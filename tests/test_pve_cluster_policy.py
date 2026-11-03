"""Safe offline contracts for the Proxmox HA/startup policy: no PVE access required."""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import pve_cluster_policy as ha


class PveClusterPolicyTests(unittest.TestCase):
    def test_only_pets_are_ha_managed(self):
        self.assertEqual({item[0] for item in ha.HA},
                         {"ct:121", "vm:271", "vm:272", "ct:141", "ct:142"})
        self.assertNotIn("ct:120", [item[0] for item in ha.HA])
        self.assertEqual(ha.HA[2], ("vm:272", "meow-db-02", "node3:2,node1:1,node2:1", "qemu", 272))
        self.assertEqual(dict(ha.SPREAD_RULES)["meow-etcd-voter-spread"],
                         ("ct:121", "vm:271", "vm:272"))
        self.assertEqual(dict(ha.SPREAD_RULES)["meow-lb-spread"],
                         ("ct:141", "ct:142"))
        for _, _, nodes, _, _ in ha.HA:
            self.assertEqual(set(ha.normalize_nodes(nodes)), set(ha.NODES))

    def test_pve9_affinity_rule_has_exact_scope_and_non_strict_preference(self):
        sid, name, nodes, _, _ = ha.HA[0]
        rule = {
            "rule": name, "type": "node-affinity", "resources": sid,
            "nodes": nodes, "affinity": "positive", "strict": 0
        }
        self.assertTrue(ha.rule_matches(rule, sid, nodes))
        self.assertFalse(ha.rule_matches({**rule, "strict": 1}, sid, nodes))
        self.assertFalse(ha.rule_matches({**rule, "affinity": "negative"}, sid, nodes))
        self.assertFalse(ha.rule_matches({**rule, "disable": 1}, sid, nodes))
        self.assertFalse(ha.rule_matches({**rule, "nodes": "node1:2,node2:1"}, sid, nodes))
        self.assertFalse(ha.rule_matches({**rule, "resources": "ct:121,ct:120"}, sid, nodes))
        self.assertFalse(ha.rule_matches({**rule, "type": "resource-affinity"}, sid, nodes))

    def test_resource_requires_no_failback_and_no_rebalance(self):
        expected = {"state": "started", "failback": 0, "auto-rebalance": 0}
        self.assertTrue(ha.resource_matches(expected))
        self.assertFalse(ha.resource_matches({**expected, "failback": 1}))
        self.assertFalse(ha.resource_matches({**expected, "auto-rebalance": 1}))
        self.assertFalse(ha.resource_matches({**expected, "state": "stopped"}))
        self.assertFalse(ha.resource_matches({"state": "started"}))

    def test_apply_refuses_preexisting_wrong_failback(self):
        sid, name, nodes, _, _ = ha.HA[0]
        rules = {
            name: {"rule": name, "type": "node-affinity",
                   "resources": sid, "nodes": nodes,
                   "affinity": "positive", "strict": 0}
        }
        existing = {
            sid: {"state": "started", "failback": 1, "auto-rebalance": 0}
        }
        with mock.patch.object(ha, "check_cluster"), mock.patch.object(ha, "check_guests"), \
             mock.patch.object(ha, "ha_rules", return_value=rules), \
             mock.patch.object(ha, "ha_resources", return_value=existing), \
             mock.patch.object(ha, "remote") as remote:
            with self.assertRaises(ha.PolicyError):
                ha.apply_ha()
            remote.assert_not_called()

    def test_apply_refuses_foreign_service_before_any_write(self):
        conflicting = {"ct:121": {"state": "started"}}
        with mock.patch.object(ha, "check_cluster"), mock.patch.object(ha, "check_guests"), \
             mock.patch.object(ha, "ha_rules", return_value={}), \
             mock.patch.object(ha, "ha_resources", return_value=conflicting), \
             mock.patch.object(ha, "remote") as remote:
            with self.assertRaises(ha.PolicyError):
                ha.apply_ha()
            remote.assert_not_called()

    def test_apply_refuses_foreign_affinity_rule_before_any_write(self):
        foreign = {
            "someone-else": {
                "type": "node-affinity", "resources": "ct:121",
                "nodes": "node1,node2", "affinity": "positive"
            }
        }
        with mock.patch.object(ha, "check_cluster"), mock.patch.object(ha, "check_guests"), \
             mock.patch.object(ha, "ha_rules", return_value=foreign), \
             mock.patch.object(ha, "ha_resources", return_value={}), \
             mock.patch.object(ha, "remote") as remote:
            with self.assertRaises(ha.PolicyError):
                ha.apply_ha()
            remote.assert_not_called()

    def test_apply_creates_services_and_soft_spread_without_weighted_node_rules(self):
        rules, resources, calls = {}, {}, []

        def update(*args):
            calls.append(args)
            if args[:3] == ("pvesh", "create", "/cluster/ha/resources"):
                sid = args[args.index("--sid") + 1]
                self.assertEqual(args[args.index("--failback") + 1], "0")
                self.assertEqual(args[args.index("--auto-rebalance") + 1], "0")
                resources[sid] = {
                    "sid": sid, "state": "started",
                    "failback": 0, "auto-rebalance": 0
                }
            elif args[:3] == ("pvesh", "create", "/cluster/ha/rules"):
                self.assertEqual(args[args.index("--type") + 1], "resource-affinity")
                self.assertEqual(args[args.index("--affinity") + 1], "negative")
                self.assertEqual(args[args.index("--strict") + 1], "0")
                name = args[args.index("--rule") + 1]
                rules[name] = {
                    "rule": name, "type": "resource-affinity",
                    "resources": args[args.index("--resources") + 1],
                    "affinity": "negative", "strict": 0
                }
            else:
                self.fail(f"Unexpected write: {args}")
            return ""

        with mock.patch.object(ha, "check_cluster"), mock.patch.object(ha, "check_guests"), \\
             mock.patch.object(ha, "ha_rules", side_effect=lambda: dict(rules)), \\
             mock.patch.object(ha, "ha_resources", side_effect=lambda: dict(resources)), \\
             mock.patch.object(ha, "remote", side_effect=update):
            ha.apply_ha()
            self.assertEqual(len(calls), len(ha.HA) + len(ha.SPREAD_RULES))
            self.assertEqual([call[2] for call in calls[:5]],
                             ["/cluster/ha/resources"] * len(ha.HA))
            self.assertEqual([call[2] for call in calls[5:]],
                             ["/cluster/ha/rules"] * len(ha.SPREAD_RULES))
            ha.apply_ha()
            self.assertEqual(len(calls), len(ha.HA) + len(ha.SPREAD_RULES))
            self.assertNotIn("ct:120", resources)

    def test_resume_failed_01_migrates_only_owned_node_rules(self):
        resources = {
            sid: {"sid": sid, "state": "started", "failback": 0, "auto-rebalance": 0}
            for sid, _, _, _, _ in ha.HA
        }
        resources["ct:120"] = {"sid": "ct:120", "state": "started"}
        rules = {
            name: {"rule": name, "type": "node-affinity", "affinity": "positive",
                   "strict": 0, "nodes": nodes, "resources": sid}
            for sid, name, nodes, _, _ in ha.HA
        }
        calls = []

        def update(*args):
            calls.append(args)
            if args[:3] == ("pvesh", "delete", args[2]):
                rule_name = args[2].rsplit("/", 1)[-1]
                self.assertIn(rule_name, rules)
                del rules[rule_name]
            elif args[:3] == ("pvesh", "create", "/cluster/ha/rules"):
                self.assertEqual(args[args.index("--type") + 1], "resource-affinity")
                name = args[args.index("--rule") + 1]
                rules[name] = {"rule": name, "type": "resource-affinity",
                               "affinity": "negative", "strict": 0,
                               "resources": args[args.index("--resources") + 1]}
            else:
                self.fail(f"Unexpected HA modification: {args}")
            return ""

        with mock.patch.object(ha, "check_cluster"), mock.patch.object(ha, "check_guests"), \\
             mock.patch.object(ha, "ha_rules", side_effect=lambda: dict(rules)), \\
             mock.patch.object(ha, "ha_resources", side_effect=lambda: dict(resources)), \\
             mock.patch.object(ha, "remote", side_effect=update):
            ha.apply_ha()
            self.assertEqual(len(calls), len(ha.HA) + len(ha.SPREAD_RULES))
            self.assertEqual(set(rules), set(dict(ha.SPREAD_RULES)))
            self.assertIn("ct:120", resources)
            ha.apply_ha()
            self.assertEqual(len(calls), len(ha.HA) + len(ha.SPREAD_RULES))

    def test_partial_01_without_owned_rules_requires_state_proof(self):
        resources = {
            "vm:271": {"sid": "vm:271", "state": "started",
                       "failback": 0, "auto-rebalance": 0}
        }
        with mock.patch.object(ha, "check_cluster"), mock.patch.object(ha, "check_guests"), \\
             mock.patch.object(ha, "ha_rules", return_value={}), \\
             mock.patch.object(ha, "ha_resources", return_value=resources), \\
             mock.patch.object(ha, "prove_orphaned_ha_ownership",
                               side_effect=ha.PolicyError("No ownership proof")), \\
             mock.patch.object(ha, "remote") as remote:
            with self.assertRaisesRegex(ha.PolicyError, "No ownership proof"):
                ha.apply_ha()
            remote.assert_not_called()

    def test_remove_ha_leaves_ct120_untouched(self):
        resources = {
            "ct:120": {"state": "started"},
            "ct:121": {"state": "started", "failback": 0, "auto-rebalance": 0},
            "vm:271": {"state": "started", "failback": 0, "auto-rebalance": 0},
            "vm:272": {"state": "started", "failback": 0, "auto-rebalance": 0},
            "ct:141": {"state": "started", "failback": 0, "auto-rebalance": 0},
            "ct:142": {"state": "started", "failback": 0, "auto-rebalance": 0},
        }
        rules = {
            name: {"rule": name, "type": "node-affinity",
                   "resources": sid, "nodes": nodes,
                   "affinity": "positive", "strict": 0}
            for sid, name, nodes, _, _ in ha.HA
        }
        with mock.patch.object(ha, "check_cluster"), \
             mock.patch.object(ha, "ha_resources", return_value=resources), \
             mock.patch.object(ha, "ha_rules", return_value=rules), \
             mock.patch.object(ha, "remote") as remote:
            ha.remove_ha()
            calls = [" ".join(str(v) for v in call.args) for call in remote.call_args_list]
            self.assertEqual(len(calls), len(ha.HA) * 2)
            self.assertIn("/cluster/ha/rules/meow-observer", calls[0])
            self.assertIn("/cluster/ha/resources/ct:121", calls[1])
            self.assertIn("/cluster/ha/rules/meow-db-primary", calls[2])
            self.assertIn("/cluster/ha/resources/vm:271", calls[3])
            self.assertIn("/cluster/ha/rules/meow-db-02", calls[4])
            self.assertIn("/cluster/ha/resources/vm:272", calls[5])
            self.assertIn("/cluster/ha/resources/ct:141", calls[7])
            self.assertIn("/cluster/ha/resources/ct:142", calls[9])
            self.assertTrue(all("ct:120" not in call for call in calls))

    def test_remove_ha_accepts_valid_spread_only_policy(self):
        resources = {
            sid: {"sid": sid, "state": "started", "failback": 0, "auto-rebalance": 0}
            for sid, _, _, _, _ in ha.HA
        }
        resources["ct:120"] = {"state": "started"}
        rules = {
            name: {"rule": name, "type": "resource-affinity", "affinity": "negative",
                   "strict": 0, "resources": ",".join(members)}
            for name, members in ha.SPREAD_RULES
        }
        with mock.patch.object(ha, "check_cluster"), \\
             mock.patch.object(ha, "ha_rules", return_value=rules), \\
             mock.patch.object(ha, "ha_resources", return_value=resources), \\
             mock.patch.object(ha, "remote") as remote:
            ha.remove_ha()
            commands = [" ".join(str(v) for v in c.args)
                        for c in remote.call_args_list]
            self.assertEqual(len(commands), len(ha.HA) + len(ha.SPREAD_RULES))
            self.assertIn("/cluster/ha/rules/meow-etcd-voter-spread", commands[0])
            self.assertIn("/cluster/ha/rules/meow-lb-spread", commands[1])
            self.assertTrue(all("ct:120" not in v for v in commands))

    def test_remove_empty_ha_is_a_noop(self):
        with mock.patch.object(ha, "check_cluster"), \
             mock.patch.object(ha, "ha_resources", return_value={}), \
             mock.patch.object(ha, "ha_rules", return_value={}), \
             mock.patch.object(ha, "remote") as remote:
            ha.remove_ha()
            remote.assert_not_called()

    def test_remove_refuses_ambiguous_ownership(self):
        with mock.patch.object(ha, "check_cluster"), \
             mock.patch.object(ha, "ha_resources", return_value={"vm:271": {"state": "started"}}), \
             mock.patch.object(ha, "ha_rules", return_value={}), \
             mock.patch.object(ha, "remote") as remote:
            with self.assertRaises(ha.PolicyError):
                ha.remove_ha()
            remote.assert_not_called()

    def test_legacy_destroy_requires_identity_proof_before_writes(self):
        expected_resources = {
            "vm:271": {"state": "started", "failback": 0, "auto-rebalance": 0}
        }
        with mock.patch.object(ha, "check_cluster"), \
             mock.patch.object(ha, "ha_resources", return_value=expected_resources), \
             mock.patch.object(ha, "ha_rules", return_value={}), \
             mock.patch.object(ha, "prove_orphaned_ha_ownership") as prove, \
             mock.patch.object(ha, "remote") as remote:
            prove.side_effect = ha.PolicyError("Terraform state not verified")
            with self.assertRaises(ha.PolicyError):
                ha.remove_ha(allow_owned_missing_rule=True)
            prove.assert_called_once_with({"vm:271"})
            remote.assert_not_called()

    def test_legacy_destroy_removes_verified_ruleless_db_without_ct120(self):
        resources = {
            "ct:120": {"state": "started"},
            "vm:271": {"state": "started", "failback": 0, "auto-rebalance": 0},
            "vm:272": {"state": "started", "failback": 0, "auto-rebalance": 0}
        }
        with mock.patch.object(ha, "check_cluster"), \
             mock.patch.object(ha, "ha_resources", return_value=resources), \
             mock.patch.object(ha, "ha_rules", return_value={}), \
             mock.patch.object(ha, "prove_orphaned_ha_ownership") as prove, \
             mock.patch.object(ha, "remote") as remote:
            ha.remove_ha(allow_owned_missing_rule=True)
            prove.assert_called_once_with({"vm:271", "vm:272"})
            commands = [" ".join(str(v) for v in c.args) for c in remote.call_args_list]
            self.assertEqual(len(commands), 2)
            self.assertTrue(any("/cluster/ha/resources/vm:271" in v for v in commands))
            self.assertTrue(any("/cluster/ha/resources/vm:272" in v for v in commands))
            self.assertTrue(all("ct:120" not in v for v in commands))

    def test_spread_rule_requires_correct_members_and_negative_preference(self):
        base = {
            "type": "resource-affinity", "affinity": "negative",
            "strict": 0, "resources": "ct:141,ct:142",
        }
        members = ("ct:141", "ct:142")
        self.assertTrue(ha.spread_rule_matches(base, members))
        self.assertFalse(ha.spread_rule_matches({**base, "affinity": "positive"}, members))
        self.assertFalse(ha.spread_rule_matches({**base, "strict": 1}, members))
        self.assertFalse(ha.spread_rule_matches({**base, "resources": "ct:141,ct:120"}, members))

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
