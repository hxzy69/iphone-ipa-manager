from fixtures import configured
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from iphone_detector import auto_refresh as auto, discord_outbox as outbox, remote_tunnel as remote


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        state = patch("iphone_detector.config.load_config", return_value=configured())
        state.start()
        self.addCleanup(state.stop)

    def test_reports_survive_delivery_failure_restart_and_do_not_repeat_after_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = {"user_id": 1, "guild_id": 2, "channel_id": 3}
            (root / "discord.json").write_text(json.dumps(settings))
            self.assertTrue(outbox.enqueue(root, "job:one", "Verified completion"))
            failing = AsyncMock(side_effect=OSError("offline"))
            with self.assertRaises(OSError):
                asyncio.run(outbox.deliver(root, settings, failing))
            self.assertEqual(len(outbox.pending(root, settings)), 1)
            sent = AsyncMock()
            asyncio.run(outbox.deliver(root, settings, sent))
            sent.assert_awaited_once_with("Verified completion")
            self.assertTrue(outbox.enqueue(root, "job:one", "Duplicate producer"))
            asyncio.run(outbox.deliver(root, settings, sent))
            self.assertEqual(sent.await_count, 1)
            self.assertEqual(outbox.pending(root, settings), [])

    def test_oversized_timestamps_do_not_break_worker_or_notification_status(self):
        from iphone_detector import job_queue, worker_registry

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jobs = root / "gui-jobs" / "damaged"
            jobs.mkdir(parents=True)
            (jobs / "worker.json").write_text(
                json.dumps({"state": "running", "pid": 1, "process_created": 10**500})
            )
            self.assertEqual(worker_registry.active_jobs(root), [])
            (jobs / "worker.json").write_text(
                json.dumps({"state": "running", "pid": 10**500, "process_created": 1})
            )
            self.assertEqual(worker_registry.active_jobs(root), [])
            self.assertFalse(job_queue.alive({"pid": 1, "process_created": 10**500}))
            self.assertFalse(job_queue.alive({"pid": 10**500, "process_created": 1}))
            folder = root / "discord-outbox"
            folder.mkdir()
            audience = {"user_id": 1, "guild_id": 2, "channel_id": 3}
            (folder / "damaged.json").write_text(
                json.dumps({"audience": audience, "message": "status", "created": 10**500})
            )
            self.assertEqual(outbox.pending(root, audience), [])

    def test_reports_never_move_to_a_new_configured_owner_or_channel(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = {"user_id": 1, "guild_id": 2, "channel_id": 3}
            (root / "discord.json").write_text(json.dumps(settings))
            outbox.enqueue(root, "old", "Private result")
            for key in settings:
                changed = dict(settings, **{key: 99})
                self.assertEqual(outbox.pending(root, changed), [])
            (root / "discord-outbox/broken.json").write_text("[]")
            self.assertEqual(len(outbox.pending(root, settings)), 1)

    def test_same_event_for_new_audience_is_not_blocked_by_old_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = {"user_id": 1, "guild_id": 2, "channel_id": 3}
            new = dict(old, channel_id=4)
            (root / "discord.json").write_text(json.dumps(old))
            self.assertTrue(outbox.enqueue(root, "incident", "Old audience"))
            (root / "discord.json").write_text(json.dumps(new))
            self.assertTrue(outbox.enqueue(root, "incident", "New audience"))
            self.assertTrue(outbox.enqueue(root, "incident", "Duplicate"))
            self.assertEqual([v["message"] for _, v in outbox.pending(root, old)], ["Old audience"])
            self.assertEqual([v["message"] for _, v in outbox.pending(root, new)], ["New audience"])

    def test_corrupt_existing_outbox_record_is_not_reported_as_queued(self):
        import hashlib

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "discord.json").write_text(
                json.dumps({"user_id": 1, "guild_id": 2, "channel_id": 3})
            )
            folder = root / "discord-outbox"
            folder.mkdir()
            (folder / (hashlib.sha256(b"incident").hexdigest() + ".json")).write_bytes(b"broken")
            self.assertFalse(outbox.enqueue(root, "incident", "Message"))

    def test_corrupt_renewal_state_blocks_launch_and_preserves_original_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(auto, "DATA_ROOT", root),
                patch.object(auto, "CONFIG", root / "config.json"),
                patch.object(auto, "SUCCESS", root / "success.json"),
                patch.object(auto, "STATE", root / "state.json"),
                patch.object(auto.subprocess, "Popen") as launch,
            ):
                auto.CONFIG.write_text(json.dumps({"enabled": True, "anchor": 0}))
                for invalid in (
                    "{",
                    "[]",
                    '{"pending_job":"job"}',
                    '{"started_at":NaN}',
                    json.dumps({"started_at": 10**500}),
                ):
                    auto.STATE.write_text(invalid)
                    auto.tick()
                    self.assertIn("needs attention", auto.describe())
                    self.assertEqual(auto.STATE.read_text(), invalid)
                    self.assertEqual(
                        auto.read(root / "auto-refresh-check.json")["status"], "state-needs-repair"
                    )
                launch.assert_not_called()

    def test_invalid_tunnel_status_is_offline_instead_of_crashing_or_false_ready(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(remote, "STATE", Path(directory) / "state.json"),
            patch.object(remote.time, "time", return_value=100),
        ):
            for value in (
                [],
                None,
                {"state": "ready"},
                {"state": "ready", "updated": "now"},
                {"state": "ready", "updated": float("nan")},
                {"state": "ready", "updated": 10**500},
                {"state": "ready", "updated": 10000},
                {"state": "anything", "updated": 100},
            ):
                remote.STATE.write_text(json.dumps(value))
                self.assertEqual(remote.state(), {"state": "offline"})
            remote.STATE.write_text(json.dumps({"state": "ready", "updated": 99}))
            self.assertEqual(remote.state()["state"], "ready")

    def test_success_requires_exact_typed_completion_not_truthy_or_partial_receipt(self):
        for result in (
            {"ok": 1, "completed_accounts": 3},
            {"ok": True, "completed_accounts": 3.0},
            {"ok": True, "completed_accounts": 1},
            {"ok": True, "completed_accounts": 3, "prepared_only": True},
        ):
            self.assertFalse(auto.verified_success(result))
        self.assertTrue(auto.verified_success({"ok": True, "completed_accounts": 3}))

    def test_worker_launch_failure_defers_without_false_interrupted_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with (
                patch.object(auto, "DATA_ROOT", root),
                patch.object(auto, "CONFIG", root / "config.json"),
                patch.object(auto, "STATE", root / "state.json"),
                patch.object(auto, "SUCCESS", root / "success.json"),
                patch("iphone_detector.remote_tunnel.enabled", return_value=True),
                patch("iphone_detector.remote_tunnel.state", return_value={"state": "ready"}),
                patch.object(
                    auto.subprocess, "Popen", side_effect=OSError("cannot launch")
                ) as launch,
            ):
                auto.CONFIG.write_text(json.dumps({"enabled": True, "anchor": 0}))
                auto.tick()
                self.assertEqual(auto.read(auto.STATE)["reason"], "worker-launch-failed")
                self.assertNotIn("pending_job", auto.read(auto.STATE))
                self.assertEqual(launch.call_count, 1)
