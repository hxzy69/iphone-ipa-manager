"""Retention protects unresolved work and keeps recovery receipts intact."""

import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from iphone_detector import maintenance, auto_refresh
from iphone_detector.storage import write_json


class MaintenanceTests(unittest.TestCase):
    def test_dead_native_temporary_files_are_cleaned_without_removing_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "automation-runs/failed"
            temporary = job / "native-temp-test"
            extracted = temporary / "Payload/App.app"
            extracted.mkdir(parents=True)
            (extracted / "binary").write_bytes(b"temporary")
            write_json(job / "result.json", {"ok": False})
            os.utime(temporary, (1, 1))
            with patch("iphone_detector.worker_registry.active_jobs", return_value=[]):
                self.assertEqual(maintenance._clean_locked(root), 1)
            self.assertFalse(temporary.exists())
            self.assertTrue((job / "result.json").exists())

    def test_cleanup_removes_only_old_completed_logs_and_terminal_records(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            done, failed = root / "automation-runs/done", root / "automation-runs/failed"
            write_json(done / "result.json", {"ok": True})
            write_json(done / "completed-1.json", {"verified": True})
            write_json(failed / "result.json", {"ok": False})
            for folder in (done, failed):
                (folder / "backend.log").write_text("diagnostics", encoding="utf-8")
                os.utime(folder / "backend.log", (1, 1))
            write_json(root / "queue/completed.json", {"state": "completed"})
            write_json(root / "queue/failed.json", {"state": "failed"})
            for path in (root / "queue").glob("*"):
                os.utime(path, (1, 1))
            with patch("iphone_detector.worker_registry.active_jobs", return_value=[]):
                self.assertEqual(maintenance.clean(root), 2)
            self.assertTrue((done / "completed-1.json").exists())
            self.assertTrue((failed / "backend.log").exists())
            self.assertTrue((root / "queue/failed.json").exists())

    def test_active_or_unresolved_job_is_not_cleaned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "automation-runs/active"
            write_json(job / "result.json", {"ok": True})
            log = job / "backend.log"
            log.write_text("keep", encoding="utf-8")
            os.utime(log, (1, 1))
            with patch("iphone_detector.worker_registry.active_jobs", return_value=[{"job": job}]):
                self.assertEqual(maintenance.cleanup_candidates(root), [])

    def test_invalid_config_repair_preserves_original_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = b'{"automation": broken'
            (root / "config.json").write_bytes(original)
            destination = maintenance.quarantine_invalid_config(root)
            self.assertEqual(destination.read_bytes(), original)
            self.assertFalse((root / "config.json").exists())

    def test_readable_config_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "config.json", {})
            with self.assertRaisesRegex(ValueError, "readable"):
                maintenance.quarantine_invalid_config(root)
            self.assertTrue((root / "config.json").exists())

    def test_observed_early_expiry_brings_renewal_forward(self):
        now = time.time()
        config = {"anchor": now, "enabled": True}
        success = {"at": now, "next_due": now + 3600}
        self.assertEqual(auto_refresh.decision(config, success, {}, now + 3601, True, False), "due")
        self.assertEqual(auto_refresh.due_time(config, success), now + 3600)

    def test_future_clock_receipt_pauses_instead_of_postponing_renewal_silently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(root / "success.json", {"at": time.time() + 365 * 86400})
            with (
                patch.object(auto_refresh, "CONFIG", root / "config.json"),
                patch.object(auto_refresh, "SUCCESS", root / "success.json"),
                patch.object(auto_refresh, "STATE", root / "state.json"),
            ):
                with self.assertRaisesRegex(ValueError, "timestamp"):
                    auto_refresh.schedule_snapshot()
