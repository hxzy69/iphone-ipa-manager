"""Editable version settings, persistence, and stoppable USB watching."""

from fixtures import configured
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from iphone_detector import config
from iphone_detector.detector import IPhoneWatcher
from iphone_detector.settings import get_workflow, ipa_stem, update_settings
from fixtures import ACCOUNTS as DEFAULT_ACCOUNTS


class SettingsTests(unittest.TestCase):
    def test_corrupt_existing_config_is_never_replaced_by_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{broken")
            with patch.object(config, "CONFIG_PATH", path):
                with self.assertRaises(config.ConfigurationError):
                    config.load_config()
            self.assertEqual(path.read_text(), "{broken")

    def test_changing_folder_marks_same_named_ipas_for_full_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            executable = root / "iloader.exe"
            executable.write_bytes(b"test")
            before = configured({"automation": {"ipa_directory": str(old)}})
            result = update_settings(before, new, [a["ipa"] for a in DEFAULT_ACCOUNTS])
            self.assertEqual(
                result["automation"]["pending_ipa_installs"],
                {str(i + 1): a["ipa"] for i, a in enumerate(DEFAULT_ACCOUNTS)},
            )

    def test_version_update_changes_ocr_expected_name_and_keeps_account_order(self):
        saved = configured(
            {
                "automation": {
                    "accounts": [
                        {"ipa": "YouTube 22.1.0 (NewMod 2.0).ipa"},
                        {"ipa": "EeveeSpotify-8.0.0-10.1.2.ipa"},
                        {"ipa": "TwitchUnblock-2.0.ipa"},
                    ]
                }
            }
        )
        for index, entry in enumerate(saved["automation"]["accounts"]):
            entry["email"] = DEFAULT_ACCOUNTS[index]["email"]
        flow = get_workflow(saved)
        self.assertEqual([a["name"] for a in flow], ["account 1", "account 2", "account 3"])
        self.assertEqual(flow[0]["ipa"], "YouTube 22.1.0 (NewMod 2.0)")
        self.assertEqual(flow[1]["identifier"], "eeveespotify")
        self.assertEqual(flow[2]["ipa"], "TwitchUnblock-2.0")
        self.assertEqual(DEFAULT_ACCOUNTS[0]["ipa"], "SampleVideo 1.0")

    def test_extension_is_optional_but_paths_and_query_syntax_are_rejected(self):
        self.assertEqual(ipa_stem(" App v2.IPA "), "App v2")
        self.assertEqual(ipa_stem("App v2"), "App v2")
        for bad in (
            "",
            ".ipa",
            "../app.ipa",
            "C:\\app.ipa",
            'app" OR anything',
            "NUL.ipa",
            "App?.ipa",
        ):
            with self.subTest(name=bad), self.assertRaises(ValueError):
                ipa_stem(bad)

    def test_atomic_save_preserves_pairing_and_unrelated_settings(self):
        original = configured(
            {"device": {"serial": "ABC", "paired_at": "2026-10-18T10:00:00"}, "other": {"keep": 42}}
        )
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config, "CONFIG_PATH", Path(directory) / "config.json"):
                config.save_config(copy.deepcopy(original))
                actual = config.load_config()
                self.assertEqual(actual, original)
                self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_gui_save_preserves_other_settings_and_updates_all_three_names(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            exe = folder / "iloader.exe"
            exe.write_bytes(b"test")
            before = configured({"device": {"serial": "ABC"}, "automation": {"keep": "unchanged"}})
            after = update_settings(
                before, folder, ["AppOne 2.ipa", "AppTwo 3", "AppThree 4.IPA"], 20, False
            )
            self.assertEqual(before["automation"]["accounts"], DEFAULT_ACCOUNTS)
            self.assertEqual(after["device"]["serial"], "ABC")
            self.assertEqual(after["automation"]["keep"], "unchanged")
            self.assertEqual(after["automation"]["install_timeout"], 1200)
            self.assertFalse(after["automation"]["close_gui_after_success"])
            self.assertEqual(
                [a["ipa"] for a in get_workflow(after)], ["AppOne 2", "AppTwo 3", "AppThree 4"]
            )

    def test_partial_account_configuration_is_not_silently_used(self):
        with self.assertRaises(ValueError):
            get_workflow(configured({"automation": {"accounts": [{"ipa": "only-one"}]}}))


class WatcherStopTests(unittest.TestCase):
    def test_stopped_watcher_does_not_trigger_an_already_connected_phone(self):
        event = threading.Event()
        event.set()
        dispatcher = Mock()
        watcher = IPhoneWatcher(dispatcher, target_serial="abc", emit_startup=True)
        with patch.object(watcher, "is_connected") as scan:
            watcher.run_forever(stop_event=event)
            scan.assert_not_called()
            dispatcher.emit.assert_not_called()

    def test_usb_wait_can_stop_without_waiting_for_next_poll(self):
        event = threading.Event()
        watcher = IPhoneWatcher(Mock(), target_serial="abc", poll_interval=60)

        def scan():
            event.set()
            return None

        with patch.object(watcher, "is_connected", side_effect=scan):
            watcher.run_forever(stop_event=event)


if __name__ == "__main__":
    unittest.main()
