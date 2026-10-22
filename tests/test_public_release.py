"""Public artifact boundaries and optional setup validation."""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iphone_detector import remote_setup

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("public_audit", ROOT / "tools/audit_release.py")
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
license_spec = importlib.util.spec_from_file_location(
    "public_licenses", ROOT / "tools/collect_licenses.py"
)
licenses = importlib.util.module_from_spec(license_spec)
license_spec.loader.exec_module(licenses)


class ReleaseBoundaryTests(unittest.TestCase):
    def test_license_collection_never_distributes_code_or_bytecode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            legal, code, bytecode = root / "LICENSE.txt", root / "license.py", root / "LICENSE.pyc"
            for path in (legal, code, bytecode):
                path.write_bytes(b"example")
            with patch.object(licenses, "DIST", root):
                copied = licenses.copy_notices([legal, code, bytecode], root / "notices")
            self.assertEqual(len(copied), 1)
            self.assertTrue(copied[0].endswith("LICENSE.txt"))

    def test_license_collection_rejects_private_build_paths_in_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "NOTICE.txt"
            profile = "C:/PrivateBuildProfile"
            path.write_text(profile)
            with (
                patch.object(licenses, "DIST", root),
                patch.dict("os.environ", {"USERPROFILE": profile}),
            ):
                with self.assertRaisesRegex(ValueError, "build profile"):
                    licenses.copy_notices([path], root / "notices")

    def test_reviewed_transport_sources_default_to_userspace_without_wintun(self):
        import sys
        from pymobiledevice3.remote import tunnel_service

        self.assertTrue(Path(tunnel_service.__file__).is_relative_to(ROOT / "vendor/python"))
        self.assertTrue(tunnel_service.USE_USERSPACE_TUNNEL)
        self.assertNotIn("pytun_pmd3.wintun", sys.modules)

    def test_source_allowlist_excludes_builds_binaries_profiles_and_history(self):
        paths = [path.relative_to(ROOT) for path in audit.source_files()]
        for path in paths:
            self.assertFalse(set(path.parts) & audit.EXCLUDED)
            self.assertNotIn(path.suffix, (".exe", ".dll", ".zip", ".pyc"))
            self.assertNotIn(path.name, audit.PRIVATE_NAMES)
        self.assertIn(Path("native/Cargo.lock"), paths)
        self.assertIn(Path("vendor/isideload/LICENSE"), paths)

    def test_private_config_inside_allowed_folder_blocks_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "iphone_detector").mkdir()
            (root / "iphone_detector/discord.json").write_text("{}")
            (root / "config.example.json").write_text(
                json.dumps({"device": {}, "automation": {"accounts": []}})
            )
            result = audit.audit(root)
            self.assertFalse(result["ok"])
            self.assertEqual(result["findings"][0]["reason"], "private or unsafe release input")

    def test_remote_setup_rejects_public_loopback_and_non_vpn_addresses(self):
        for address in ("127.0.0.1", "192.168.1.10", "8.8.8.8", "not-an-address"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                remote_setup.tailscale_address(address)

    def test_remote_setup_needs_a_selected_device_before_network_access(self):
        with (
            patch("iphone_detector.config.load_config", return_value={}),
            patch.object(remote_setup, "tailscale_address") as network,
        ):
            with self.assertRaisesRegex(ValueError, "Select your iPhone"):
                remote_setup.configure()
            network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
