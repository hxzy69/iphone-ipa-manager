"""Crash/reboot recovery, snapshot identity and credential-free configuration backups."""

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fixtures import configured
from iphone_detector import operations, job_queue, config_backup
from iphone_detector.background import make_job
from iphone_detector.storage import write_json


class WorkflowUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = configured({"automation": {"ipa_directory": str(self.root)}})
        self.config["device"] = {"serial": "fixture-phone"}
        from iphone_detector.settings import get_workflow

        for account in get_workflow(self.config):
            (self.root / (account["ipa"] + ".ipa")).write_bytes(b"fixture")
        self.patches = [
            patch.object(job_queue, "ROOT", self.root / "queue"),
            patch.object(job_queue, "DATA_ROOT", self.root),
            patch.object(job_queue, "ensure_started"),
            patch("iphone_detector.connection_router.route_ready", return_value=True),
            patch("iphone_detector.startup_task.configure"),
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch("iphone_detector.config_backup.load_config", return_value=self.config),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def job(self):
        job = self.root / "automation-runs/gui-fixture"
        job.mkdir(parents=True)
        request = make_job(self.config, "Network")
        write_json(job / "selection.json", {key: request[key] for key in ("serial", "accounts")})
        write_json(job / "result.json", {"ok": False})
        return job, request

    def test_retry_skips_only_matching_verified_accounts(self):
        job, request = self.job()
        operations.record(job, request, 1)
        operations.record(job, request, 3)
        self.assertEqual(operations.retry_plan(job, self.config), [1])
        operations.record(job, request, 2)
        with self.assertRaisesRegex(ValueError, "nothing to retry"):
            operations.retry_plan(job, self.config)

    def test_changed_file_or_phone_prevents_partial_retry(self):
        job, request = self.job()
        operations.record(job, request, 1)
        Path(request["accounts"][0]["ipa"]).write_bytes(b"changed IPA")
        with self.assertRaisesRegex(ValueError, "changed"):
            operations.retry_plan(job, self.config)

    def test_retry_accepts_equivalent_remote_device_id_formatting(self):
        job, request = self.job()
        request["serial"] = "FIXTUREPHONE"
        write_json(job / "selection.json", {key: request[key] for key in ("serial", "accounts")})
        operations.record(job, request, 1)
        self.assertEqual(operations.retry_plan(job, self.config), [1, 2])

    def test_corrupt_account_snapshot_is_rejected_as_a_validation_error(self):
        job, _ = self.job()
        write_json(job / "selection.json", {"serial": "fixture-phone", "accounts": ["broken"]})
        with self.assertRaisesRegex(ValueError, "snapshot"):
            operations.retry_plan(job, self.config)

    def test_active_or_unfinished_run_cannot_be_retried(self):
        job, _ = self.job()
        with patch("iphone_detector.worker_registry.active_jobs", return_value=[{"job": job}]):
            with self.assertRaisesRegex(ValueError, "still active"):
                operations.retry_plan(job, self.config)
        (job / "result.json").unlink()
        with self.assertRaisesRegex(ValueError, "no final result"):
            operations.retry_plan(job, self.config)

    def test_install_does_not_invent_profile_expiration(self):
        job, request = self.job()
        receipt = operations.record(job, request, 1)
        self.assertIsNone(receipt["profile_expires_at"])
        write_json(
            job / "profile-refresh-1.json",
            {
                "profile_readback_verified": True,
                "expiration_changes": [{"after": "2030-01-01T00:00:00+00:00"}],
            },
        )
        self.assertIsInstance(
            operations.record(job, request, 1, "refresh")["profile_expires_at"], float
        )

    def test_native_completion_is_persisted_but_prepare_events_are_not(self):
        job, request = self.job()
        write_json(job / "backend-job.json", request)
        (job / "backend-events.jsonl").write_text(
            'broken\n{"stage":"completed_account","account":1}\n', encoding="utf-8"
        )
        operations.collect_native(job)
        self.assertTrue((job / "completed-1.json").exists())
        (job / "completed-1.json").unlink()
        write_json(job / "backend-job.json", dict(request, prepare_only=True))
        operations.collect_native(job)
        self.assertFalse((job / "completed-1.json").exists())

    def test_fifo_reboot_recovery_never_replays_a_completed_request(self):
        first = job_queue.enqueue(accounts=[1])
        second = job_queue.enqueue(accounts=[0])
        with (
            patch.object(job_queue.subprocess, "Popen", return_value=Mock(pid=123)) as spawn,
            patch.object(job_queue.psutil, "Process", return_value=Mock(create_time=lambda: 1.0)),
            patch.object(job_queue, "alive", return_value=False),
        ):
            self.assertTrue(job_queue.step())
            self.assertIn("queue-" + first["id"], str(spawn.call_args.args))
            write_json(
                self.root / "automation-runs" / ("queue-" + first["id"]) / "result.json",
                {"ok": True, "completed_accounts": 1},
            )
            self.assertTrue(job_queue.step())
            self.assertEqual(spawn.call_count, 2)
            self.assertIn("queue-" + second["id"], str(spawn.call_args.args))
            self.assertEqual(job_queue.entries()[0]["state"], "completed")

    def test_queue_rejects_an_account_replaced_after_dashboard_selection(self):
        from iphone_detector.settings import get_workflow

        email = get_workflow(self.config)[0]["email"]
        with self.assertRaisesRegex(ValueError, "account changed"):
            job_queue.enqueue(accounts=[0], expected_email="different@example.com")
        self.assertEqual(job_queue.entries(), [])
        entry = job_queue.enqueue(accounts=[0], expected_email=email)
        self.assertEqual(entry["selection"]["accounts"][0]["email"], email)

    def test_interruption_pauses_later_requests_until_explicit_dismissal(self):
        first = job_queue.enqueue(accounts=[0])
        job_queue.enqueue(accounts=[1])
        write_json(job_queue.ROOT / (first["id"] + ".json"), dict(first, state="running"))
        with (
            patch.object(job_queue, "alive", return_value=False),
            patch.object(job_queue.subprocess, "Popen") as spawn,
        ):
            self.assertFalse(job_queue.step())
            spawn.assert_not_called()
        self.assertEqual(job_queue.entries()[0]["state"], "interrupted")
        job_queue.change(first["id"], "dismiss")
        self.assertEqual(job_queue.entries()[0]["state"], "acknowledged")

    def test_changed_ipa_pauses_queue_before_spawning_worker(self):
        entry = job_queue.enqueue(accounts=[0])
        Path(entry["selection"]["accounts"][0]["ipa"]).write_bytes(b"another version")
        with patch.object(job_queue.subprocess, "Popen") as spawn:
            self.assertFalse(job_queue.step())
            spawn.assert_not_called()
        self.assertEqual(job_queue.entries()[0]["state"], "failed")

    def test_network_probe_failure_keeps_request_waiting_then_resumes(self):
        first = job_queue.enqueue(accounts=[0])
        with (
            patch("iphone_detector.connection_router.route_ready", side_effect=OSError("offline")),
            patch(
                "iphone_detector.remote_tunnel.ensure_started", side_effect=OSError("unavailable")
            ),
            patch.object(job_queue.subprocess, "Popen") as spawn,
        ):
            self.assertTrue(job_queue.step())
            self.assertTrue(job_queue.step())
            spawn.assert_not_called()
        entry = job_queue.entries()[0]
        self.assertEqual(entry["id"], first["id"])
        self.assertEqual(entry["state"], "queued")
        self.assertEqual(entry["waiting_reason"], "connection")
        self.assertFalse((self.root / "automation-runs").exists())
        with (
            patch.object(job_queue.subprocess, "Popen", return_value=Mock(pid=123)) as spawn,
            patch.object(job_queue.psutil, "Process", return_value=Mock(create_time=lambda: 1.0)),
        ):
            self.assertTrue(job_queue.step())
            spawn.assert_called_once()
        self.assertEqual(job_queue.entries()[0]["state"], "running")
        self.assertNotIn("waiting_reason", job_queue.entries()[0])

    def test_corrupt_queue_is_preserved_and_blocks_dispatch(self):
        job_queue.ROOT.mkdir()
        path = job_queue.ROOT / "broken.json"
        path.write_text("broken")
        with self.assertRaisesRegex(ValueError, "unreadable"):
            job_queue.step()
        self.assertEqual(path.read_text(), "broken")

    def test_cancel_does_not_spawn_waiting_request(self):
        entry = job_queue.enqueue(accounts=[0])
        job_queue.change(entry["id"], "cancel")
        with patch.object(job_queue.subprocess, "Popen") as spawn:
            self.assertFalse(job_queue.step())
            spawn.assert_not_called()

    def test_backup_contains_assignments_but_excludes_secrets_and_paths(self):
        self.config.update(token="secret-token", device={"serial": "private-device"})
        value = config_backup.export()
        text = json.dumps(value)
        for private in ("secret-token", "private-device", str(self.root)):
            self.assertNotIn(private, text)
        self.assertEqual(len(value["accounts"]), 3)
        for extra in ({"token": "secret"}, {"device": {}}, {"preferences": {"other": True}}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                config_backup.validate(dict(value, **extra))

    def test_restore_preserves_local_pairing_and_marks_new_assignments_pending(self):
        value = config_backup.export()
        with (
            patch("iphone_detector.paths.DATA_ROOT", self.root),
            patch.object(config_backup, "save_config") as save,
        ):
            self.assertEqual(config_backup.restore(value), 3)
        saved = save.call_args.args[0]
        self.assertEqual(saved["device"], self.config["device"])
        self.assertEqual(len(saved["automation"]["pending_ipa_installs"]), 3)
        self.assertEqual(len(list((self.root / "backups").glob("*.json"))), 1)

    def test_restore_is_blocked_by_pending_requests(self):
        job_queue.enqueue(accounts=[0])
        with self.assertRaisesRegex(ValueError, "queued"):
            config_backup.restore(config_backup.export())

    def test_partial_verified_install_clears_only_its_pending_assignment(self):
        from iphone_detector.ipa_assignments import acknowledge_installs

        job, request = self.job()
        write_json(job / "backend-job.json", request)
        self.config["automation"]["pending_ipa_installs"] = {
            str(e["account"]): Path(e["ipa"]).stem for e in request["accounts"]
        }
        operations.record(job, request, 1)
        with patch("iphone_detector.config.save_config") as save:
            acknowledge_installs(job, {"ok": False, "completed_accounts": 1})
        self.assertEqual(
            set(save.call_args.args[0]["automation"]["pending_ipa_installs"]), {"2", "3"}
        )

    def test_partial_recovery_resumes_schedule_only_with_all_new_verified_accounts(self):
        import time
        from iphone_detector import auto_refresh

        job, request = self.job()
        first = operations.record(job, request, 1)
        for number in (2, 3):
            operations.record(job, request, number)
        with (
            patch.object(auto_refresh, "DATA_ROOT", self.root),
            patch.object(auto_refresh, "SUCCESS", self.root / "success.json"),
            patch.object(
                auto_refresh,
                "schedule_snapshot",
                return_value=({}, {}, {"pending_job": str(job), "started_at": first["at"] - 1}),
            ),
        ):
            auto_refresh.record_success(job, {"ok": True, "completed_accounts": 2})
            receipt = json.loads((self.root / "success.json").read_text())
            self.assertEqual(receipt["at"], first["at"])
            self.assertTrue(receipt["combined_verified_accounts"])
            (self.root / "success.json").unlink()
            with patch.object(
                auto_refresh,
                "schedule_snapshot",
                return_value=({}, {}, {"pending_job": str(job), "started_at": time.time() + 1}),
            ):
                auto_refresh.record_success(job, {"ok": True, "completed_accounts": 2})
                self.assertFalse((self.root / "success.json").exists())

    def test_busy_installation_defers_queue_without_claiming_request(self):
        from iphone_detector.run_lock import RunLock

        entry = job_queue.enqueue(accounts=[0])
        lock = RunLock()
        self.assertTrue(lock.acquire())
        try:
            with patch.object(job_queue.subprocess, "Popen") as spawn:
                self.assertTrue(job_queue.step())
                spawn.assert_not_called()
            self.assertEqual(job_queue.entries()[0]["state"], "queued")
            self.assertEqual(job_queue.entries()[0]["id"], entry["id"])
        finally:
            lock.close()

    def test_activation_blocks_new_queue_requests(self):
        import time

        write_json(self.root / "update-state.json", {"phase": "starting", "at": time.time()})
        with self.assertRaisesRegex(ValueError, "update"):
            job_queue.enqueue(accounts=[0])
        self.assertEqual(job_queue.entries(), [])

    def test_missing_ipa_does_not_hide_other_verified_account_rows(self):
        job, request = self.job()
        operations.record(job, request, 1)
        Path(request["accounts"][1]["ipa"]).unlink()
        self.assertIn(1, operations.account_status(self.root, self.config))

    def test_connection_metrics_are_measured_and_stale_uploads_are_hidden(self):
        import time
        from iphone_detector.dashboard import connection

        job, _ = self.job()
        write_json(job / "route.json", {"transport": "remote"})
        write_json(
            job / "remote-transfer.json",
            {
                "stage": "upload",
                "upload_started_at": time.time() - 10,
                "updated_at": time.time(),
                "uploaded_bytes": 20 * 1024 * 1024,
            },
        )
        with patch("iphone_detector.remote_tunnel.state", return_value={"state": "ready"}):
            self.assertEqual(connection(self.root)["upload_mib_s"], 2.0)
            self.assertIn("Last route", connection(self.root)["route"])
            with patch("iphone_detector.dashboard.time.time", return_value=time.time() + 60):
                self.assertIsNone(connection(self.root)["upload_mib_s"])
