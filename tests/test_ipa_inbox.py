import tempfile
import unittest
import zipfile
from pathlib import Path

from iphone_detector.ipa_inbox import import_ipa


class InboxTests(unittest.TestCase):
    def test_archive_paths_cannot_escape_the_app_directory(self):
        from iphone_detector.ipa_inbox import validate_ipa

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "unsafe.ipa"
            for bad in (
                "../outside.txt",
                "/absolute.txt",
                "C:/outside.txt",
                "Payload/../outside.txt",
            ):
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr("Payload/Test.app/Info.plist", b"plist")
                    archive.writestr(bad, b"bad")
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    validate_ipa(source)

    def test_multiple_main_apps_are_rejected(self):
        from iphone_detector.ipa_inbox import validate_ipa

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ambiguous.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("Payload/One.app/Info.plist", b"plist")
                archive.writestr("Payload/Two.app/Info.plist", b"plist")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                validate_ipa(source)

    def test_valid_import_preserves_source_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "New Version.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("Payload/Test.app/Info.plist", b"plist")
            first = import_ipa(source, root / "managed")
            second = import_ipa(source, root / "managed")
            self.assertNotEqual(first, second)
            self.assertEqual(first.read_bytes(), source.read_bytes())
            self.assertTrue(source.exists())

    def test_incomplete_file_rejected_and_temporary_removed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "incomplete.ipa"
            source.write_bytes(b"partial download")
            with self.assertRaises(ValueError):
                import_ipa(source, root / "managed")
            self.assertEqual(list((root / "managed").iterdir()), [])

    def test_non_app_archive_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "wrong.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("readme.txt", "Not an app")
            with self.assertRaises(ValueError):
                import_ipa(source, root / "managed")
