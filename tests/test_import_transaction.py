"""A closed import dialog must never delete a committed account's IPA."""

import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from fixtures import write_sample_app
from iphone_detector import config, ipa_inbox
from iphone_detector.run_lock import RunLock


class ImportTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source_folder = self.root / "received"
        self.source_folder.mkdir()
        self.managed = self.root / "managed"
        self.managed.mkdir()
        self.source = self.source_folder / "New app.ipa"
        with zipfile.ZipFile(self.source, "w") as archive:
            write_sample_app(archive)
        stat = self.source.stat()
        self.signature = (stat.st_size, stat.st_mtime_ns)
        self.initial = {
            "device": {"serial": "fixture-phone"},
            "automation": {
                "ipa_directory": str(self.managed),
                "accounts": [{"email": "fixture@example.com", "ipa": "Old app"}],
                "notify_on_finish": False,
            },
            "other": {"preserve": True},
        }
        self.settings_path = self.root / "config.json"
        self.override = patch.object(config, "CONFIG_PATH", self.settings_path)
        self.override.start()
        self.addCleanup(self.override.stop)
        config.save_config(copy.deepcopy(self.initial))

    def import_file(self, **kwargs):
        return ipa_inbox.import_assignment(
            self.source,
            self.managed,
            0,
            self.initial["automation"]["accounts"][0],
            self.signature,
            source_directory=self.source_folder,
            **kwargs,
        )

    def test_commit_preserves_other_settings_and_saves_source_folder_and_pending_ipa(self):
        target = self.import_file()
        saved = config.load_config()
        self.assertEqual(saved["other"], self.initial["other"])
        self.assertFalse(saved["automation"]["notify_on_finish"])
        self.assertEqual(saved["automation"]["accounts"][0]["ipa"], target.stem)
        self.assertEqual(saved["automation"]["pending_ipa_installs"], {"1": target.stem})
        self.assertEqual(saved["automation"]["import_directory"], str(self.source_folder))
        self.assertEqual(target.read_bytes(), self.source.read_bytes())

    def test_cancellation_before_commit_removes_only_new_copy(self):
        cancelled = threading.Event()
        original = ipa_inbox.import_ipa
        unrelated = self.managed / "keep.ipa"
        unrelated.write_bytes(b"keep")

        def copying(*args, **kwargs):
            target = original(*args, **kwargs)
            cancelled.set()
            return target

        with patch.object(ipa_inbox, "import_ipa", side_effect=copying):
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                self.import_file(cancelled=cancelled)
        self.assertEqual(list(self.managed.iterdir()), [unrelated])
        self.assertEqual(
            config.load_config()["automation"]["accounts"], self.initial["automation"]["accounts"]
        )
        self.assertTrue(self.source.is_file())

    def test_cancellation_after_commit_preserves_the_assigned_file(self):
        cancelled = threading.Event()
        save = config.save_config

        def commit(value):
            save(value)
            cancelled.set()  # Simulate closing the dialog immediately after atomic save.

        with patch.object(config, "save_config", side_effect=commit):
            target = self.import_file(cancelled=cancelled)
        self.assertTrue(cancelled.is_set())
        self.assertTrue(target.is_file())
        self.assertEqual(config.load_config()["automation"]["accounts"][0]["ipa"], target.stem)

    def test_changed_account_or_folder_is_rejected_before_copy(self):
        for changed in ("account", "folder"):
            saved = copy.deepcopy(self.initial)
            if changed == "account":
                saved["automation"]["accounts"][0]["email"] = "other@example.com"
            else:
                saved["automation"]["ipa_directory"] = str(self.root / "other")
            config.save_config(saved)
            with self.subTest(changed=changed), patch.object(ipa_inbox, "import_ipa") as copying:
                with self.assertRaises(ValueError):
                    self.import_file()
                copying.assert_not_called()

    def test_failed_save_removes_unique_copy_and_releases_the_shared_lock(self):
        with patch.object(config, "save_config", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.import_file()
        self.assertEqual(list(self.managed.iterdir()), [])
        guard = RunLock()
        try:
            self.assertTrue(guard.acquire())
        finally:
            guard.close()

    def test_copy_holds_the_lock_against_conflicting_installs_and_assignments(self):
        original = ipa_inbox.import_ipa

        def copying(*args, **kwargs):
            guard = RunLock()
            try:
                self.assertFalse(guard.acquire())
            finally:
                guard.close()
            return original(*args, **kwargs)

        with patch.object(ipa_inbox, "import_ipa", side_effect=copying):
            self.assertTrue(self.import_file().is_file())
