"""Cross-component recovery and stale-interaction regressions from the full audit."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import ipaddress
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch
import zipfile

from iphone_detector import connection_router as router, discord_outbox, remote_tunnel as remote
from iphone_detector import background, account_login, discord_inbox, guided_update
from iphone_detector.release_integrity import verify_members, verify_receipt
from iphone_detector.storage import write_json


class AuditRegressions(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_remote_cleanup_failure_preserves_verified_result_and_never_replays(self):
        job = self.root / "automation-runs/test"
        job.mkdir(parents=True)
        write_json(job / "remote-request.json", {"accounts": [1]})
        (job / "remote-heartbeat").touch()
        config = {"device": {"serial": "phone"}}
        request = {
            "serial": "phone",
            "timeout": 2,
            "accounts": [{"account": 1, "ipa": str(job / "original.ipa")}],
        }

        def native(*args):
            (job / "remote-1.ipa").write_bytes(b"synthetic")
            (job / "remote-1.ready").touch()
            return {"ok": True, "completed_accounts": 1}

        original_unlink = Path.unlink

        def locked(path, *args, **kwargs):
            if path.name == "remote-1.ipa":
                raise PermissionError("fixture")
            return original_unlink(path, *args, **kwargs)

        with (
            patch("iphone_detector.config.load_config", return_value=config),
            patch.object(background, "make_job", return_value=request),
            patch.object(background, "run_background", side_effect=native) as signer,
            patch.object(remote, "lookup", new_callable=AsyncMock, return_value={}),
            patch.object(remote, "compression_level", return_value=9),
            patch.object(remote, "upload_streams", return_value=1),
            patch.object(remote, "install", new_callable=AsyncMock) as installer,
            patch("iphone_detector.operations.record"),
            patch.object(Path, "unlink", locked),
        ):
            asyncio.run(remote.execute_job(job, Mock(udid="phone")))
            self.assertIs(json.loads((job / "remote-result.json").read_text())["ok"], True)
            asyncio.run(remote.execute_job(job, Mock(udid="phone")))
        signer.assert_called_once()
        installer.assert_awaited_once()

    def test_recovered_remote_attempt_and_legacy_dispatch_are_paused_without_replay(self):
        with patch.object(remote, "DATA_ROOT", self.root):
            for marker in (
                "remote-execution.json",
                "native-dispatch.json",
                "native-input.json",
                "backend-job.json",
                "backend-events.jsonl",
            ):
                job = self.root / "automation-runs" / marker
                job.mkdir(parents=True)
                write_json(job / "remote-request.json", {"accounts": [1]})
                (job / "remote-heartbeat").touch()
                (job / marker).touch()
            self.assertEqual(list(remote.pending()), [])
            for path in (self.root / "automation-runs").glob("*/remote-result.json"):
                self.assertIs(json.loads(path.read_text())["ok"], False)
            self.assertEqual(
                len(list((self.root / "automation-runs").glob("*/remote-result.json"))), 5
            )

    def test_changed_selection_is_rejected_again_at_remote_dispatch_before_signing(self):
        job = self.root / "changed-selection"
        job.mkdir()
        write_json(job / "remote-request.json", {"accounts": [1]})
        write_json(job / "selection.json", {"serial": "phone", "accounts": []})
        request = {
            "serial": "phone",
            "timeout": 2,
            "accounts": [
                {
                    "account": 1,
                    "email": "new@example.com",
                    "ipa": "new.ipa",
                    "file_identity": {"size": 1, "mtime_ns": 1, "sha256": "0" * 64},
                }
            ],
        }
        with (
            patch(
                "iphone_detector.config.load_config", return_value={"device": {"serial": "phone"}}
            ),
            patch.object(background, "make_job", return_value=request),
            patch.object(background, "run_background") as native,
            patch.object(remote, "lookup", new_callable=AsyncMock) as lookup,
        ):
            asyncio.run(remote.execute_job(job, Mock(udid="phone")))
        native.assert_not_called()
        lookup.assert_not_awaited()
        self.assertIs(json.loads((job / "remote-result.json").read_text())["ok"], False)

    def test_remote_final_receipts_reject_untyped_success_counts_and_preparation(self):
        for value in (
            [],
            {"ok": 1, "completed_accounts": 1},
            {"ok": True, "completed_accounts": True},
            {"ok": True, "completed_accounts": 1.0},
            {"ok": True, "completed_accounts": 1, "prepared_only": True},
        ):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                remote.completed_result(value, 1)
        self.assertEqual(
            remote.completed_result({"ok": True, "completed_accounts": 1}, 1)["completed_accounts"],
            1,
        )

    def test_lost_tunnel_stops_waiting_instead_of_holding_worker_forever(self):
        job = self.root / "automation-runs/test"
        job.mkdir(parents=True)
        stop = Mock()
        with (
            patch.object(remote, "DATA_ROOT", self.root),
            patch.object(
                remote,
                "state",
                side_effect=[{"state": "ready"}, {"state": "waiting"}, {"state": "waiting"}],
            ),
            patch.object(
                background, "make_job", return_value={"timeout": 60, "accounts": [{"account": 1}]}
            ),
            patch.object(remote.time, "monotonic", side_effect=[1, 2, 3, 4, 5, 40]),
        ):
            with self.assertRaisesRegex(RuntimeError, "stopped"):
                remote.submit(job, stop)
        self.assertTrue((job / "stop").exists())

    def test_usb_install_and_readiness_do_not_require_remote_or_legacy_service(self):
        config = {"device": {"serial": "phone"}}
        with (
            patch("iphone_detector.config.load_config", return_value=config),
            patch.object(router, "find_usb", return_value="phone"),
            patch.object(router, "find_local") as local,
            patch.object(remote, "enabled", return_value=False),
            patch("iphone_detector.bridge_service.installed", return_value=False),
            patch.object(
                background, "run_background", return_value={"ok": True, "completed_accounts": 1}
            ) as native,
        ):
            self.assertTrue(router.route_ready())
            result = router.run_automatic(self.root, Mock(), [0])
        local.assert_not_called()
        self.assertEqual(native.call_args.args[2], "USB")
        self.assertEqual(native.call_args.kwargs["accounts"], [0])
        self.assertEqual(result["transport"], "usb")

    def test_corrupt_optional_cache_does_not_block_authenticated_discovery(self):
        cache = self.root / "cache.json"
        service = Mock(addresses=[Mock(ip="192.168.1.3")])
        for value in ([], None, {"serial": True}, {"serial": "phone", "ip": []}):
            write_json(cache, value)
            with (
                self.subTest(value=value),
                patch.object(router, "CACHE", cache),
                patch.object(
                    router, "local_networks", return_value=[ipaddress.ip_network("192.168.1.0/24")]
                ),
                patch.object(router, "tailscale_candidate", return_value=None),
                patch.object(router, "paired_local_session") as verify,
                patch(
                    "pymobiledevice3.bonjour.browse_mobdev2",
                    new_callable=AsyncMock,
                    return_value=[service],
                ),
            ):
                self.assertEqual(router.find_local("phone", {}, Mock()), "192.168.1.3")
                verify.assert_called_once_with("192.168.1.3", "phone")

    def test_stale_discord_account_selection_cannot_write_to_replacement_account(self):
        config = {"automation": {"accounts": [{"email": "new@example.com", "ipa": "Sample"}]}}
        source = self.root / "Sample.ipa"
        source.write_bytes(b"synthetic")
        signature = (source.stat().st_size, source.stat().st_mtime_ns)
        for assign in (discord_inbox.assign_received, discord_inbox.assign_managed):
            with (
                self.subTest(assign=assign.__name__),
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config") as save,
                patch.object(discord_inbox, "import_ipa") as importer,
            ):
                with self.assertRaisesRegex(ValueError, "account changed"):
                    assign(source, signature, 0, expected_email="old@example.com")
                save.assert_not_called()
                importer.assert_not_called()
        self.assertEqual(source.read_bytes(), b"synthetic")

    def test_concurrent_report_producers_cannot_overwrite_delivery_acknowledgement(self):
        audience = {"user_id": 1, "guild_id": 2, "channel_id": 3}
        write_json(self.root / "discord.json", audience)
        key = "same-completed-job"
        self.assertTrue(discord_outbox.enqueue(self.root, key, "first"))
        path = self.root / "discord-outbox" / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        value = json.loads(path.read_text())
        write_json(path, dict(value, delivered_at=time.time()))
        original = path.read_bytes()
        with ThreadPoolExecutor(max_workers=8) as executor:
            self.assertTrue(
                all(
                    executor.map(
                        lambda _: discord_outbox.enqueue(self.root, key, "again"), range(32)
                    )
                )
            )
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(discord_outbox.pending(self.root, audience), [])

    def test_shared_server_override_works_for_login_without_reading_legacy_file(self):
        with (
            patch(
                "iphone_detector.config.load_config",
                return_value={"automation": {"anisette_server": "https://example.com"}},
            ),
            patch.object(Path, "read_text", side_effect=PermissionError("fixture")),
        ):
            self.assertEqual(account_login.server_url(), "https://example.com")

    def test_late_report_producer_cannot_reset_a_newly_acknowledged_report(self):
        audience = {"user_id": 1, "guild_id": 2, "channel_id": 3}
        write_json(self.root / "discord.json", audience)
        key = "racing-completion"
        entered, release = threading.Event(), threading.Event()
        outcome = []

        def delayed(path, value, **options):
            if threading.current_thread().name == "late-producer":
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("Producer fixture timed out")
            return write_json(path, value, **options)

        with patch.object(discord_outbox, "write_json", side_effect=delayed):
            late = threading.Thread(
                name="late-producer",
                target=lambda: outcome.append(discord_outbox.enqueue(self.root, key, "late")),
            )
            late.start()
            try:
                self.assertTrue(entered.wait(5))
                self.assertTrue(discord_outbox.enqueue(self.root, key, "first"))
                path, value = discord_outbox.pending(self.root, audience)[0]
                write_json(path, dict(value, delivered_at=time.time()))
                before = path.read_bytes()
            finally:
                release.set()
                late.join(timeout=5)
            self.assertFalse(late.is_alive())
            self.assertEqual(outcome, [True])
            self.assertEqual(path.read_bytes(), before)

    def test_bad_update_startup_acknowledgement_cleans_owned_process(self):
        process = Mock(pid=123, poll=lambda: None)
        child = Mock()
        with (
            patch.object(guided_update.subprocess, "Popen", return_value=process),
            patch.object(guided_update, "read", return_value={"nonce": "same", "component": "gui"}),
            patch.object(guided_update.uuid, "uuid4", return_value=Mock(hex="same")),
            patch.object(
                guided_update.psutil,
                "Process",
                return_value=Mock(children=lambda recursive: [child]),
            ),
        ):
            with self.assertRaises(KeyError):
                guided_update.launch(
                    self.root / "new.exe", {"discord": False, "remote": False, "queue": False}
                )
        child.terminate.assert_called_once()
        process.terminate.assert_called_once()

    def test_release_archive_rejects_symlink_and_control_character_paths(self):
        for name, mode in (("unsafe\x01name", 0), ("link", 0o120777 << 16)):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, "w") as archive:
                member = zipfile.ZipInfo(name)
                member.create_system = 3
                member.external_attr = mode
                archive.writestr(member, b"synthetic")
            with zipfile.ZipFile(stream) as archive, self.assertRaises(ValueError):
                verify_members(archive)
        with self.assertRaises(ValueError):
            verify_receipt([], b"synthetic")

    def test_usb_watch_failure_and_cancellation_have_final_receipts(self):
        import ipa_manager
        from iphone_detector.cancellation import Cancelled
        from fixtures import configured

        config = configured(
            {"device": {"serial": "phone"}, "automation": {"notify_on_finish": False}}
        )
        for error, expected in (
            (RuntimeError("fixture failure"), {"error": "fixture failure"}),
            (Cancelled("fixture cancel"), {"cancelled": True}),
        ):
            job = self.root / type(error).__name__

            def watcher(dispatcher, **kwargs):
                return Mock(run_forever=lambda **_: dispatcher.emit("connect", {}))

            with (
                self.subTest(error=type(error).__name__),
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.logger.setup_logging"),
                patch("iphone_detector.run_lock.RunLock.acquire", return_value=True),
                patch("iphone_detector.run_lock.RunLock.close"),
                patch("iphone_detector.worker_registry.register"),
                patch("iphone_detector.worker_registry.finish"),
                patch("iphone_detector.detector.IPhoneWatcher", side_effect=watcher),
                patch.object(background, "run_background", side_effect=error),
            ):
                self.assertNotEqual(ipa_manager.worker("watch", job), 0)
                self.assertEqual(json.loads((job / "result.json").read_text()), expected)

    def test_malformed_progress_stage_is_ignored_without_losing_valid_progress(self):
        from iphone_detector.discord_bot import Controller

        controller = Controller()
        controller.job = self.root
        (self.root / "backend-events.jsonl").write_text(
            '{"stage":"login","account":1}\n{"stage":[],"account":2}\n'
        )
        self.assertIn("Signing in", controller.progress())

    def test_non_object_discord_configuration_is_an_actionable_validation_error(self):
        from iphone_detector.discord_bot import validate_settings

        for value in ([], None, True):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "JSON object"):
                validate_settings(value)
