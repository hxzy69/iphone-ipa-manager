import json
from pathlib import Path
import plistlib
import tempfile
import time
import unittest
import asyncio
from unittest.mock import patch, Mock, AsyncMock, MagicMock
import zipfile

from iphone_detector import remote_tunnel as remote


class RemoteTunnelTests(unittest.TestCase):
    def test_progress_write_failure_does_not_abort_transfer(self):
        with (
            patch.object(remote, "atomic", side_effect=PermissionError()),
            patch.object(remote, "LOG"),
        ):
            remote.transfer_progress(
                Path("job/remote-1.ipa"), {"uploaded_bytes": 8, "archive_bytes": 16}, "upload"
            )

    def test_upload_preserves_bytes_and_refreshes_existing_app_with_upgrade(self):
        afc, installer = AsyncMock(), AsyncMock()
        afc_context, install_context = MagicMock(), MagicMock()
        afc_context.__aenter__.return_value = afc
        install_context.__aenter__.return_value = installer
        payload = b"x" * (8 * 1024 * 1024) + b"last-block"
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "bundle_id", return_value="signed.app"),
            patch.object(remote, "lookup", new_callable=AsyncMock, return_value={"signed.app": {}}),
            patch("pymobiledevice3.services.afc.AfcService", return_value=afc_context),
            patch(
                "pymobiledevice3.services.installation_proxy.InstallationProxyService",
                return_value=install_context,
            ),
        ):
            path = Path(folder) / "signed.ipa"
            path.write_bytes(payload)
            asyncio.run(remote.install(Mock(udid="phone"), path))
        chunks = [call.args[1] for call in afc.fwrite.await_args_list]
        self.assertEqual([len(chunk) for chunk in chunks], [8 * 1024 * 1024, 10])
        self.assertEqual(b"".join(chunks), payload)
        self.assertEqual(installer.send_package.await_args.args[0], "Upgrade")
        installer.send_package.assert_awaited_once()
        afc.rm_single.assert_awaited_once()

    def test_new_app_uses_install_and_failed_verification_stops_without_retry(self):
        afc, installer = AsyncMock(), AsyncMock()
        afc_context, install_context = MagicMock(), MagicMock()
        afc_context.__aenter__.return_value = afc
        install_context.__aenter__.return_value = installer
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "bundle_id", return_value="new.app"),
            patch.object(remote, "lookup", new_callable=AsyncMock, return_value={}),
            patch("pymobiledevice3.services.afc.AfcService", return_value=afc_context),
            patch(
                "pymobiledevice3.services.installation_proxy.InstallationProxyService",
                return_value=install_context,
            ),
        ):
            path = Path(folder) / "signed.ipa"
            path.write_bytes(b"archive")
            with self.assertRaisesRegex(RuntimeError, "not found"):
                asyncio.run(remote.install(Mock(), path, {}))
        self.assertEqual(installer.send_package.await_args.args[0], "Install")
        installer.send_package.assert_awaited_once()
        afc.rm_single.assert_awaited_once()

    def test_successful_install_updates_inventory_for_the_next_account(self):
        afc, installer = AsyncMock(), AsyncMock()
        afc_context, install_context = MagicMock(), MagicMock()
        afc_context.__aenter__.return_value = afc
        install_context.__aenter__.return_value = installer
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "bundle_id", return_value="signed.app"),
            patch.object(remote, "lookup", new_callable=AsyncMock, return_value={"signed.app": {}}),
            patch("pymobiledevice3.services.afc.AfcService", return_value=afc_context),
            patch(
                "pymobiledevice3.services.installation_proxy.InstallationProxyService",
                return_value=install_context,
            ),
        ):
            path = Path(folder) / "signed.ipa"
            path.write_bytes(b"archive")
            inventory = {}
            asyncio.run(remote.install(Mock(), path, inventory))
            asyncio.run(remote.install(Mock(), path, inventory))
        self.assertEqual(
            [call.args[0] for call in installer.send_package.await_args_list],
            ["Install", "Upgrade"],
        )

    def test_remote_compression_uses_strongest_for_relay_and_balanced_for_direct(self):
        with (
            patch.object(remote, "settings", return_value={}),
            patch(
                "iphone_detector.connection_router.tailscale_candidate", return_value=None
            ) as candidate,
        ):
            self.assertEqual(remote.compression_level(), 9)
            candidate.return_value = "192.168.1.7"
            self.assertEqual(remote.compression_level(), 6)
            candidate.side_effect = OSError("Unavailable status")
            self.assertEqual(remote.compression_level(), 9)

    def test_status_replacement_retries_windows_reader_contention(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            original = Path.replace
            calls = []

            def replace(source, destination):
                calls.append(1)
                if len(calls) < 3:
                    raise PermissionError("File is briefly open in a reader")
                return original(source, destination)

            with patch.object(Path, "replace", replace), patch.object(remote.time, "sleep"):
                remote.atomic(path, {"state": "ready"})
            self.assertEqual(len(calls), 3)
            self.assertEqual(json.loads(path.read_text())["state"], "ready")

    def test_busy_status_file_does_not_report_dead_process(self):
        with patch.object(Path, "read_text", side_effect=PermissionError()):
            self.assertEqual(remote.state()["state"], "connecting")

    def test_status_write_failure_never_escapes_into_tunnel_cleanup(self):
        async def connection(config, publish):
            publish("ready")
            publish("installing")

        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "DATA_ROOT", Path(folder)),
            patch.object(remote, "enabled", side_effect=[True, False]),
            patch.object(remote, "settings", return_value={}),
            patch.object(remote, "atomic", side_effect=PermissionError()),
            patch.object(remote, "LOG"),
            patch.object(
                remote, "connected", new_callable=AsyncMock, side_effect=connection
            ) as connected,
            patch("iphone_detector.run_lock.RunLock") as lock,
        ):
            asyncio.run(remote.daemon())
            connected.assert_awaited_once()
            lock.return_value.close.assert_called_once()

    def test_stale_state_cannot_be_used_as_ready(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.json"
            remote.atomic(path, {"state": "ready", "updated": time.time() - 90})
            with patch.object(remote, "STATE", path):
                self.assertEqual(remote.state()["state"], "offline")

    def test_frozen_daemon_gets_independent_runtime_lifetime(self):
        with (
            patch.object(remote, "enabled", return_value=True),
            patch.object(remote, "state", return_value={"state": "offline"}),
            patch.object(remote.subprocess, "Popen") as launch,
        ):
            remote.ensure_started()
            self.assertEqual(launch.call_args.kwargs["env"]["PYINSTALLER_RESET_ENVIRONMENT"], "1")

    def test_live_tunnel_prevents_restart_when_status_file_is_stale(self):
        with (
            patch.object(remote, "FROZEN", True),
            patch.object(remote, "enabled", return_value=True),
            patch.object(remote, "state", return_value={"state": "offline"}),
            patch("iphone_detector.live_status.query", return_value={"tunnel": "ready"}),
            patch("iphone_detector.startup_task.start_existing") as task,
            patch.object(remote.subprocess, "Popen") as launch,
        ):
            remote.ensure_started()
            task.assert_not_called()
            launch.assert_not_called()

    def test_matching_startup_task_is_preferred_to_child_process(self):
        with (
            patch.object(remote, "FROZEN", True),
            patch.object(remote, "enabled", return_value=True),
            patch.object(remote, "state", return_value={"state": "offline"}),
            patch("iphone_detector.live_status.query", return_value=None),
            patch("iphone_detector.startup_task.start_existing", return_value=True) as task,
            patch.object(remote.subprocess, "Popen") as launch,
        ):
            remote.ensure_started()
            task.assert_called_once()
            launch.assert_not_called()

    def test_remote_startup_task_is_separate_and_uses_normal_permissions(self):
        from iphone_detector.startup_task import configure
        from unittest.mock import MagicMock

        scheduler = MagicMock()
        with (
            patch("win32com.client.Dispatch", return_value=scheduler),
            patch("iphone_detector.bridge_service.current_sid", return_value="owner"),
        ):
            configure(
                True, [r"C:\App\manager.exe", "--remote-daemon"], name="iPhone IPA Remote Tunnel"
            )
        self.assertEqual(
            scheduler.GetFolder.return_value.RegisterTaskDefinition.call_args.args[0],
            "iPhone IPA Remote Tunnel",
        )
        self.assertEqual(scheduler.NewTask.return_value.Principal.RunLevel, 0)

    def test_expired_requests_fail_instead_of_replaying_on_reconnect(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "DATA_ROOT", Path(folder)),
        ):
            job = Path(folder) / "automation-runs/test"
            job.mkdir(parents=True)
            (job / "remote-request.json").write_text("{}")
            self.assertEqual(list(remote.pending()), [])
            self.assertFalse(json.loads((job / "remote-result.json").read_text())["ok"])

    def test_completed_request_is_never_replayed(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "DATA_ROOT", Path(folder)),
        ):
            job = Path(folder) / "automation-runs/test"
            job.mkdir(parents=True)
            (job / "remote-request.json").write_text("{}")
            (job / "remote-heartbeat").touch()
            remote.atomic(job / "remote-result.json", {"ok": True})
            self.assertEqual(list(remote.pending()), [])

    def test_verification_uses_main_signed_bundle_not_extension(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signed.ipa"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr(
                    "Payload/App.app/Info.plist",
                    plistlib.dumps({"CFBundleIdentifier": "signed.main"}),
                )
                archive.writestr(
                    "Payload/App.app/PlugIns/Extension.appex/Info.plist",
                    plistlib.dumps({"CFBundleIdentifier": "signed.extension"}),
                )
            self.assertEqual(remote.bundle_id(path), "signed.main")

    def test_unsigned_or_ambiguous_package_rejected_before_upload(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "signed.ipa"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("Payload/App.app/Info.plist", plistlib.dumps({}))
            with self.assertRaises(RuntimeError):
                remote.bundle_id(path)

    def test_wrong_tunnel_identity_never_starts_native_signing(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch(
                "iphone_detector.config.load_config",
                return_value={"device": {"serial": "expected"}},
            ),
            patch("iphone_detector.background.make_job", return_value={"timeout": 900}),
            patch("iphone_detector.background.run_background") as native,
        ):
            job = Path(folder)
            asyncio.run(remote.execute_job(job, Mock(udid="other-phone", name="iPhone")))
            native.assert_not_called()
            self.assertFalse(json.loads((job / "remote-result.json").read_text())["ok"])

    def test_cancelled_submission_signals_daemon_immediately(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "DATA_ROOT", Path(folder)),
            patch.object(remote, "state", return_value={"state": "ready"}),
            patch("iphone_detector.background.make_job", return_value={"timeout": 900}),
        ):
            job = Path(folder) / "automation-runs/test"
            job.mkdir(parents=True)
            stop = Mock()
            stop.check.side_effect = RuntimeError("cancelled")
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                remote.submit(job, stop)
            self.assertTrue((job / "stop").exists())

    def test_transfer_failure_never_acknowledges_account_or_retries_install(self):
        def native(job, stop, *args):
            stop.cancelled.wait(3)
            raise RuntimeError("Stopped native signing")

        with (
            tempfile.TemporaryDirectory() as folder,
            patch(
                "iphone_detector.config.load_config", return_value={"device": {"serial": "phone"}}
            ),
            patch("iphone_detector.background.make_job", return_value={"timeout": 2}),
            patch("iphone_detector.background.run_background", side_effect=native),
            patch.object(remote, "compression_level", return_value=9),
            patch.object(remote, "lookup", new_callable=AsyncMock, return_value={}),
            patch.object(
                remote,
                "install",
                new_callable=AsyncMock,
                side_effect=RuntimeError("transfer failed"),
            ) as install,
        ):
            job = Path(folder)
            (job / "remote-heartbeat").touch()
            remote.atomic(job / "remote-request.json", {"accounts": [1, 2, 3]})
            (job / "remote-1.ready").touch()
            (job / "remote-1.ipa").touch()
            asyncio.run(remote.execute_job(job, Mock(udid="phone", name="iPhone")))
            install.assert_awaited_once()
            self.assertFalse((job / "remote-1.installed").exists())
            self.assertFalse((job / "remote-1.ipa").exists())
            self.assertFalse(json.loads((job / "remote-result.json").read_text())["ok"])

    def test_submit_records_requested_accounts_for_the_daemon(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "DATA_ROOT", Path(folder)),
            patch.object(remote, "state", return_value={"state": "ready"}),
            patch(
                "iphone_detector.background.make_job",
                return_value={"timeout": 900, "accounts": [{"account": 2}]},
            ),
        ):
            job = Path(folder) / "automation-runs/test"
            job.mkdir(parents=True)
            remote.atomic(job / "remote-result.json", {"ok": True, "completed_accounts": 1})
            remote.submit(job, Mock(), accounts=[1])
            request = json.loads((job / "remote-request.json").read_text(encoding="utf-8"))
            self.assertEqual(request["accounts"], [2])

    def test_unserviceable_queue_path_fails_before_submitting(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.object(remote, "DATA_ROOT", Path(folder)),
        ):
            job = Path(folder) / "gui-jobs/test"
            job.mkdir(parents=True)
            with self.assertRaisesRegex(ValueError, "automation-runs"):
                remote.submit(job, Mock())
            self.assertFalse((job / "remote-request.json").exists())

    def test_job_accounts_requires_explicit_selection_and_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as folder:
            job = Path(folder)
            with self.assertRaises(RuntimeError):
                remote.job_accounts(job)
            remote.atomic(job / "remote-request.json", {"accounts": [2]})
            self.assertEqual(remote.job_accounts(job), [2])
            remote.atomic(job / "remote-request.json", {})
            with self.assertRaises(RuntimeError):
                remote.job_accounts(job)
            for bad in ([], "2", [True], [2, 2], [26], [0], ["2"], [1, "x"]):
                remote.atomic(job / "remote-request.json", {"accounts": bad})
                with self.assertRaises(RuntimeError):
                    remote.job_accounts(job)
            (job / "remote-request.json").write_text("{broken")
            with self.assertRaises(RuntimeError):
                remote.job_accounts(job)

    def test_daemon_installs_only_the_requested_account(self):
        def native(job, stop, *args, **kwargs):
            (job / "remote-2.ready").touch()
            (job / "remote-2.ipa").write_bytes(b"ipa")
            for _ in range(200):
                if (job / "remote-2.installed").exists():
                    break
                time.sleep(0.05)
            return {"ok": True, "completed_accounts": 1}

        with (
            tempfile.TemporaryDirectory() as folder,
            patch(
                "iphone_detector.config.load_config", return_value={"device": {"serial": "phone"}}
            ),
            patch(
                "iphone_detector.background.make_job",
                return_value={"timeout": 5, "accounts": [{"account": 2, "ipa": "App.ipa"}]},
            ) as make,
            patch("iphone_detector.background.run_background", side_effect=native),
            patch.object(remote, "compression_level", return_value=9),
            patch.object(remote, "upload_streams", return_value=1),
            patch.object(remote, "lookup", new_callable=AsyncMock, return_value={}),
            patch.object(remote, "install", new_callable=AsyncMock) as install,
        ):
            job = Path(folder)
            remote.atomic(job / "remote-request.json", {"accounts": [2]})
            (job / "remote-heartbeat").touch()
            asyncio.run(remote.execute_job(job, Mock(udid="phone", name="iPhone")))
            self.assertEqual(make.call_args.kwargs.get("accounts"), [1])
            self.assertEqual(install.await_args.args[1].name, "remote-2.ipa")
            result = json.loads((job / "remote-result.json").read_text(encoding="utf-8"))
            self.assertTrue(result["ok"])
            self.assertEqual(result["completed_accounts"], 1)
            self.assertEqual(result["accounts"], [2])
            self.assertFalse((job / "remote-1.ready").exists())
            self.assertFalse((job / "remote-3.ready").exists())
