"""Corrupt receipts, failed process tracking and unavailable cancellation storage."""

import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from iphone_detector import discord_outbox, job_queue, operations, worker_registry
from iphone_detector.storage import write_json
import test_workflow_upgrades as workflow_fixtures


class QueueRecoveryTests(unittest.TestCase):
    setUp = workflow_fixtures.WorkflowUpgradeTests.setUp
    job = workflow_fixtures.WorkflowUpgradeTests.job

    def test_boolean_slot_in_completion_snapshot_pauses_queue_without_replay(self):
        entry = job_queue.enqueue(accounts=[0])
        job = self.root / "automation-runs" / ("queue-" + entry["id"])
        job.mkdir(parents=True)
        altered = json.loads(json.dumps(entry["selection"]))
        altered["accounts"][0]["account"] = True
        write_json(job / "selection.json", altered)
        write_json(job / "result.json", {"ok": True, "completed_accounts": 1})
        write_json(job_queue.ROOT / (entry["id"] + ".json"), dict(entry, state="running"))
        with patch.object(job_queue.subprocess, "Popen") as spawn:
            self.assertFalse(job_queue.step())
        spawn.assert_not_called()
        self.assertEqual(job_queue.entries()[0]["failure_code"], "completion-unverified")

    def test_completion_for_another_selection_pauses_instead_of_claiming_success(self):
        entry = job_queue.enqueue(accounts=[0])
        job = self.root / "automation-runs" / ("queue-" + entry["id"])
        job.mkdir(parents=True)
        altered = dict(entry["selection"], serial="other-phone")
        write_json(job / "selection.json", altered)
        write_json(job / "result.json", {"ok": True, "completed_accounts": 1})
        write_json(job_queue.ROOT / (entry["id"] + ".json"), dict(entry, state="running"))
        with patch.object(job_queue.subprocess, "Popen") as spawn:
            self.assertFalse(job_queue.step())
        spawn.assert_not_called()
        self.assertEqual(job_queue.entries()[0]["failure_code"], "completion-unverified")

    def test_failed_spawn_saves_reason_and_one_durable_attention_report(self):
        write_json(self.root / "discord.json", {"user_id": 1, "guild_id": 2, "channel_id": 3})
        job_queue.enqueue(accounts=[0])
        with patch.object(
            job_queue.subprocess, "Popen", side_effect=OSError("private-error-detail")
        ):
            self.assertFalse(job_queue.step())
        self.assertEqual(job_queue.entries()[0]["failure_code"], "worker-launch-failed")
        for _ in range(3):
            self.assertFalse(job_queue.step())
        reports = list((self.root / "discord-outbox").glob("*.json"))
        self.assertEqual(len(reports), 1)
        message = json.loads(reports[0].read_text())["message"]
        self.assertIn("No device work", message)
        self.assertNotIn("private-error-detail", message)

    def test_process_tracking_failure_never_claims_worker_was_not_started(self):
        job_queue.enqueue(accounts=[0])
        with (
            patch.object(job_queue.subprocess, "Popen", return_value=Mock(pid=123)) as spawn,
            patch.object(
                job_queue.psutil, "Process", side_effect=job_queue.psutil.NoSuchProcess(123)
            ),
        ):
            self.assertFalse(job_queue.step())
        spawn.assert_called_once()
        self.assertEqual(job_queue.entries()[0]["failure_code"], "worker-identity-unavailable")

    def test_invalid_pid_and_nonfinite_creation_time_cannot_pin_queue(self):
        for pid, created in (
            (True, 1),
            (0, 1),
            (-1, 1),
            (123, float("nan")),
            (123, float("inf")),
            (123, "bad"),
        ):
            with (
                self.subTest(pid=pid, created=created),
                patch.object(job_queue.psutil, "Process") as probe,
            ):
                self.assertFalse(job_queue.alive({"pid": pid, "process_created": created}))
                probe.assert_not_called()

    def test_malformed_optional_expiration_does_not_erase_completion(self):
        job, request = self.job()
        for changes in (
            None,
            [],
            [None],
            ["bad"],
            [{"after": "bad"}],
            [{"after": "2030-01-01"}],
            [{"after": "2030-01-01T00:00:00+00:00"}, {}],
        ):
            with self.subTest(changes=changes):
                write_json(
                    job / "profile-refresh-1.json",
                    {"profile_readback_verified": True, "expiration_changes": changes},
                )
                receipt = operations.record(job, request, 1, "refresh")
                self.assertIsNone(receipt["profile_expires_at"])
                self.assertTrue(receipt["verified"])
                self.assertTrue((job / "completed-1.json").is_file())


class WorkerRecoveryTests(unittest.TestCase):
    def test_invalid_worker_identity_is_not_treated_as_an_active_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = root / "automation-runs/job"
            job.mkdir(parents=True)
            base = {
                "state": "running",
                "pid": 123,
                "process_created": 1,
                "started_at": time.time(),
                "mode": "discord-run",
                "accounts": [1],
            }
            for delta in (
                {"pid": True},
                {"process_created": float("nan")},
                {"started_at": float("inf")},
                {"started_at": time.time() + 3600},
            ):
                write_json(job / "worker.json", dict(base, **delta))
                with patch.object(worker_registry.psutil, "Process") as probe:
                    self.assertEqual(worker_registry.active_jobs(root), [])
                    probe.assert_not_called()

    def test_one_unwritable_stop_marker_does_not_block_other_cancellations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            good = root / "good"
            good.mkdir()
            with patch.object(
                worker_registry,
                "active_jobs",
                return_value=[{"job": root / "missing"}, {"job": good}],
            ):
                self.assertEqual(worker_registry.stop_active(root), 1)
            self.assertTrue((good / "stop").exists())

    def test_invalid_outbox_times_are_preserved_but_never_delivered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audience = {"user_id": 1, "guild_id": 2, "channel_id": 3}
            for index, created in enumerate((float("nan"), float("inf"), -1, time.time() + 3600)):
                write_json(
                    root / "discord-outbox" / f"{index}.json",
                    {"audience": audience, "message": "fixture", "created": created},
                )
            self.assertEqual(discord_outbox.pending(root, audience), [])
            self.assertEqual(len(list((root / "discord-outbox").glob("*.json"))), 4)
