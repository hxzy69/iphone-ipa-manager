"""Damaged saved identities must not postpone renewal or claim queue completion."""

import copy
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from iphone_detector import auto_refresh, background, operations
from iphone_detector.storage import write_json


class SavedReceiptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "Sample.ipa").write_bytes(b"sample-content")
        self.config = {
            "device": {"serial": "sample-phone"},
            "automation": {
                "ipa_directory": str(self.root),
                "accounts": [{"email": "sample@example.com", "ipa": "Sample"}],
            },
        }
        environment = patch.dict(os.environ, APPDATA=str(self.root))
        environment.start()
        self.addCleanup(environment.stop)
        self.request = background.make_job(self.config, "Network")

    def test_boolean_or_float_account_cannot_inherit_verified_selection(self):
        for value in (True, 1.0, "1", 0, 26, None):
            altered = copy.deepcopy(self.request)
            altered["accounts"][0]["account"] = value
            with self.subTest(value=value):
                self.assertFalse(operations.selection_matches(altered, self.request))
                self.assertFalse(operations.selection_matches(self.request, altered))

    def test_identically_corrupt_selections_never_verify_each_other(self):
        for key, values in {
            "size": (True, 1.0, -1, "1", None),
            "mtime_ns": (True, 1.0, -1, "1", None),
            "sha256": (True, [], "", "f" * 63, "g" * 64),
        }.items():
            for value in values:
                altered = copy.deepcopy(self.request)
                altered["accounts"][0]["file_identity"][key] = value
                with self.subTest(key=key, value=value):
                    self.assertFalse(operations.selection_matches(altered, altered))

    def test_duplicate_slots_and_missing_account_text_never_verify(self):
        altered = copy.deepcopy(self.request)
        altered["accounts"] *= 2
        self.assertFalse(operations.selection_matches(altered, altered))
        for key in ("email", "ipa"):
            for value in (None, True, "", " "):
                altered = copy.deepcopy(self.request)
                altered["accounts"][0][key] = value
                with self.subTest(key=key, value=value):
                    self.assertFalse(operations.selection_matches(altered, altered))

    def test_metadata_comparison_allows_missing_digest_but_requires_typed_stats(self):
        altered = copy.deepcopy(self.request)
        del altered["accounts"][0]["file_identity"]["sha256"]
        self.assertTrue(operations.selection_matches(altered, self.request, content=False))
        self.assertFalse(operations.selection_matches(altered, self.request))
        altered["accounts"][0]["file_identity"]["size"] = True
        self.assertFalse(operations.selection_matches(altered, altered, content=False))

    def test_damaged_saved_success_cannot_postpone_due_renewal(self):
        config_path = self.root / "auto-refresh.json"
        success_path = self.root / "renewal-last-success.json"
        state_path = self.root / "auto-refresh-state.json"
        altered = copy.deepcopy(self.request)
        altered["accounts"][0]["account"] = True
        now = time.time()
        write_json(config_path, {"enabled": True, "anchor": now})
        write_json(success_path, {"at": now, "selection": altered})
        write_json(state_path, {})
        with (
            patch.object(auto_refresh, "CONFIG", config_path),
            patch.object(auto_refresh, "SUCCESS", success_path),
            patch.object(auto_refresh, "STATE", state_path),
            patch("iphone_detector.config.load_config", return_value=self.config),
        ):
            config, success, _ = auto_refresh.schedule_snapshot()
        self.assertEqual(success, {})
        self.assertLessEqual(auto_refresh.due_time(config, success), time.time())
        self.assertIs(
            json.loads(success_path.read_text())["selection"]["accounts"][0]["account"], True
        )

    def preferences(self, contents):
        path = self.root / "me.nabdev.iloader/preferences.json"
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(contents)
        return path

    def test_explicit_server_works_without_reading_corrupt_legacy_preferences(self):
        path = self.preferences(b"{broken")
        self.config["automation"]["anisette_server"] = "https://example.com"
        self.assertEqual(
            background.make_job(self.config, "Network")["anisette_server"],
            "https://example.com",
        )
        self.assertEqual(path.read_bytes(), b"{broken")

    def test_invalid_legacy_preferences_fail_with_repair_instructions_and_preserve_bytes(self):
        for contents in (b"{broken", b"[]", b"null", b"\xff"):
            path = self.preferences(contents)
            with self.subTest(contents=contents):
                with self.assertRaisesRegex(ValueError, "Repair them locally"):
                    background.make_job(self.config, "Network")
                self.assertEqual(path.read_bytes(), contents)

    def test_legacy_server_can_be_read_from_bom_encoded_preferences(self):
        contents = json.dumps({"newAnisetteServer": "https://example.com"})
        self.preferences(contents.encode("utf-8-sig"))
        self.assertEqual(
            background.make_job(self.config, "Network")["anisette_server"],
            "https://example.com",
        )

    def test_inaccessible_legacy_preferences_report_repair_instead_of_using_defaults(self):
        with patch.object(Path, "read_text", side_effect=PermissionError("fixture")):
            with self.assertRaisesRegex(ValueError, "Repair them locally"):
                background.make_job(self.config, "Network")
