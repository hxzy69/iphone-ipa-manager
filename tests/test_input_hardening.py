"""Regression cases from the commercial-readiness review, using isolated inputs."""

import asyncio
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

from fixtures import write_sample_app
from iphone_detector import background, operations, job_queue, guided_update, connection_router
from iphone_detector.input_integrity import identity, https_server, snapshot_inputs
from iphone_detector.ipa_inbox import validate_ipa
from iphone_detector.storage import write_json


class InputHardeningTests(unittest.TestCase):
    def test_hash_and_archive_validation_propagate_cancellation(self):
        from iphone_detector.cancellation import Cancelled

        stop = Mock()
        stop.check.side_effect = Cancelled("stopped")
        with self.assertRaises(Cancelled):
            identity(self.ipa, stop=stop)
        with self.assertRaises(Cancelled):
            validate_ipa(self.ipa, stop=stop)

    def test_cancel_during_input_copy_removes_snapshot(self):
        from iphone_detector.cancellation import Cancelled

        request = {
            "accounts": [{"account": 1, "ipa": str(self.ipa), "file_identity": identity(self.ipa)}]
        }
        stop = Mock()
        stop.check.side_effect = [None, None, Cancelled("stopped")]
        with self.assertRaises(Cancelled), snapshot_inputs(self.job, request, stop):
            self.fail("cancelled preparation must not yield")
        self.assertEqual(list(self.job.glob("inputs-*")), [])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ipa = self.root / "Sample.ipa"
        with zipfile.ZipFile(self.ipa, "w") as archive:
            write_sample_app(archive)
        self.config = {
            "device": {"serial": "sample-phone"},
            "automation": {
                "ipa_directory": str(self.root),
                "accounts": [{"email": "sample@example.com", "ipa": "Sample"}],
            },
        }
        self.job = self.root / "automation-runs" / "example"
        self.job.mkdir(parents=True)
        usb = patch.object(connection_router, "find_usb", return_value=None)
        usb.start()
        self.addCleanup(usb.stop)

    def test_http_and_embedded_credentials_are_rejected(self):
        for value in (
            "http://example.com",
            "https://user:secret@example.com",
            "https://",
            "https://example.com\n",
        ):
            if value.endswith("\n"):
                continue  # surrounding whitespace is intentionally normalized
            with self.subTest(value=value), self.assertRaises(ValueError):
                https_server(value)
        self.assertEqual(https_server("example.com"), "https://example.com")

    def test_log_rotation_does_not_permanently_hide_progress(self):
        import codecs
        from iphone_detector.gui import App

        app = App.__new__(App)
        app.job = self.job
        app.log_offset = 10000
        app.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        app.append = Mock()
        (self.job / "activity.log").write_bytes(b"new log after rotation\n")
        app._read_log()
        app.append.assert_called_once_with("new log after rotation\n")

    def test_slow_live_state_read_does_not_block_gui_thread(self):
        import queue
        import threading
        import time
        from iphone_detector.gui import App

        app = App.__new__(App)
        app.live_events = queue.Queue()
        app.live_loading = False
        app.last_sync = 0
        release = threading.Event()

        def slow_read():
            release.wait(3)
            return {}

        with (
            patch("iphone_detector.gui.load_config", side_effect=slow_read),
            patch("iphone_detector.worker_registry.active_jobs", return_value=[]),
            patch("iphone_detector.remote_tunnel.describe", return_value="offline"),
            patch("iphone_detector.auto_refresh.describe", return_value="disabled"),
        ):
            try:
                started = time.monotonic()
                app.sync_live()
                self.assertLess(time.monotonic() - started, 0.2)
                self.assertTrue(app.live_loading)
            finally:
                release.set()
            value = app.live_events.get(timeout=3)
            self.assertIsNone(value[-1])

    def test_same_size_timestamp_replacement_changes_content_identity(self):
        before = identity(self.ipa)
        content = bytearray(self.ipa.read_bytes())
        content[0] ^= 1
        self.ipa.write_bytes(content)
        os.utime(self.ipa, ns=(self.ipa.stat().st_atime_ns, before["mtime_ns"]))
        self.assertNotEqual(identity(self.ipa)["sha256"], before["sha256"])

    def test_worker_snapshot_preserves_receipt_path_and_cleans_private_copy(self):
        request = background.make_job(self.config, "USB")
        with snapshot_inputs(self.job, request) as prepared:
            copied = Path(prepared["accounts"][0]["ipa"])
            self.assertNotEqual(copied, self.ipa)
            self.assertEqual(copied.read_bytes(), self.ipa.read_bytes())
        self.assertEqual(request["accounts"][0]["ipa"], str(self.ipa.resolve()))
        self.assertFalse(copied.exists())
        self.assertTrue(self.ipa.exists())

    def test_changed_source_is_rejected_before_backend_launch(self):
        request = background.make_job(self.config, "USB")
        self.ipa.write_bytes(b"replacement")
        with self.assertRaisesRegex(ValueError, "changed"), snapshot_inputs(self.job, request):
            self.fail("Changed input must not be yielded")

    def test_malformed_metadata_and_missing_executable_are_rejected(self):
        import plistlib

        for value in (
            b"not plist",
            plistlib.dumps(
                {
                    "CFBundleIdentifier": "com.example.test",
                    "CFBundleExecutable": "Missing",
                    "CFBundleVersion": "1",
                }
            ),
        ):
            with zipfile.ZipFile(self.ipa, "w") as archive:
                archive.writestr("Payload/Test.app/Info.plist", value)
            with self.assertRaises(ValueError):
                validate_ipa(self.ipa)

    def test_symlink_and_expansion_budget_are_rejected(self):
        with zipfile.ZipFile(self.ipa, "a") as archive:
            link = zipfile.ZipInfo("Payload/Test.app/link")
            link.create_system = 3
            link.external_attr = 0o120777 << 16
            archive.writestr(link, "../../outside")
        with self.assertRaisesRegex(ValueError, "links"):
            validate_ipa(self.ipa)
        with (
            patch("iphone_detector.ipa_inbox.MAX_EXPANDED", 1),
            self.assertRaisesRegex(ValueError, "size"),
        ):
            validate_ipa(self.ipa)

    def test_renewal_success_requires_current_phone_accounts_and_content(self):
        request = background.make_job(self.config, "Network")
        result = {"ok": True, "completed_accounts": 1}
        write_json(self.job / "selection.json", request)
        self.assertTrue(operations.verified_job(self.job, result, self.config))
        for changed in (
            dict(request, serial="different-phone"),
            dict(request, accounts=[dict(request["accounts"][0], email="other@example.com")]),
        ):
            write_json(self.job / "selection.json", changed)
            self.assertFalse(operations.verified_job(self.job, result, self.config))
        write_json(self.job / "selection.json", request)
        self.ipa.write_bytes(b"different")
        self.assertFalse(operations.verified_job(self.job, result, self.config))

    def test_old_verified_account_is_found_beyond_recent_history(self):
        request = background.make_job(self.config, "Network")
        operations.record(self.job, request, 1)
        operations.index_path(self.root, request["serial"], request["accounts"][0]).unlink()
        for index in range(105):
            (self.job.parent / f"unrelated-{index}").mkdir()
        self.assertIn(1, operations.account_status(self.root, self.config))

    def test_malformed_saved_device_identity_is_rejected_without_crashing(self):
        request = background.make_job(self.config, "Network")
        for serial in (True, 123, [], {"value": "sample-phone"}, None, ""):
            with self.subTest(serial=serial):
                bad = dict(request, serial=serial)
                self.assertFalse(operations.selection_matches(bad, request))
                self.assertFalse(operations.selection_matches(request, bad))
                receipt = {"serial": serial, "verified": True, "at": 100}
                self.assertFalse(operations.matching(receipt, request, request["accounts"][0]))
                write_json(self.job / "selection.json", bad)
                self.assertFalse(
                    operations.verified_job(
                        self.job, {"ok": True, "completed_accounts": 1}, self.config
                    )
                )

    def test_offline_install_waits_without_claim_or_spawn(self):
        with (
            patch.object(job_queue, "ROOT", self.root / "queue"),
            patch.object(job_queue, "DATA_ROOT", self.root),
            patch.object(job_queue, "ensure_started"),
            patch("iphone_detector.startup_task.configure"),
            patch("iphone_detector.config.load_config", return_value=self.config),
        ):
            entry = job_queue.enqueue()
            with (
                patch("iphone_detector.connection_router.route_ready", return_value=False),
                patch("iphone_detector.remote_tunnel.ensure_started"),
                patch.object(job_queue.subprocess, "Popen") as spawn,
            ):
                self.assertTrue(job_queue.step())
                spawn.assert_not_called()
            self.assertEqual(job_queue.entries()[0]["state"], "queued")
            self.assertFalse((self.root / "automation-runs" / ("queue-" + entry["id"])).exists())

    def test_queue_runner_retries_only_typed_store_contention(self):
        with (
            patch.object(job_queue, "DATA_ROOT", self.root),
            patch.object(
                job_queue, "step", side_effect=[job_queue.QueueBusy("busy"), False]
            ) as step,
            patch.object(job_queue.time, "sleep"),
        ):
            job_queue.run()
            self.assertEqual(step.call_count, 2)

    def test_update_rejects_child_that_exits_without_startup_ack(self):
        child = Mock(pid=123, poll=Mock(return_value=1))
        with (
            patch.object(guided_update.subprocess, "Popen", return_value=child),
            patch.object(guided_update.psutil, "Process"),
        ):
            with self.assertRaisesRegex(ValueError, "healthy startup"):
                guided_update.launch(
                    self.root / "new.exe", {"discord": False, "remote": False, "queue": False}
                )

    def test_local_refresh_maintains_heartbeat_and_uses_same_verification_engine(self):
        client = Mock(udid="sample-phone", close=AsyncMock())

        async def execute(job, session):
            await asyncio.sleep(0)
            self.assertTrue((job / "remote-heartbeat").exists())
            write_json(job / "remote-result.json", {"ok": True, "completed_accounts": 1})

        stop = Mock()
        stop.cancelled.is_set.return_value = False
        with (
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch("iphone_detector.remote_tunnel.settings", return_value={}),
            patch.object(connection_router, "find_local", return_value="192.168.1.7"),
            patch("iphone_detector.remote_check.read_pair", return_value={}),
            patch(
                "pymobiledevice3.lockdown.create_using_tcp",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("iphone_detector.remote_tunnel.execute_job", side_effect=execute) as perform,
            patch("iphone_detector.remote_tunnel.lookup", new_callable=AsyncMock, return_value={}),
        ):
            result = connection_router.run_refresh(self.job, stop)
        self.assertEqual(result["transport"], "local-wifi")
        perform.assert_called_once()
        client.close.assert_awaited_once()

    def test_refresh_prefers_matching_usb_without_network_discovery(self):
        client = Mock(udid="sample-phone", close=AsyncMock())

        async def execute(job, session):
            write_json(job / "remote-result.json", {"ok": True, "completed_accounts": 1})

        stop = Mock()
        stop.cancelled.is_set.return_value = False
        with (
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch.object(connection_router, "find_usb", return_value="sample-phone"),
            patch.object(connection_router, "find_local") as network,
            patch("iphone_detector.remote_check.read_pair", return_value={}),
            patch(
                "pymobiledevice3.lockdown.create_using_usbmux",
                new_callable=AsyncMock,
                return_value=client,
            ) as connect,
            patch("iphone_detector.remote_tunnel.lookup", new_callable=AsyncMock, return_value={}),
            patch("iphone_detector.remote_tunnel.execute_job", side_effect=execute),
            patch("iphone_detector.remote_tunnel.submit") as remote,
        ):
            result = connection_router.run_refresh(self.job, stop)
        self.assertEqual(result["transport"], "usb")
        self.assertEqual(connect.call_args.kwargs["connection_type"], "USB")
        network.assert_not_called()
        remote.assert_not_called()
        client.close.assert_awaited_once()

    def test_local_refresh_falls_back_only_before_device_work(self):
        from pymobiledevice3.exceptions import ConnectionTerminatedError

        client = Mock(udid="sample-phone", close=AsyncMock())
        with (
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch("iphone_detector.bridge_service.installed", return_value=False),
            patch("iphone_detector.remote_tunnel.settings", return_value={}),
            patch.object(connection_router, "find_local", return_value="192.168.1.7"),
            patch("iphone_detector.remote_check.read_pair", return_value={}),
            patch(
                "pymobiledevice3.lockdown.create_using_tcp",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch(
                "iphone_detector.remote_tunnel.lookup",
                new_callable=AsyncMock,
                side_effect=ConnectionTerminatedError(),
            ),
            patch("iphone_detector.remote_tunnel.execute_job", new_callable=AsyncMock) as execute,
            patch("iphone_detector.remote_tunnel.submit", return_value={"ok": True}) as submit,
        ):
            self.assertTrue(connection_router.run_refresh(self.job, Mock())["ok"])
        execute.assert_not_called()
        submit.assert_called_once()
        client.close.assert_awaited_once()
        self.assertFalse((self.job / "remote-request.json").exists())

    def test_refresh_uses_authenticated_wifi_bridge_after_direct_preflight_failure(self):
        from pymobiledevice3.exceptions import ConnectionTerminatedError

        direct = Mock(udid="sample-phone", close=AsyncMock())
        bridged = Mock(udid="sample-phone", close=AsyncMock())
        bridge = Mock(device={"serial": "sample-phone"}, cleanup_verified=True)
        bridge.__enter__ = Mock(return_value=bridge)
        bridge.__exit__ = Mock()

        async def execute(job, session):
            self.assertIs(session, bridged)
            write_json(job / "remote-result.json", {"ok": True, "completed_accounts": 1})

        stop = Mock()
        stop.cancelled.is_set.return_value = False
        with (
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch("iphone_detector.remote_tunnel.settings", return_value={}),
            patch("iphone_detector.remote_tunnel.enabled", return_value=False),
            patch.object(connection_router, "find_local", return_value="192.168.1.7"),
            patch("iphone_detector.remote_check.read_pair", return_value={}),
            patch("iphone_detector.bridge_service.installed", return_value=True),
            patch("iphone_detector.wifi.WifiBridge", return_value=bridge),
            patch(
                "pymobiledevice3.lockdown.create_using_tcp",
                new_callable=AsyncMock,
                return_value=direct,
            ),
            patch(
                "pymobiledevice3.lockdown.create_using_usbmux",
                new_callable=AsyncMock,
                return_value=bridged,
            ) as connect,
            patch(
                "iphone_detector.remote_tunnel.lookup",
                new_callable=AsyncMock,
                side_effect=[ConnectionTerminatedError(), {}],
            ),
            patch("iphone_detector.remote_tunnel.execute_job", side_effect=execute),
            patch("iphone_detector.remote_tunnel.submit") as remote,
        ):
            result = connection_router.run_refresh(self.job, stop)
        self.assertEqual(result["transport"], "local-wifi")
        self.assertEqual(connect.call_args.kwargs["connection_type"], "Network")
        self.assertEqual(connect.call_args.kwargs["usbmux_address"], "127.0.0.1:27015")
        self.assertEqual(
            json.loads((self.job / "route.json").read_text())["protocol"], "wifi-bridge"
        )
        bridge.__exit__.assert_called_once()
        remote.assert_not_called()

    def test_wifi_bridge_cleanup_failure_prevents_remote_retry(self):
        from pymobiledevice3.exceptions import ConnectionTerminatedError

        client = Mock(udid="sample-phone", close=AsyncMock())
        bridge = Mock(cleanup_verified=False)
        bridge.__enter__ = Mock(side_effect=RuntimeError("bridge failed"))
        with (
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch("iphone_detector.remote_tunnel.settings", return_value={}),
            patch("iphone_detector.remote_tunnel.enabled", return_value=False),
            patch.object(connection_router, "find_local", return_value="192.168.1.7"),
            patch("iphone_detector.remote_check.read_pair", return_value={}),
            patch("iphone_detector.bridge_service.installed", return_value=True),
            patch("iphone_detector.wifi.WifiBridge", return_value=bridge),
            patch(
                "pymobiledevice3.lockdown.create_using_tcp",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch(
                "iphone_detector.remote_tunnel.lookup",
                new_callable=AsyncMock,
                side_effect=ConnectionTerminatedError(),
            ),
            patch("iphone_detector.remote_tunnel.submit") as remote,
        ):
            with self.assertRaisesRegex(RuntimeError, "cleanup"):
                connection_router.run_refresh(self.job, Mock())
        remote.assert_not_called()

    def test_wifi_bridge_failure_after_dispatch_never_replays_on_remote(self):
        from pymobiledevice3.exceptions import ConnectionTerminatedError

        direct = Mock(udid="sample-phone", close=AsyncMock())
        bridged = Mock(udid="sample-phone", close=AsyncMock())
        bridge = Mock(device={"serial": "sample-phone"}, cleanup_verified=True)
        bridge.__enter__ = Mock(return_value=bridge)
        bridge.__exit__ = Mock()
        stop = Mock()
        stop.cancelled.is_set.return_value = False
        with (
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch("iphone_detector.remote_tunnel.settings", return_value={}),
            patch("iphone_detector.remote_tunnel.enabled", return_value=False),
            patch.object(connection_router, "find_local", return_value="192.168.1.7"),
            patch("iphone_detector.remote_check.read_pair", return_value={}),
            patch("iphone_detector.bridge_service.installed", return_value=True),
            patch("iphone_detector.wifi.WifiBridge", return_value=bridge),
            patch(
                "pymobiledevice3.lockdown.create_using_tcp",
                new_callable=AsyncMock,
                return_value=direct,
            ),
            patch(
                "pymobiledevice3.lockdown.create_using_usbmux",
                new_callable=AsyncMock,
                return_value=bridged,
            ),
            patch(
                "iphone_detector.remote_tunnel.lookup",
                new_callable=AsyncMock,
                side_effect=[ConnectionTerminatedError(), {}],
            ),
            patch(
                "iphone_detector.remote_tunnel.execute_job",
                new_callable=AsyncMock,
                side_effect=OSError("connection lost after dispatch"),
            ),
            patch("iphone_detector.remote_tunnel.submit") as remote,
        ):
            with self.assertRaisesRegex(OSError, "after dispatch"):
                connection_router.run_refresh(self.job, stop)
        self.assertTrue((self.job / "remote-request.json").exists())
        bridge.__exit__.assert_called_once()
        remote.assert_not_called()

    def test_local_failure_after_submission_never_replays_on_remote(self):
        client = Mock(udid="sample-phone", close=AsyncMock())
        with (
            patch("iphone_detector.config.load_config", return_value=self.config),
            patch("iphone_detector.remote_tunnel.settings", return_value={}),
            patch.object(connection_router, "find_local", return_value="192.168.1.7"),
            patch("iphone_detector.remote_check.read_pair", return_value={}),
            patch(
                "pymobiledevice3.lockdown.create_using_tcp",
                new_callable=AsyncMock,
                return_value=client,
            ),
            patch("iphone_detector.remote_tunnel.lookup", new_callable=AsyncMock, return_value={}),
            patch(
                "iphone_detector.remote_tunnel.execute_job",
                new_callable=AsyncMock,
                side_effect=OSError("connection lost after submission"),
            ),
            patch("iphone_detector.remote_tunnel.submit") as submit,
        ):
            with self.assertRaises(OSError):
                connection_router.run_refresh(self.job, Mock())
        submit.assert_not_called()
        self.assertTrue((self.job / "remote-request.json").exists())
