from fixtures import configured
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

from iphone_detector import auto_refresh as auto


class AutoRefreshTests(unittest.TestCase):
    def test_disabling_renewal_removes_its_hidden_task(self):
        self.isolated_schedule()
        folder = Mock()
        folder.GetTasks.side_effect = lambda flags: (
            [Mock(Name=auto.TASK_NAME)] if flags == 1 else []
        )
        scheduler = Mock()
        scheduler.GetFolder.return_value = folder
        with patch("win32com.client.Dispatch", return_value=scheduler):
            auto.configure(False)
        folder.DeleteTask.assert_called_once_with(auto.TASK_NAME, 0)
        self.assertFalse(auto.read(auto.CONFIG)["enabled"])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.config = configured(
            {
                "device": {"serial": "sample-phone"},
                "automation": {"ipa_directory": str(self.folder)},
            }
        )
        for account in self.config["automation"]["accounts"]:
            (self.folder / (account["ipa"] + ".ipa")).write_bytes(b"fixture")
        state = patch("iphone_detector.config.load_config", return_value=self.config)
        state.start()
        self.addCleanup(state.stop)
        enqueue = patch("iphone_detector.discord_outbox.enqueue", return_value=False)
        enqueue.start()
        self.addCleanup(enqueue.stop)
        route = patch("iphone_detector.connection_router.route_ready", return_value=True)
        route.start()
        self.addCleanup(route.stop)

    def seed(self, job):
        from iphone_detector.background import make_job
        from iphone_detector.storage import write_json

        write_json(job / "selection.json", make_job(self.config, "Network"))

    def isolated_schedule(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        for name, path in (
            ("DATA_ROOT", self.folder),
            ("CONFIG", self.folder / "auto.json"),
            ("SUCCESS", self.folder / "success.json"),
            ("STATE", self.folder / "state.json"),
        ):
            stack.enter_context(patch.object(auto, name, path))
        return stack

    def completed_job(self, parent, name, at):
        import os

        job = self.folder / parent / name
        job.mkdir(parents=True)
        self.seed(job)
        (job / "result.json").write_text(json.dumps({"ok": True, "completed_accounts": 3}))
        os.utime(job / "result.json", (at, at))
        return job

    def test_enable_seeds_latest_matching_gui_or_automatic_completion(self):
        self.isolated_schedule()
        self.completed_job("automation-runs", "older", 100)
        gui = self.completed_job("gui-jobs", "latest", 200)
        self.completed_job("automation-runs", "future", auto.time.time() + 3600)
        changed = self.completed_job("automation-runs", "wrong-phone", 300)
        selection = auto.read(changed / "selection.json")
        selection["serial"] = "other-phone"
        (changed / "selection.json").write_text(json.dumps(selection))
        receipt = auto.seed_success()
        self.assertEqual(receipt["job"], str(gui))
        self.assertEqual(receipt["at"], 200)
        self.assertEqual(receipt["selection"], auto.read(gui / "selection.json"))

    def test_legacy_timer_recovers_identity_and_changed_assignments_become_due(self):
        self.isolated_schedule()
        job = self.completed_job("gui-jobs", "success", 200)
        now = auto.time.time()
        auto.CONFIG.write_text(json.dumps({"enabled": True, "anchor": now}))
        auto.SUCCESS.write_text(json.dumps({"at": now, "job": str(job)}))
        config, success, _ = auto.schedule_snapshot()
        self.assertIn("selection", success)
        self.assertEqual(auto.decision(config, success, {}, now, True, False), "scheduled")
        self.config["device"]["serial"] = "replacement-phone"
        config, success, _ = auto.schedule_snapshot()
        self.assertEqual(success, {})
        self.assertEqual(auto.decision(config, success, {}, now + 1, True, False), "due")

    def test_unverifiable_legacy_timer_does_not_delay_renewal(self):
        self.isolated_schedule()
        now = auto.time.time()
        auto.CONFIG.write_text(json.dumps({"enabled": True, "anchor": now}))
        for job in (None, {}, "missing-job"):
            auto.SUCCESS.write_text(json.dumps({"at": now, "job": job}))
            config, success, _ = auto.schedule_snapshot()
            self.assertEqual(success, {})
            self.assertLessEqual(auto.due_time(config, success), auto.time.time())

    def test_invalid_selection_pauses_with_repair_error(self):
        self.isolated_schedule()
        auto.SUCCESS.write_text(json.dumps({"at": 100, "selection": []}))
        with self.assertRaisesRegex(ValueError, "repair"):
            auto.schedule_snapshot()

    def test_scheduled_busy_and_unresolved_runs_do_not_probe_network(self):
        stack = self.isolated_schedule()
        stack.enter_context(patch("iphone_detector.run_lock.RunLock"))
        probe = stack.enter_context(patch("iphone_detector.connection_router.route_ready"))
        spawn = stack.enter_context(patch.object(auto.subprocess, "Popen"))
        stack.enter_context(patch.object(auto, "attention"))
        for config, state, available in (
            ({"enabled": True, "anchor": auto.time.time()}, {}, True),
            ({"enabled": True, "anchor": 0}, {}, False),
            ({"enabled": True, "anchor": 0}, {"pending_job": "missing", "started_at": 1}, True),
        ):
            auto.CONFIG.write_text(json.dumps(config))
            auto.STATE.write_text(json.dumps(state))
            with patch("iphone_detector.run_lock.RunLock") as lock:
                lock.return_value.acquire.side_effect = [True, available]
                auto.tick()
        probe.assert_not_called()
        spawn.assert_not_called()

    def test_connectivity_failure_defers_without_creating_a_pending_job(self):
        stack = self.isolated_schedule()
        lock = stack.enter_context(patch("iphone_detector.run_lock.RunLock"))
        lock.return_value.acquire.return_value = True
        stack.enter_context(
            patch("iphone_detector.connection_router.route_ready", side_effect=OSError("offline"))
        )
        ensure = stack.enter_context(patch("iphone_detector.remote_tunnel.ensure_started"))
        spawn = stack.enter_context(patch.object(auto.subprocess, "Popen"))
        auto.CONFIG.write_text(json.dumps({"enabled": True, "anchor": 0}))
        auto.tick()
        self.assertEqual(auto.read(self.folder / "auto-refresh-check.json")["status"], "waiting")
        self.assertFalse(auto.STATE.exists())
        ensure.assert_called_once()
        spawn.assert_not_called()

    def test_month_overdue_starts_one_catchup_not_a_run_per_missed_interval(self):
        config = {"enabled": True, "anchor": 100}
        now = 100 + 35 * 86400
        self.assertEqual(auto.decision(config, {}, {}, now, True, False), "due")
        self.assertEqual(auto.decision(config, {"at": now}, {}, now + 60, True, False), "scheduled")

    def test_crash_after_success_recovers_receipt_without_reinstalling(self):
        import os

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "job"
            job.mkdir()
            self.seed(job)
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
            job = Path(root) / "job"
            self.seed(job)
            for result in (
                {"ok": False, "completed_accounts": 3},
                {"ok": True, "completed_accounts": 2},
                {"ok": True, "completed_accounts": 3, "prepared_only": True},
            ):
                auto.record_success(job, result)
                self.assertFalse(auto.SUCCESS.exists())
            auto.record_success(job, {"ok": True, "completed_accounts": 3})
            self.assertEqual(auto.read(auto.SUCCESS)["job"], str(job))

    def run_tick(self, submitted, ok, marker=None):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)

            def launch(args, **kwargs):
                job = Path(args[args.index("--job-dir") + 1])
                self.seed(job)
                if submitted:
                    (job / "remote-request.json").touch()
                if marker:
                    (job / marker).touch()
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

    def test_dispatched_native_work_without_events_pauses_instead_of_replaying(self):
        for marker in ("native-dispatch.json", "native-input.json"):
            with self.subTest(marker=marker):
                state, launches, notices, _ = self.run_tick(False, False, marker)
                self.assertTrue(state["notified"])
                self.assertEqual(launches, 1)
                self.assertEqual(notices, 1)
