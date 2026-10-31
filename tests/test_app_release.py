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



DEMO_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "demo_release.py"
V1_SHA = "cd32cd5031cc37096fc64d0507d9328ae2329f32"


class DemoReleaseReplayTests(unittest.TestCase):
    def run_demo(self, directory, action, target, *extra):
        return subprocess.run(
            [sys.executable, str(DEMO_SCRIPT), action, "--state-dir", str(directory),
             "--target", target, *extra], capture_output=True, text=True)

    def test_baseline_only_publishes_after_explicit_confirmation(self):
        with tempfile.TemporaryDirectory() as d:
            directory = pathlib.Path(d)
            current = directory / "app-release.json"
            current.write_text('{"version":"untouched"}')
            preview = self.run_demo(directory, "resolve", "baseline")
            self.assertEqual(preview.returncode, 0, preview.stderr)
            self.assertEqual(json.loads(preview.stdout)["frontend_image"], "nginx:alpine")
            denied = self.run_demo(directory, "publish", "baseline")
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(json.loads(current.read_text())["version"], "untouched")
            applied = self.run_demo(directory, "publish", "baseline", "--confirm", "PUBLISH-VERIFIED-RELEASE")
            self.assertEqual(applied.returncode, 0, applied.stderr)
            self.assertEqual(json.loads(current.read_text())["backend_image"], "meow-backend:latest")
            self.assertEqual(current.stat().st_mode & 0o777, 0o600)

    def test_v1_v2_pinned_release_selection_and_publish(self):
        with tempfile.TemporaryDirectory() as d:
            directory = pathlib.Path(d)
            for label, version in (("v1", V1_SHA), ("v2", "f" * 40)):
                manifest = {"backend_image": BACKEND, "frontend_image": FRONTEND,
                            "version": version}
                (directory / ("app-release-" + label + ".json")).write_text(json.dumps(manifest))
                result = self.run_demo(directory, "resolve", label)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), manifest)
                applied = self.run_demo(directory, "publish", label, "--confirm", "PUBLISH-VERIFIED-RELEASE")
                self.assertEqual(applied.returncode, 0, applied.stderr)
                self.assertEqual(json.loads((directory / "app-release.json").read_text()), manifest)

    def test_invalid_v1_digest_or_sha_cannot_publish(self):
        with tempfile.TemporaryDirectory() as d:
            directory = pathlib.Path(d)
            current = directory / "app-release.json"
            current.write_text('{"version":"unchanged"}')
            bad = {"backend_image": BACKEND.replace("@sha256:", ":latest-sha256:"),
                   "frontend_image": FRONTEND, "version": V1_SHA}
            v1 = directory / "app-release-v1.json"
            v1.write_text(json.dumps(bad))
            denied = self.run_demo(directory, "publish", "v1", "--confirm", "PUBLISH-VERIFIED-RELEASE")
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(json.loads(current.read_text())["version"], "unchanged")
            bad["backend_image"] = BACKEND
            bad["version"] = "c" * 40
            v1.write_text(json.dumps(bad))
            denied = self.run_demo(directory, "resolve", "v1")
            self.assertNotEqual(denied.returncode, 0)


if __name__ == "__main__":
    unittest.main()
