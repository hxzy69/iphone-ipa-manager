import json
import os
from pathlib import Path
import tempfile
import unittest

from iphone_detector.job_history import recent_jobs


class HistoryTests(unittest.TestCase):
    def test_only_matching_completion_count_is_reported_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "automation-runs/discord-test"
            job.mkdir(parents=True)
            (job / "backend-job.json").write_text(
                json.dumps({"accounts": [{"account": 2}], "profile_refresh": True})
            )
            result = job / "result.json"
            result.write_text(
                json.dumps({"ok": True, "completed_accounts": 3, "error": "SECRET-MUST-NOT-LEAK"})
            )
            history = recent_jobs(root)
            self.assertEqual(history[0]["status"], "Needs attention")
            self.assertNotIn("SECRET", str(history))
            result.write_text(
                json.dumps({"ok": True, "completed_accounts": 1, "transport": "remote"})
            )
            entry = recent_jobs(root)[0]
            self.assertEqual(entry["status"], "Completed")
            self.assertEqual(entry["accounts"], [2])
            self.assertEqual(entry["operation"], "Refresh")
            self.assertEqual(entry["transport"], "remote tunnel")

    def test_recent_first_bounded_and_ignores_damaged_json_and_unrelated_folders(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(8):
                job = root / "automation-runs" / ("discord-%d" % index)
                job.mkdir(parents=True)
                (job / "result.json").write_text("{broken")
                (job / "backend-job.json").write_text('{"accounts": null}')
                os.utime(job, (100 + index, 100 + index))
            (root / "automation-runs/benchmark").mkdir()
            history = recent_jobs(root)
            self.assertEqual(len(history), 5)
            self.assertEqual(history[0]["updated"], 107)
            self.assertTrue(all(entry["status"] == "Unfinished or running" for entry in history))
