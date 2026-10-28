"""Interrupted imports and service handovers must remain safe to retry."""

import asyncio
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

from fixtures import write_sample_app
from iphone_detector import discord_bot, guided_update, ipa_inbox, release_integrity, remote_tunnel
from iphone_detector.cancellation import Cancelled
from iphone_detector.run_lock import RunLock


class AuditLifecycleTests(unittest.TestCase):
    def test_packaged_recovery_timeout_stops_and_joins_bootloader_children(self):
        from tools import smoke_recovery

        process = Mock(pid=123)
        process.wait.side_effect = [smoke_recovery.subprocess.TimeoutExpired("fixture", 60), -9]
        child = Mock()
        with (
            patch.object(smoke_recovery.subprocess, "Popen", return_value=process),
            patch.object(
                smoke_recovery.psutil,
                "Process",
                return_value=Mock(children=lambda recursive: [child]),
            ),
            patch.object(smoke_recovery.psutil, "wait_procs", return_value=([child], [])) as join,
        ):
            with self.assertRaises(smoke_recovery.subprocess.TimeoutExpired):
                smoke_recovery.run(Path("fixture.exe"), Path("fixture-profile"), "--queue-runner")
        child.kill.assert_called_once()
        process.kill.assert_called_once()
        join.assert_called_once_with([child], timeout=10)
        self.assertEqual(process.wait.call_args.kwargs, {"timeout": 10})

    def test_worker_can_be_stopped_during_selection_validation_before_device_dispatch(self):
        import json
        import ipa_manager

        def validating(job, config, accounts, *, stop=None):
            self.assertIsNotNone(stop)
            self.assertTrue(stop.cancelled.wait(2), "Stop monitor was not active during validation")
            stop.check()

        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            (job / "stop").touch()
            with (
                patch("iphone_detector.logger.setup_logging"),
                patch("iphone_detector.config.load_config", return_value={}),
                patch.object(remote_tunnel, "enabled", return_value=False),
                patch("iphone_detector.run_lock.RunLock") as lock,
                patch("iphone_detector.worker_registry.register"),
                patch("iphone_detector.worker_registry.finish"),
                patch("iphone_detector.background.validate_selection", side_effect=validating),
                patch("iphone_detector.connection_router.run_automatic") as dispatch,
            ):
                lock.return_value.acquire.return_value = True
                self.assertEqual(ipa_manager.worker("discord-run", job), 130)
            dispatch.assert_not_called()
            lock.return_value.close.assert_called_once()
            self.assertTrue(json.loads((job / "result.json").read_text())["cancelled"])

    def test_overlapping_renewal_changes_do_not_interleave_task_and_preference_writes(self):
        from iphone_detector import auto_refresh

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "auto-refresh.json"
            config.write_text('{"enabled":true,"anchor":1}', encoding="utf-8")
            folder = Mock()
            folder.GetTasks.return_value = [Mock(Name=auto_refresh.TASK_NAME)]
            scheduler = Mock()
            scheduler.GetFolder.return_value = folder

            def overlapping_save(*args):
                with self.assertRaisesRegex(ValueError, "being changed"):
                    auto_refresh.configure(True)

            folder.DeleteTask.side_effect = overlapping_save
            with (
                patch.object(auto_refresh, "CONFIG", config),
                patch("win32com.client.Dispatch", return_value=scheduler) as dispatch,
            ):
                auto_refresh.configure(False)
                self.assertFalse(auto_refresh.read(config)["enabled"])
                auto_refresh.configure(False)  # The completed save released its mutex.
            self.assertEqual(dispatch.call_count, 2)
            self.assertEqual(folder.DeleteTask.call_count, 2)

    def test_failed_renewal_save_releases_settings_mutex_for_retry(self):
        import pywintypes
        from iphone_detector import auto_refresh

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "auto-refresh.json"
            folder = Mock()
            folder.GetTasks.return_value = []
            scheduler = Mock()
            scheduler.GetFolder.return_value = folder
            with (
                patch.object(auto_refresh, "CONFIG", config),
                patch(
                    "win32com.client.Dispatch",
                    side_effect=[
                        pywintypes.com_error(-2147467259, "fixture", None, None),
                        scheduler,
                    ],
                ),
            ):
                with self.assertRaises(OSError):
                    auto_refresh.configure(False)
                auto_refresh.configure(False)
            self.assertFalse(auto_refresh.read(config)["enabled"])

    def test_renewal_dialog_reenables_controls_after_scheduler_failure(self):
        import tkinter as tk
        from iphone_detector.renewal_dialog import RenewalDialog

        root = tk.Tk()
        root.withdraw()
        try:
            dialog = RenewalDialog(root, preview=True)
            with patch(
                "iphone_detector.auto_refresh.configure",
                side_effect=OSError("scheduler unavailable"),
            ):
                dialog.work(True)
                result = dialog.events.get(timeout=3)
            dialog.events.put(result)
            dialog.receive()
            self.assertEqual(str(dialog.save_button.cget("state")), "normal")
            self.assertEqual(str(dialog.toggle.cget("state")), "normal")
            self.assertIn("scheduler unavailable", dialog.status.get())
        finally:
            root.destroy()

    def test_scheduler_failure_is_actionable_and_preserves_renewal_preferences(self):
        import pywintypes
        from iphone_detector import auto_refresh

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "auto-refresh.json"
            config.write_text('{"enabled":true,"anchor":1}', encoding="utf-8")
            before = config.read_bytes()
            with (
                patch.object(auto_refresh, "CONFIG", config),
                patch(
                    "win32com.client.Dispatch",
                    side_effect=pywintypes.com_error(-2147467259, "fixture", None, None),
                ),
            ):
                with self.assertRaisesRegex(OSError, "Windows could not update"):
                    auto_refresh.configure(False)
            self.assertEqual(config.read_bytes(), before)

    def test_cancel_during_archive_validation_stops_reads_and_removes_partial_import(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.ipa"
            target = root / "managed"
            with zipfile.ZipFile(source, "w") as archive:
                write_sample_app(archive)
                archive.writestr("Payload/Test.app/large.dat", b"x" * (3 * 1024 * 1024))
            cancelled = threading.Event()
            original = zipfile.ZipExtFile.read
            chunks = []

            def read(stream, size=-1):
                data = original(stream, size)
                if stream.name.endswith("large.dat"):
                    chunks.append(len(data))
                    cancelled.set()
                return data

            with patch.object(zipfile.ZipExtFile, "read", read):
                with self.assertRaisesRegex(Cancelled, "cancelled"):
                    ipa_inbox.import_ipa(source, target, cancelled=cancelled)
            self.assertEqual(chunks, [1024 * 1024])
            self.assertEqual(list(target.iterdir()), [])
            self.assertTrue(source.is_file())

    def test_archive_aliases_are_rejected_before_extraction_or_signing(self):
        for name in (
            "Payload/./Test.app/data",
            "Payload//Test.app/data",
            "./readme.txt",
            "Payload//",
        ):
            with self.subTest(name=name):
                contents = io.BytesIO()
                with zipfile.ZipFile(contents, "w") as archive:
                    write_sample_app(archive)
                    archive.writestr(name, b"alias")
                with zipfile.ZipFile(io.BytesIO(contents.getvalue())) as archive:
                    with self.assertRaisesRegex(ValueError, "unsafe"):
                        release_integrity.verify_members(archive)
                with self.assertRaisesRegex(ValueError, "unsafe"):
                    ipa_inbox.validate_ipa(io.BytesIO(contents.getvalue()))

    def test_remote_startup_failure_releases_singleton_for_retry(self):
        for failure in (ValueError("invalid settings"), PermissionError("receipt locked")):
            with tempfile.TemporaryDirectory() as directory, self.subTest(failure=failure):
                with (
                    patch.object(remote_tunnel, "DATA_ROOT", Path(directory)),
                    patch.object(remote_tunnel, "settings", side_effect=failure),
                    patch.object(remote_tunnel, "atomic"),
                ):
                    with self.assertRaises(type(failure)):
                        asyncio.run(remote_tunnel.daemon())
                    retry = RunLock()
                    retry.name = remote_tunnel.profile_lock_name("RemoteTunnel")
                    try:
                        self.assertTrue(retry.acquire())
                    finally:
                        retry.close()

    def test_recovery_closes_every_discovered_connection_when_settings_write_fails(self):
        listeners = [
            Mock(remote_identifier="phone", port=50000, close=AsyncMock()) for _ in range(2)
        ]
        current = {"enabled": True, "serial": "phone", "ip": "100.64.0.10", "port": 40000}

        def write(path, value):
            if path == remote_tunnel.SETTINGS:
                raise PermissionError("settings locked")

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(remote_tunnel, "DATA_ROOT", Path(directory)),
            patch.object(remote_tunnel, "settings", return_value=current),
            patch.object(remote_tunnel, "enabled", side_effect=[True, False]),
            patch.object(remote_tunnel, "connected", new_callable=AsyncMock, side_effect=OSError()),
            patch.object(remote_tunnel, "atomic", side_effect=write),
            patch(
                "iphone_detector.remote_discovery.discover",
                new_callable=AsyncMock,
                return_value=listeners,
            ),
        ):
            asyncio.run(remote_tunnel.daemon())
        for listener in listeners:
            listener.close.assert_awaited_once()

    def test_discord_invalid_settings_do_not_block_a_repaired_listener(self):
        class FixtureLock(RunLock):
            def acquire(lock):
                lock.name += "AuditFixture-" + str(id(self))
                return super().acquire()

        with (
            patch("iphone_detector.run_lock.RunLock", FixtureLock),
            patch("iphone_detector.logger.setup_logging"),
            patch.object(remote_tunnel, "ensure_started"),
            patch.object(discord_bot, "load_settings", side_effect=ValueError("invalid settings")),
        ):
            for _ in range(2):
                with self.assertRaisesRegex(ValueError, "invalid settings"):
                    discord_bot.run()

    def test_update_owns_remote_singleton_before_starting_controllers(self):
        executable = Path("C:/fixture/new.exe")
        remote_acknowledged = False
        started = []

        def spawn(arguments, **kwargs):
            if "--discord" in arguments:
                self.assertTrue(remote_acknowledged, "Discord can race the unclaimed tunnel")
            process = Mock(pid=len(started) + 100, poll=lambda: None)
            started.append(process)
            return process

        def receipt(path):
            nonlocal remote_acknowledged
            nonce, component = path.stem.rsplit("-", 1)
            remote_acknowledged |= component == "remote"
            return {
                "nonce": nonce,
                "component": component,
                "pid": 100 if component == "remote" else 101,
                "created": 1.0,
            }

        def actual(pid):
            return Mock(pid=pid, exe=lambda: str(executable), create_time=lambda: 1.0)

        with (
            patch.object(guided_update.subprocess, "Popen", side_effect=spawn),
            patch.object(guided_update, "read", side_effect=receipt),
            patch.object(guided_update.psutil, "Process", side_effect=actual),
        ):
            result = guided_update.launch(
                executable, {"discord": True, "remote": True, "queue": False}, gui=False
            )
        self.assertEqual(result, started)
        self.assertEqual(len(started), 2)
