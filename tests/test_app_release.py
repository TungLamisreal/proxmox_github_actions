import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "set_app_release.py"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
BACKEND = "ghcr.io/tunglamisreal/proxmox_github_actions/backend@" + DIGEST_A
FRONTEND = "ghcr.io/tunglamisreal/proxmox_github_actions/frontend@" + DIGEST_B


class ReleaseStateTests(unittest.TestCase):
    def test_atomic_manifest_written_with_immutable_images(self):
        with tempfile.TemporaryDirectory() as d:
            state = pathlib.Path(d) / "app-release.json"
            subprocess.run([
                sys.executable, str(SCRIPT),
                "--state", str(state),
                "--backend", BACKEND,
                "--frontend", FRONTEND,
                "--version", "c" * 40,
            ], check=True)
            data = json.loads(state.read_text())
            self.assertEqual(data["backend_image"], BACKEND)
            self.assertEqual(data["frontend_image"], FRONTEND)
            self.assertEqual(data["version"], "c" * 40)
            self.assertEqual(state.stat().st_mode & 0o777, 0o600)

    def test_invalid_reference_does_not_overwrite_previous_release(self):
        with tempfile.TemporaryDirectory() as d:
            state = pathlib.Path(d) / "app-release.json"
            state.write_text('{"version":"old"}\n')
            result = subprocess.run([
                sys.executable, str(SCRIPT),
                "--state", str(state),
                "--backend", "ghcr.io/example/backend:latest",
                "--frontend", FRONTEND,
                "--version", "c" * 40,
            ], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(state.read_text())["version"], "old")


if __name__ == "__main__":
    unittest.main()
