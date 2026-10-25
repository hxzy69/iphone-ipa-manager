"""Release publishing refuses unreviewed source; advisory exceptions are narrowly scoped."""

import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch


def tool(name):
    path = Path(__file__).resolve().parents[1] / "tools" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BuildControlTests(unittest.TestCase):
    def test_tracked_build_metadata_blocks_publication(self):
        publisher = tool("publish_release")
        auditor = tool("audit_release")
        for tracked, allowed in (
            (b"README.md\0build-temp/Analysis.toc\0", ["README.md"]),
            (b"README.md\0", ["README.md", "missing.py"]),
        ):
            with (
                self.subTest(tracked=tracked),
                patch.dict(sys.modules, {"audit_release": auditor}),
                patch.object(auditor, "audit", return_value={"ok": True}),
                patch.object(
                    auditor,
                    "source_files",
                    return_value=[publisher.ROOT / name for name in allowed],
                ),
                patch.object(publisher.subprocess, "check_output", return_value=tracked),
            ):
                with self.assertRaisesRegex(ValueError, "allowlist"):
                    publisher.verify_tracked_source()

    def test_exact_tracked_allowlist_passes_publication_boundary(self):
        publisher = tool("publish_release")
        auditor = tool("audit_release")
        with (
            patch.dict(sys.modules, {"audit_release": auditor}),
            patch.object(auditor, "audit", return_value={"ok": True}),
            patch.object(auditor, "source_files", return_value=[publisher.ROOT / "README.md"]),
            patch.object(publisher.subprocess, "check_output", return_value=b"README.md\0"),
        ):
            publisher.verify_tracked_source()

    def test_setup_publishing_requires_matching_checksum_and_version(self):
        import hashlib

        publisher = tool("publish_release")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "artifacts"
            output.mkdir()
            name = "iPhoneIPAManager-1.2.3-setup-x64.exe"
            (output / name).write_bytes(b"MZsetup")
            digest = hashlib.sha256(b"MZsetup").hexdigest()
            (output / "SETUP-SHA256SUMS.txt").write_text(digest + "  " + name)
            with patch.object(publisher, "ROOT", root):
                self.assertEqual(
                    publisher.installer_assets("1.2.3"), [name, "SETUP-SHA256SUMS.txt"]
                )
                (output / name).write_bytes(b"modified")
                with self.assertRaises(ValueError):
                    publisher.installer_assets("1.2.3")

    def test_publisher_rejects_wrong_tag_before_any_github_operation(self):
        publisher = tool("publish_release")
        with patch.object(publisher, "gh") as gh:
            with self.assertRaises(ValueError):
                publisher.publish("v99.0.0-preview", "example/project")
            gh.assert_not_called()

    def test_publisher_rejects_dirty_source_without_contacting_github(self):
        from iphone_detector import __version__

        publisher = tool("publish_release")
        with (
            patch.object(
                publisher.subprocess,
                "check_output",
                side_effect=["same-sha", "same-sha", " M changed.py"],
            ),
            patch.object(publisher, "gh") as gh,
        ):
            with self.assertRaisesRegex(ValueError, "uncommitted"):
                publisher.publish(f"v{__version__}-preview", "example/project")
            gh.assert_not_called()

    def test_expired_or_wrong_version_advisory_exception_does_not_pass(self):
        auditor = tool("security_audit")
        package = {"package": {"name": "example", "ecosystem": "PyPI"}, "version": "1.0"}
        for version, expires in (("1.0", "2000-01-01"), ("2.0", "9999-12-31")):
            with (
                self.subTest(version=version, expires=expires),
                tempfile.TemporaryDirectory() as directory,
            ):
                root = Path(directory)
                (root / "third_party").mkdir()
                (root / "third_party/security-exceptions.json").write_text(
                    json.dumps(
                        [
                            {
                                "id": "EXAMPLE-1",
                                "name": "example",
                                "ecosystem": "PyPI",
                                "version": version,
                                "expires": expires,
                                "reason": "test fixture",
                            }
                        ]
                    ),
                    encoding="utf-8",
                )
                response = Mock()
                response.__enter__ = Mock(return_value=response)
                response.__exit__ = Mock(return_value=False)
                response.read.return_value = json.dumps(
                    {"results": [{"vulns": [{"id": "EXAMPLE-1"}]}]}
                ).encode()
                with (
                    patch.object(auditor, "ROOT", root),
                    patch.object(auditor, "packages", return_value=[package]),
                    patch.object(auditor, "urlopen", return_value=response),
                ):
                    result = auditor.audit()
                self.assertFalse(result["ok"])
                self.assertEqual(len(result["findings"]), 1)
                self.assertEqual(result["accepted"], [])

    def test_inventory_uses_canonical_package_urls(self):
        auditor = tool("security_audit")
        with patch.object(
            auditor,
            "packages",
            return_value=[
                {"package": {"name": "Some_Package", "ecosystem": "PyPI"}, "version": "1.0"}
            ],
        ):
            inventory = auditor.sbom()
        self.assertEqual(inventory["components"][0]["purl"], "pkg:pypi/some-package@1.0")
