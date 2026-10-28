from fixtures import write_sample_app
import tempfile
import unittest
import zipfile
from pathlib import Path

from iphone_detector.ipa_inbox import import_ipa


class InboxTests(unittest.TestCase):
    def test_malformed_xml_and_dates_report_invalid_metadata(self):
        from iphone_detector.ipa_inbox import validate_ipa

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.ipa"
            for raw in (
                b"<plist><dict>",
                b"<plist><dict><key>date</key><date>broken</date></dict></plist>",
            ):
                with self.subTest(raw=raw):
                    with zipfile.ZipFile(path, "w") as archive:
                        archive.writestr("Payload/App.app/Info.plist", raw)
                    with self.assertRaisesRegex(ValueError, "invalid app metadata"):
                        validate_ipa(path)

    def test_import_name_collision_never_overwrites_an_existing_ipa(self):
        from unittest.mock import Mock, patch

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Sample.ipa"
            managed = root / "managed"
            managed.mkdir()
            with zipfile.ZipFile(source, "w") as archive:
                write_sample_app(archive)
            existing = managed / ("Sample-" + "a" * 12 + ".ipa")
            existing.write_bytes(b"preserve-existing")
            with patch("iphone_detector.ipa_inbox.uuid.uuid4", return_value=Mock(hex="a" * 32)):
                with self.assertRaises(FileExistsError):
                    import_ipa(source, managed)
            self.assertEqual(existing.read_bytes(), b"preserve-existing")
            self.assertEqual(list(managed.iterdir()), [existing])
            self.assertTrue(source.exists())

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
                    write_sample_app(archive, "Payload/Test.app/Info.plist")
                    archive.writestr(bad, b"bad")
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    validate_ipa(source)

    def test_multiple_main_apps_are_rejected(self):
        from iphone_detector.ipa_inbox import validate_ipa

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "ambiguous.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                write_sample_app(archive, "Payload/One.app/Info.plist")
                write_sample_app(archive, "Payload/Two.app/Info.plist")
            with self.assertRaisesRegex(ValueError, "exactly one"):
                validate_ipa(source)

    def test_valid_import_preserves_source_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "New Version.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                write_sample_app(archive, "Payload/Test.app/Info.plist")
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
