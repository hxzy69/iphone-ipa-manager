import plistlib
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from iphone_detector import ipa_library as library


class LibraryTests(unittest.TestCase):
    def test_readable_metadata_does_not_bypass_full_assignment_checks(self):
        from iphone_detector.discord_inbox import assign_managed

        self.write()  # Browsable manifest, but no executable declared.
        (item,) = library.scan(self.config)
        self.assertFalse(item.error)
        with (
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch("iphone_detector.config.save_config") as save,
        ):
            with self.assertRaises(ValueError):
                assign_managed(item.path, item.signature, 0, expected_email="one@example.com")
            save.assert_not_called()

    def test_assignment_marks_new_ipa_for_full_install_and_renewal(self):
        from fixtures import write_sample_app
        from iphone_detector.discord_inbox import assign_managed

        path = self.folder / "NewVideo.ipa"
        with zipfile.ZipFile(path, "w") as archive:
            write_sample_app(archive)
        (item,) = library.scan(self.config)
        with (
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch("iphone_detector.config.save_config") as save,
        ):
            assign_managed(item.path, item.signature, 0, expected_email="one@example.com")
            saved = save.call_args.args[0]["automation"]
            self.assertEqual(saved["accounts"][0]["ipa"], "NewVideo")
            self.assertEqual(saved["pending_ipa_installs"], {"1": "NewVideo"})

    def test_closed_window_cancels_remaining_metadata_reads(self):
        self.write()
        stop = threading.Event()
        stop.set()
        with patch.object(library, "_metadata", side_effect=AssertionError("Cancelled scan read")):
            self.assertEqual(library.scan(self.config, stop=stop), [])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.addCleanup(library.clear_cache)
        self.folder = Path(self.directory.name)
        self.config = {
            "automation": {
                "ipa_directory": str(self.folder),
                "accounts": [{"email": "one@example.com", "ipa": "Video"}],
            }
        }

    def write(self, name="Video.ipa", info=None):
        path = self.folder / name
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "Payload/Video.app/Info.plist",
                plistlib.dumps(
                    info
                    or {
                        "CFBundleDisplayName": "Video Player",
                        "CFBundleIdentifier": "org.example.video",
                        "CFBundleShortVersionString": "2.3",
                        "CFBundleVersion": "42",
                    }
                ),
            )
            archive.writestr("Payload/Video.app/Video", b"binary")
        return path

    def test_metadata_search_and_case_insensitive_assignments(self):
        self.write("VIDEO.IPA")
        (item,) = library.scan(self.config)
        self.assertEqual(
            (item.name, item.version, item.bundle_id, item.accounts),
            ("Video Player", "2.3", "org.example.video", (1,)),
        )
        for query in ("player", "2.3", "org.example", "video.ipa", "  VIDEO  "):
            self.assertTrue(item.matches(query), query)
        self.assertFalse(item.matches("spotify"))
        self.assertFalse(item.error)

    def test_browsing_never_runs_full_archive_validation(self):
        self.write()
        with patch(
            "iphone_detector.ipa_inbox.validate_ipa",
            side_effect=AssertionError("Full archive read"),
        ):
            self.assertEqual(len(library.scan(self.config)), 1)

    def test_unreadable_file_does_not_hide_good_files(self):
        self.write()
        (self.folder / "partial.ipa").write_bytes(b"still receiving")
        (self.folder / "ignore.txt").write_text("ignore")
        items = library.scan(self.config)
        self.assertEqual(len(items), 2)
        self.assertTrue(next(item for item in items if item.path.name == "partial.ipa").error)

    def test_duplicate_and_oversized_manifests_are_not_read(self):
        path = self.write()
        with zipfile.ZipFile(path, "a") as archive:
            archive.writestr("Payload/Other.app/Info.plist", b"bad")
        self.assertTrue(library.scan(self.config)[0].error)
        library.clear_cache()
        self.write()
        with patch.object(library, "MAX_METADATA", 4):
            self.assertTrue(library.scan(self.config)[0].error)

    def test_bad_plist_and_wrong_value_types_are_unreadable(self):
        for value in (
            b"<plist><dict>",
            b"<plist><dict><key>date</key><date>broken</date></dict></plist>",
            b"bplist00\0\0",
            plistlib.dumps(["array"]),
            plistlib.dumps({"CFBundleIdentifier": 123, "CFBundleVersion": ["1"]}),
        ):
            with self.subTest(value=value):
                with zipfile.ZipFile(self.folder / "bad.ipa", "w") as archive:
                    archive.writestr("Payload/Bad.app/Info.plist", value)
                library.clear_cache()
                self.assertTrue(library.scan(self.config)[0].error)

    def test_fallbacks_and_control_character_sanitizing(self):
        self.write(
            info={
                "CFBundleName": " A\nB\tC ",
                "CFBundleIdentifier": "org.example.a",
                "CFBundleVersion": "4",
            }
        )
        (item,) = library.scan(self.config)
        self.assertEqual(item.name, "A B C")
        self.assertEqual(item.version, "4")

    def test_cache_updates_assignments_without_rereading_manifest(self):
        self.write()
        library.scan(self.config)
        with patch.object(library.zipfile, "ZipFile", side_effect=AssertionError("Not cached")):
            self.config["automation"]["accounts"][0]["ipa"] = "Other"
            self.assertEqual(library.scan(self.config)[0].accounts, ())

    def test_explicit_refresh_and_file_change_invalidate_metadata(self):
        self.write()
        library.scan(self.config)
        library.clear_cache()
        with patch.object(library.zipfile, "ZipFile", wraps=zipfile.ZipFile) as opening:
            library.scan(self.config)
            self.assertEqual(opening.call_count, 1)
        self.write(info={"CFBundleIdentifier": "org.example.changed", "CFBundleVersion": "123"})
        self.assertEqual(library.scan(self.config)[0].bundle_id, "org.example.changed")

    def test_unavailable_folder_is_actionable(self):
        self.config["automation"]["ipa_directory"] = str(self.folder / "missing")
        with self.assertRaisesRegex(ValueError, "IPA folder"):
            library.scan(self.config)

    def test_disappearing_file_does_not_abort_scan(self):
        path = self.write()
        original = library._metadata

        def remove(*args):
            result = original(*args)
            path.unlink()
            return result

        with patch.object(library, "_metadata", side_effect=remove):
            self.assertEqual(library.scan(self.config), [])

    def test_file_changing_during_read_is_not_assignable(self):
        path = self.write()
        original = library._metadata

        def change(*args):
            result = original(*args)
            with path.open("ab") as stream:
                stream.write(b"more data")
            return result

        with patch.object(library, "_metadata", side_effect=change):
            self.assertIn("changing", library.scan(self.config)[0].error)
