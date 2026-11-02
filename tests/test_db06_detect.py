"""Pure state transition checks for the DB06 no-rebootstrap gate."""
import importlib.util
from pathlib import Path
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "scripts/db06_detect.py"
spec = importlib.util.spec_from_file_location("db06_detect", SOURCE)
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)

Snapshot = module.Snapshot
detect = module.detect


class DB06StateMachine(unittest.TestCase):
    primary = Snapshot("f", "1000", 1, 0)
    replica = Snapshot("t", "1000", 0, 1)

    def test_existing_healthy_pair_skips_bootstrap(self):
        for mode in ("full", "verify", "ha-readiness", "patroni-stage"):
            self.assertFalse(detect(self.primary, self.replica, mode, False))

    def test_initial_standalone_requires_explicit_confirmation(self):
        empty = Snapshot("f", "2000", 0, 0)
        with self.assertRaisesRegex(ValueError, "APPLY_DB_REPLICATION"):
            detect(self.primary, empty, "full", False)
        self.assertTrue(detect(self.primary, empty, "full", True))

    def test_verify_never_initializes_standby(self):
        with self.assertRaisesRegex(ValueError, "not set up"):
            detect(self.primary, Snapshot("f", "2000", 0, 0), "verify", True)

    def test_partial_streaming_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "broken"):
            detect(self.primary, Snapshot("t", "1000", 0, 0), "full", True)

    def test_wrong_lineage_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "broken"):
            detect(self.primary, Snapshot("t", "2000", 0, 1), "full", True)

    def test_promoted_old_standby_blocks_pipeline(self):
        with self.assertRaisesRegex(ValueError, "DB01 is not"):
            detect(Snapshot("t", "1000", 0, 1), Snapshot("f", "1000", 1, 0), "full", True)

    def test_two_writers_same_lineage_are_blocked(self):
        with self.assertRaisesRegex(ValueError, "split-brain"):
            detect(self.primary, Snapshot("f", "1000", 0, 0), "full", True)


if __name__ == "__main__":
    unittest.main()
