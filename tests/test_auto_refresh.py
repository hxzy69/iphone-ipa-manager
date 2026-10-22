from fixtures import configured
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from iphone_detector import auto_refresh as auto


class AutoRefreshTests(unittest.TestCase):
    def setUp(self):
        state = patch("iphone_detector.config.load_config", return_value=configured())
        state.start()
        self.addCleanup(state.stop)
        enqueue = patch("iphone_detector.discord_outbox.enqueue", return_value=False)
        enqueue.start()
        self.addCleanup(enqueue.stop)

    def test_crash_after_success_recovers_receipt_without_reinstalling(self):
        import os

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job"
            job.mkdir()
            receipt = job / "result.json"
            receipt.write_text(json.dumps({"ok": True, "completed_accounts": 3}))
            os.utime(receipt, (100, 100))
            with (
                patch.object(auto, "SUCCESS", root / "success.json"),
                patch.object(auto, "STATE", root / "state.json"),
            ):
                self.assertTrue(auto.recover_completed({"pending_job": str(job), "started_at": 90}))
                self.assertEqual(auto.read(auto.SUCCESS)["at"], 100)
                self.assertTrue(auto.read(auto.STATE)["recovered"])
                self.assertNotIn("pending_job", auto.read(auto.STATE))

    def test_failed_partial_or_old_receipts_never_resume_automatic_retries(self):
        import os

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = root / "result.json"
            with (
                patch.object(auto, "SUCCESS", root / "success.json"),
                patch.object(auto, "STATE", root / "state.json"),
            ):
                for result in (
                    {"ok": False, "completed_accounts": 3},
                    {"ok": True, "completed_accounts": 1},
                    {"ok": True, "completed_accounts": 3, "prepared_only": True},
                ):
                    receipt.write_text(json.dumps(result))
                    self.assertFalse(
                        auto.recover_completed({"pending_job": str(root), "started_at": 0})
                    )
                receipt.write_text(json.dumps({"ok": True, "completed_accounts": 3}))
                os.utime(receipt, (100, 100))
                self.assertFalse(
                    auto.recover_completed({"pending_job": str(root), "started_at": 101})
                )
                self.assertFalse(auto.SUCCESS.exists())

    def test_non_object_state_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text("[]")
            self.assertEqual(auto.read(path), {})

    def test_six_days_after_success_not_calendar_boundary(self):
        config = {"enabled": True, "anchor": 0}
        success = {"at": 100}
        self.assertEqual(
            auto.decision(config, success, {}, 100 + auto.INTERVAL - 1, True, False), "scheduled"
        )
        self.assertEqual(
            auto.decision(config, success, {}, 100 + auto.INTERVAL, True, False), "due"
        )

    def test_disabled_never_needs_a_seed(self):
        self.assertEqual(auto.decision({}, {}, {}, 100, True, False), "disabled")

    def test_unavailable_phone_defers_and_busy_install_is_not_overlapped(self):
        config = {"enabled": True, "anchor": 0}
        self.assertEqual(auto.decision(config, {}, {}, auto.INTERVAL, False, False), "waiting")
        self.assertEqual(auto.decision(config, {}, {}, auto.INTERVAL, True, True), "busy")

    def test_unresolved_run_blocks_until_a_later_success(self):
        config = {"enabled": True, "anchor": 0}
        state = {"pending_job": "interrupted", "started_at": auto.INTERVAL}
        self.assertEqual(
            auto.decision(config, {}, state, auto.INTERVAL + 1, True, False), "attention"
        )
        self.assertEqual(
            auto.decision(config, {"at": auto.INTERVAL + 2}, state, auto.INTERVAL + 3, True, False),
            "scheduled",
        )

    def test_only_three_account_success_resets_schedule(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.object(auto, "SUCCESS", Path(root) / "success.json"),
        ):
            for result in (
                {"ok": False, "completed_accounts": 3},
                {"ok": True, "completed_accounts": 2},
                {"ok": True, "completed_accounts": 3, "prepared_only": True},
            ):
                auto.record_success("job", result)
                self.assertFalse(auto.SUCCESS.exists())
            auto.record_success("job", {"ok": True, "completed_accounts": 3})
            self.assertEqual(auto.read(auto.SUCCESS)["job"], "job")

    def run_tick(self, submitted, ok):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)

            def launch(args, **kwargs):
                job = Path(args[args.index("--job-dir") + 1])
                if submitted:
                    (job / "remote-request.json").touch()
                (job / "result.json").write_text(
                    json.dumps({"ok": ok, "completed_accounts": 3 if ok else 1})
                )
                return Mock(wait=Mock(return_value=0 if ok else 1))

            with (
                patch.object(auto, "DATA_ROOT", root),
                patch.object(auto, "CONFIG", root / "config.json"),
                patch.object(auto, "STATE", root / "state.json"),
                patch.object(auto, "SUCCESS", root / "success.json"),
                patch("iphone_detector.run_lock.RunLock") as lock,
                patch("iphone_detector.remote_tunnel.enabled", return_value=True),
                patch("iphone_detector.remote_tunnel.state", return_value={"state": "ready"}),
                patch("iphone_detector.discord_bot.command", side_effect=lambda args: args),
                patch.object(auto.subprocess, "Popen", side_effect=launch) as popen,
                patch("iphone_detector.notify.notify") as notify,
            ):
                lock.return_value.acquire.return_value = True
                auto.CONFIG.write_text(json.dumps({"enabled": True, "anchor": 0}))
                auto.tick()
                first = auto.read(auto.STATE)
                auto.tick()
                return first, popen.call_count, notify.call_count, auto.read(auto.SUCCESS)

    def test_successful_automatic_run_does_not_repeat(self):
        state, launches, notices, success = self.run_tick(True, True)
        self.assertIn("completed_job", state)
        self.assertEqual(launches, 1)
        self.assertEqual(notices, 0)
        self.assertTrue(success["at"])

    def test_failed_submitted_job_pauses_and_notifies_once(self):
        state, launches, notices, _ = self.run_tick(True, False)
        self.assertTrue(state["notified"])
        self.assertEqual(launches, 1)
        self.assertEqual(notices, 1)

    def test_preflight_failure_can_retry_without_replaying_phone_work(self):
        state, launches, notices, _ = self.run_tick(False, False)
        self.assertIn("deferred_job", state)
        self.assertEqual(launches, 2)
        self.assertEqual(notices, 0)
