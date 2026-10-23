"""Release integrity failures must be detected before distributing an executable."""

import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

spec = importlib.util.spec_from_file_location(
    "release_verifier", Path(__file__).resolve().parents[1] / "tools/verify_release.py"
)
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def zipped(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return stream.getvalue()


class ReleaseVerificationTests(unittest.TestCase):
    def receipt(self):
        return {
            "ok": True,
            "frozen": True,
            "checks": [{"name": "runtime", "ok": True}],
            "exe_sha256": hashlib.sha256(b"MZfixture").hexdigest(),
        }

    def test_receipt_rejects_changed_executable(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            verifier.verify_receipt(self.receipt(), b"MZreplacement")

    def test_receipt_rejects_empty_failed_and_non_frozen_checks(self):
        for changes in (
            {"checks": []},
            {"checks": [{"ok": False}]},
            {"checks": [None]},
            {"frozen": False},
            {"ok": False},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                verifier.verify_receipt(dict(self.receipt(), **changes), b"MZfixture")

    def test_packaging_requires_matching_runtime_version(self):
        for version in (None, "0.0.0"):
            with (
                self.subTest(version=version),
                self.assertRaisesRegex(ValueError, "runtime version"),
            ):
                verifier.verify_receipt(
                    dict(self.receipt(), version=version), b"MZfixture", expected_version="1.0.0"
                )
        verifier.verify_receipt(
            dict(self.receipt(), version="1.0.0"), b"MZfixture", expected_version="1.0.0"
        )

    def release(self, directory, embedded=None):
        source_name = "iPhoneIPAManager-1.0-source.zip"
        source = zipped({"README.md": b"fixture"})
        windows = zipped(
            {
                "iPhoneIPAManager.exe": b"MZfixture",
                "self-test.json": json.dumps(self.receipt()),
                "source/" + source_name: source if embedded is None else embedded,
            }
        )
        files = {source_name: source, "iPhoneIPAManager-1.0-windows-x64.zip": windows}
        for name, data in files.items():
            (directory / name).write_bytes(data)
        (directory / "SHA256SUMS.txt").write_text(
            "".join(
                hashlib.sha256(data).hexdigest() + "  " + name + "\n"
                for name, data in files.items()
            ),
            encoding="ascii",
        )

    def test_complete_matching_release_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.release(root)
            self.assertTrue(verifier.verify_release(root)["ok"])

    def test_tampered_archive_fails_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.release(root)
            (root / "iPhoneIPAManager-1.0-source.zip").write_bytes(b"replacement")
            with self.assertRaisesRegex(ValueError, "checksum mismatch"):
                verifier.verify_release(root)

    def test_matching_checksums_do_not_hide_wrong_embedded_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.release(root, embedded=zipped({"README.md": b"other revision"}))
            with self.assertRaisesRegex(ValueError, "Embedded source differs"):
                verifier.verify_release(root)

    def test_unsafe_archive_members_are_rejected_without_extraction(self):
        for name in ("../secret", "/absolute", "C:/secret", "folder\\secret"):
            content = zipped({name: b"x"})
            content = content.replace(name.replace("\\", "/").encode(), name.encode())
            with self.subTest(name=name), zipfile.ZipFile(io.BytesIO(content)) as z:
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    verifier.verify_members(z)


if __name__ == "__main__":
    unittest.main()
