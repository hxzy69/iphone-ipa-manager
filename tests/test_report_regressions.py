import json
import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import ipa_manager
from iphone_detector import (
    connection_router,
    desktop_install,
    gui,
    job_queue,
    management_dialog,
    remote_dialog,
    service_entry,
    worker_registry,
)
from iphone_detector.wifi import BridgeCleanupError


class ReportRegressionTests(unittest.TestCase):
    def test_activity_poll_continues_after_account_sync_error(self):
        import queue

        dialog = Mock()
        dialog.window.winfo_exists.return_value = True
        dialog.action_events.get_nowait.return_value = ("Restored", None, None)
        dialog.app.sync_saved.side_effect = ValueError("accounts removed")
        dialog.events.get_nowait.side_effect = queue.Empty
        dialog.loading = True
        management_dialog.ManagementDialog.poll(dialog)
        dialog.app.root.after.assert_called_with(100, dialog.poll)
        dialog.app.status.set.assert_called_with(
            "Settings changed. Reopen the manager to reload its accounts."
        )

    def test_cleanup_failure_preserves_verified_worker_result(self):
        completed = {"ok": True, "completed_accounts": 1}
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("iphone_detector.logger.setup_logging"),
            patch(
                "iphone_detector.config.load_config",
                return_value={"automation": {"notify_on_finish": False}},
            ),
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch("iphone_detector.run_lock.RunLock.acquire", return_value=True),
            patch("iphone_detector.background.validate_selection"),
            patch(
                "iphone_detector.connection_router.run_automatic",
                side_effect=BridgeCleanupError("restore failed", completed),
            ),
            patch("iphone_detector.ipa_assignments.acknowledge_installs") as acknowledge,
        ):
            job = Path(folder)
            self.assertEqual(ipa_manager.worker("discord-run", job, accounts=[0]), 1)
            receipt = json.loads((job / "result.json").read_text())
            self.assertTrue(receipt["ok"])
            self.assertEqual(receipt["completed_accounts"], 1)
            self.assertIn("cleanup", receipt["cleanup_warning"])
            acknowledge.assert_called_once()

    def test_bridge_cleanup_error_retains_result(self):
        bridge = Mock()
        bridge.__exit__ = Mock(side_effect=OSError("restore failed"))
        completed = {"ok": True, "completed_accounts": 1}
        with self.assertRaises(BridgeCleanupError) as error:
            connection_router.cleanup_bridge(bridge, completed)
        self.assertEqual(error.exception.completed_result, completed)

    def test_bridge_cleanup_retains_the_original_failure(self):
        bridge = Mock()
        bridge.__exit__ = Mock(side_effect=OSError("restore failed"))
        original = RuntimeError("installation failed")
        connection_router.cleanup_bridge(bridge, original_error=original)
        self.assertIn("restore failed", original.__notes__[0])

    def test_poll_retries_after_unexpected_exception(self):
        app = Mock()
        app.root.winfo_exists.return_value = True
        app._poll_once.side_effect = OSError("temporary failure")
        gui.App.poll(app)
        app.root.after.assert_called_once_with(150, app.poll)

    def test_explicit_empty_accounts_stays_empty(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch("iphone_detector.settings.get_workflow", return_value=[1, 2]),
        ):
            job = Path(folder)
            worker_registry.register(job, "run", [])
            self.assertEqual(json.loads((job / "worker.json").read_text())["accounts"], [])

    def test_queue_connectivity_probe_does_not_hold_store_lock(self):
        held = []
        entry = {
            "id": "one",
            "state": "queued",
            "mode": "refresh",
            "selection": {
                "serial": "phone",
                "accounts": [
                    {
                        "account": 1,
                        "email": "fixture@example.com",
                        "ipa": "Fixture.ipa",
                        "file_identity": {"size": 1, "mtime_ns": 1, "sha256": "0" * 64},
                    }
                ],
            },
        }

        @contextmanager
        def guard():
            held.append(True)
            try:
                yield
            finally:
                held.pop()

        def probe(**kwargs):
            self.assertFalse(held)
            return False

        lock = Mock()
        lock.acquire.return_value = True
        with (
            patch.object(job_queue, "guard", guard),
            patch.object(job_queue, "entries", return_value=[entry]),
            patch.object(job_queue, "RunLock", return_value=lock),
            patch.object(job_queue, "write_json"),
            patch("iphone_detector.background.make_job", return_value=entry["selection"]),
            patch("iphone_detector.config.load_config", return_value={}),
            patch("iphone_detector.connection_router.route_ready", side_effect=probe),
            patch("iphone_detector.remote_tunnel.ensure_started"),
        ):
            self.assertTrue(job_queue.step())
            self.assertEqual(entry["waiting_reason"], "connection")

    def test_duplicate_queue_runner_does_not_overwrite_service_status(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(service_entry, "DATA_ROOT", Path(folder)),
            patch("iphone_detector.logger.setup_logging"),
        ):
            status = Path(folder) / "queue-service.json"
            status.write_text('{"state":"running"}')
            self.assertEqual(service_entry.run("queue", lambda: "already-running"), 0)
            self.assertEqual(json.loads(status.read_text())["state"], "running")

    def test_queue_does_not_launch_request_cancelled_during_probe(self):
        entry = {
            "id": "one",
            "state": "queued",
            "mode": "install",
            "selection": {"accounts": [], "serial": "phone"},
        }
        cancelled = dict(entry, state="cancelled")
        with (
            patch.object(job_queue, "entries", side_effect=[[entry], [cancelled]]),
            patch("iphone_detector.connection_router.route_ready", return_value=True),
            patch.object(job_queue.subprocess, "Popen") as spawn,
        ):
            self.assertFalse(job_queue.step())
            spawn.assert_not_called()

    def test_queue_launch_reservation_prevents_duplicate_spawn(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(job_queue, "DATA_ROOT", Path(folder)),
            patch.object(job_queue, "pending", return_value=[{"state": "queued"}]),
            patch("iphone_detector.discord_bot.command", return_value=["dummy"]),
            patch.object(
                job_queue.subprocess, "Popen", return_value=Mock(pid=os.getpid())
            ) as spawn,
        ):
            job_queue.ensure_started()
            job_queue.ensure_started()
            spawn.assert_called_once()

    def test_completed_install_with_cleanup_warning_pauses_queue_without_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            selection = {
                "serial": "phone",
                "accounts": [
                    {
                        "account": 1,
                        "ipa": "a.ipa",
                        "email": "a@example.invalid",
                        "file_identity": {},
                    }
                ],
            }
            queue_root = root / "queue"
            queue_root.mkdir()
            (queue_root / "one.json").write_text(
                json.dumps(
                    {"id": "one", "state": "running", "mode": "install", "selection": selection}
                )
            )
            job = root / "automation-runs" / "queue-one"
            job.mkdir(parents=True)
            (job / "result.json").write_text(
                json.dumps({"ok": True, "completed_accounts": 1, "cleanup_warning": "fixture"})
            )
            (job / "selection.json").write_text(json.dumps(selection))
            with (
                patch.object(job_queue, "DATA_ROOT", root),
                patch.object(job_queue, "ROOT", queue_root),
                patch.object(job_queue, "alive", return_value=False),
                patch.object(job_queue, "report_failure") as report,
                patch("iphone_detector.operations.selection_matches", return_value=True),
            ):
                self.assertFalse(job_queue.step())
                self.assertEqual(job_queue.entries()[0]["failure_code"], "cleanup-unverified")
                report.assert_called_once()

    def test_remote_setup_cannot_close_while_busy(self):
        dialog = Mock()
        dialog.busy = True
        remote_dialog.RemoteDialog.close(dialog)
        dialog.window.destroy.assert_not_called()
        dialog.status.set.assert_called_once()
        dialog.busy = False
        remote_dialog.RemoteDialog.close(dialog)
        dialog.window.destroy.assert_called_once()

    def test_startup_checkbox_restores_saved_value_on_failure(self):
        dialog = Mock()
        dialog.startup.get.return_value = True
        management_dialog.ManagementDialog.toggle_startup(dialog)
        completion = dialog.perform.call_args.args[1]
        with patch("iphone_detector.job_history.read", return_value={"startup": False}):
            completion("failed")
        dialog.startup.set.assert_called_once_with(False)
        dialog.startup_control.configure.assert_called_with(state="normal")

    def test_uninstaller_schedules_removal_of_only_its_temporary_copy(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder) / "installed"
            root.mkdir()
            executable = root / "manager.exe"
            executable.write_bytes(b"fixture")
            helper_dir = Path(folder) / "helper"
            helper_dir.mkdir()
            with (
                patch.object(desktop_install, "checked_root", return_value=root),
                patch.object(desktop_install.sys, "executable", str(executable)),
                patch.object(desktop_install.tempfile, "mkdtemp", return_value=str(helper_dir)),
                patch.object(
                    desktop_install.subprocess, "Popen", side_effect=[Mock(pid=123), Mock()]
                ) as spawn,
            ):
                desktop_install.launch_uninstaller(root)
            self.assertEqual(spawn.call_count, 2)
            cleanup_env = spawn.call_args_list[1].kwargs["env"]
            self.assertEqual(
                cleanup_env["IPA_UNINSTALL_HELPER_FILE"], str(helper_dir / "iPhoneIPAManager.exe")
            )
            self.assertEqual(cleanup_env["IPA_UNINSTALL_HELPER_DIR"], str(helper_dir))


if __name__ == "__main__":
    unittest.main()
