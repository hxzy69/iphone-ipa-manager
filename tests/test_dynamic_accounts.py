import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from iphone_detector.settings import get_workflow


class DynamicAccountTests(unittest.TestCase):
    def config(self, count=4):
        return {
            "device": {"serial": "phone"},
            "automation": {
                "accounts": [
                    {"email": "account%d@example.com" % i, "ipa": "App%d" % i} for i in range(count)
                ]
            },
        }

    def test_workflow_numbers_are_stable_up_to_25_and_excess_is_rejected(self):
        self.assertEqual(len(get_workflow(self.config(25))), 25)
        self.assertEqual(get_workflow(self.config(4))[3]["name"], "account 4")
        with self.assertRaises(ValueError):
            get_workflow(self.config(26))

    def test_renewal_only_resets_after_all_current_accounts_complete(self):
        from iphone_detector.auto_refresh import verified_success

        with patch("iphone_detector.config.load_config", return_value=self.config()):
            self.assertFalse(verified_success({"ok": True, "completed_accounts": 3}))
            self.assertFalse(verified_success({"ok": True, "completed_accounts": 1}))
            self.assertTrue(verified_success({"ok": True, "completed_accounts": 4}))

    def test_background_request_and_queue_support_account_10(self):
        from iphone_detector.background import make_job
        from iphone_detector.remote_tunnel import job_accounts

        with tempfile.TemporaryDirectory() as directory:
            config = self.config(10)
            config["automation"]["ipa_directory"] = directory
            (Path(directory) / "App9.ipa").write_bytes(b"file-metadata-only")
            result = make_job(config, "Remote", accounts=[9])
            self.assertEqual(result["accounts"][0]["account"], 10)
            request = Path(directory) / "remote-request.json"
            request.write_text(json.dumps({"accounts": [10]}))
            self.assertEqual(job_accounts(Path(directory)), [10])

    def test_extended_gui_fields_preserve_all_existing_assignments(self):
        from iphone_detector.gui_sync import fields, merge

        before = fields(self.config(3))
        remote = fields(self.config(4))
        local = dict(before, timeout="20")
        before["ipa3"] = local["ipa3"] = remote["ipa3"]
        values, baseline, conflicts = merge(before, local, remote)
        self.assertEqual(values["ipa3"], "App3.ipa")
        self.assertEqual(values["timeout"], "20")
        self.assertEqual(conflicts, [])
