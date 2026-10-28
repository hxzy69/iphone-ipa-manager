import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
import zipfile

from iphone_detector import profile_refresh as refresh


class ProfileRefreshTests(unittest.TestCase):
    def setUp(self):
        self.info = {
            "CFBundleIdentifier": "app.main",
            "CFBundleVersion": "1",
            "CFBundleShortVersionString": "1.0",
        }
        self.old = {
            "Entitlements": {"application-identifier": "TEAM.app.main"},
            "TeamIdentifier": ["TEAM"],
            "DeveloperCertificates": [b"cert"],
            "ProvisionedDevices": ["phone"],
            "ExpirationDate": datetime.now() + timedelta(days=1),
        }
        self.new = copy.deepcopy(self.old)
        self.new.update(UUID="fresh", ExpirationDate=datetime.now() + timedelta(days=7))

    def archive(self, root):
        path = Path(root) / "remote-1.ipa"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("Payload/App.app/Info.plist", plistlib.dumps(self.info))
            z.writestr("Payload/App.app/embedded.mobileprovision", b"fresh-profile")
        return path

    def test_changed_version_requires_full_install(self):
        with tempfile.TemporaryDirectory() as root:
            path = self.archive(root)
            with self.assertRaisesRegex(refresh.FullInstallRequired, "version changed"):
                refresh.prepare(
                    path, [self.old], "phone", {"app.main": dict(self.info, CFBundleVersion="2")}
                )

    def test_absent_app_requires_full_install(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaises(refresh.FullInstallRequired):
                refresh.prepare(self.archive(root), [self.old], "phone", {})

    def test_main_profile_required(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch("pymobiledevice3.services.misagent.ProvisioningProfile") as parser,
        ):
            extension = copy.deepcopy(self.new)
            extension["Entitlements"]["application-identifier"] += ".extension"
            old_extension = copy.deepcopy(extension)
            old_extension["ExpirationDate"] -= timedelta(days=2)
            parser.return_value.plist = extension
            with self.assertRaisesRegex(refresh.FullInstallRequired, "Main app profile missing"):
                refresh.prepare(
                    self.archive(root), [old_extension], "phone", {"app.main": self.info}
                )

    def test_success_requires_exact_profile_readback(self):
        self.perform(readback=b"wrong", fallback=True)

    def test_expired_installed_profile_requires_full_install(self):
        self.old["ExpirationDate"] = datetime.now() - timedelta(days=30)
        with (
            tempfile.TemporaryDirectory() as root,
            patch("pymobiledevice3.services.misagent.ProvisioningProfile") as parser,
        ):
            parser.return_value.plist = self.new
            with self.assertRaisesRegex(refresh.FullInstallRequired, "Installed profile expired"):
                refresh.prepare(self.archive(root), [self.old], "phone", {"app.main": self.info})

    def test_expired_extension_requires_full_install_for_its_account(self):
        extension = copy.deepcopy(self.new)
        extension["Entitlements"]["application-identifier"] += ".extension"
        old_extension = copy.deepcopy(extension)
        old_extension["ExpirationDate"] = datetime.now(timezone.utc).replace(
            tzinfo=None
        ) - timedelta(hours=1)
        with (
            tempfile.TemporaryDirectory() as root,
            patch("pymobiledevice3.services.misagent.ProvisioningProfile") as parser,
        ):
            path = self.archive(root)
            with zipfile.ZipFile(path, "a") as archive:
                archive.writestr(
                    "Payload/App.app/PlugIns/Extension.appex/embedded.mobileprovision", b"extension"
                )
            parser.side_effect = [Mock(plist=self.new), Mock(plist=extension)]
            with self.assertRaisesRegex(refresh.FullInstallRequired, "Installed profile expired"):
                refresh.prepare(path, [self.old, old_extension], "phone", {"app.main": self.info})

    def test_expired_profile_falls_back_before_writing(self):
        self.old["ExpirationDate"] = datetime.now() - timedelta(days=1)
        self.perform(fallback=True, expect_write=False)

    def test_device_rejection_allows_full_install(self):
        from pymobiledevice3.exceptions import PyMobileDevice3Exception

        self.perform(
            write_error=PyMobileDevice3Exception("invalid status: rejected"), fallback=True
        )

    def test_transport_failure_does_not_trigger_full_install(self):
        self.perform(write_error=ConnectionError("connection lost"), failure="connection lost")

    def test_daemon_falls_back_once_for_only_the_affected_account(self):
        from iphone_detector import remote_tunnel as remote
        import time

        for known_content, failure in (
            (True, None),
            (True, ConnectionError("offline")),
            (False, None),
        ):
            with (
                self.subTest(known_content=known_content, failure=failure),
                tempfile.TemporaryDirectory() as root,
            ):
                job = Path(root)
                remote.atomic(job / "remote-request.json", {"accounts": [2]})
                (job / "remote-heartbeat").touch()
                (job / "refresh-request").touch()

                def native(job, stop, *args):
                    (job / "remote-2.ipa").write_bytes(b"archive")
                    (job / "remote-2.ready").touch()
                    while not stop.cancelled.is_set() and not (job / "remote-2.installed").exists():
                        time.sleep(0.01)
                    return {"ok": True, "completed_accounts": 1}

                with (
                    patch(
                        "iphone_detector.config.load_config",
                        return_value={"device": {"serial": "phone"}},
                    ),
                    patch(
                        "iphone_detector.background.make_job",
                        return_value={"timeout": 5, "accounts": [{"account": 2, "ipa": "App.ipa"}]},
                    ),
                    patch("iphone_detector.background.run_background", side_effect=native),
                    patch(
                        "iphone_detector.operations.installed_content_matches",
                        return_value=known_content,
                    ),
                    patch.object(remote, "compression_level", return_value=9),
                    patch.object(remote, "upload_streams", return_value=1),
                    patch.object(remote, "lookup", new_callable=AsyncMock, return_value={}),
                    patch.object(remote, "require_full_archive", new_callable=AsyncMock) as full,
                    patch.object(remote, "install", new_callable=AsyncMock) as install,
                    patch.object(
                        refresh,
                        "refresh",
                        new_callable=AsyncMock,
                        return_value=False,
                        side_effect=failure,
                    ) as renew,
                ):
                    asyncio.run(remote.execute_job(job, Mock(udid="phone", name="iPhone")))
                if not known_content:
                    renew.assert_not_awaited()
                receipt = json.loads((job / "remote-result.json").read_text())
                if failure:
                    full.assert_not_awaited()
                    install.assert_not_awaited()
                    self.assertFalse(receipt["ok"])
                else:
                    full.assert_awaited_once()
                    install.assert_awaited_once()
                    self.assertEqual(install.await_args.args[1].name, "remote-2.ipa")
                    self.assertEqual(receipt["installed_accounts"], [2])
                    self.assertEqual(receipt["refreshed_accounts"], 0)

    def test_failed_write_is_not_a_fallback(self):
        self.perform(write_failure=True, failure="write failed")

    def test_success_records_verified_profile_transfer(self):
        self.perform(readback=b"fresh-profile")

    def test_expiry_window_waits_for_every_old_profile(self):
        later = self.old["ExpirationDate"] + timedelta(hours=3)
        earlier_new = dict(self.new, ExpirationDate=self.new["ExpirationDate"] - timedelta(hours=2))
        report = refresh.expiry_evidence(
            [(b"a", self.new, self.old["ExpirationDate"]), (b"b", earlier_new, later)]
        )
        self.assertEqual(
            datetime.fromisoformat(report["expiry_test_not_before"]).replace(tzinfo=None), later
        )
        self.assertEqual(
            datetime.fromisoformat(report["expiry_test_before"]).replace(tzinfo=None),
            earlier_new["ExpirationDate"],
        )
        self.assertEqual(len(report["expiration_changes"]), 2)

    def test_reuses_only_verified_inventory_within_job(self):
        service, installer = AsyncMock(), AsyncMock()
        newer = dict(
            self.new, UUID="second", ExpirationDate=self.new["ExpirationDate"] + timedelta(hours=1)
        )
        installer.lookup.return_value = {"app.main": self.info}
        service.copy_all.side_effect = [
            [Mock(plist=self.old)],
            [Mock(plist=self.new, buf=b"fresh-profile")],
            [Mock(plist=newer, buf=b"fresh-profile")],
        ]
        sc, ic = MagicMock(), MagicMock()
        sc.__aenter__.return_value, ic.__aenter__.return_value = service, installer
        with (
            tempfile.TemporaryDirectory() as root,
            patch("pymobiledevice3.services.misagent.MisagentService", return_value=sc),
            patch(
                "pymobiledevice3.services.installation_proxy.InstallationProxyService",
                return_value=ic,
            ),
            patch("pymobiledevice3.services.misagent.ProvisioningProfile") as parser,
        ):
            parser.side_effect = [Mock(plist=self.new), Mock(plist=newer)]
            inventory = refresh.Inventory()
            rsd = Mock(udid="phone")

            async def run():
                self.assertTrue(await refresh.refresh(rsd, self.archive(root), inventory))
                self.assertTrue(await refresh.refresh(rsd, self.archive(root), inventory))

            asyncio.run(run())
            self.assertEqual(installer.lookup.await_count, 1)
            self.assertEqual(service.copy_all.await_count, 3)
            self.assertEqual(inventory.profiles, [newer])

    def perform(
        self,
        readback=b"fresh-profile",
        write_failure=False,
        failure=None,
        fallback=False,
        expect_write=True,
        write_error=None,
    ):
        installer, service = AsyncMock(), AsyncMock()
        installer.lookup.return_value = {"app.main": self.info}
        service.copy_all.side_effect = [
            [Mock(plist=self.old)],
            [Mock(plist=self.new, buf=readback)],
        ]
        if write_failure:
            service.install.side_effect = RuntimeError("write failed")
        if write_error:
            service.install.side_effect = write_error
        install_context, service_context = MagicMock(), MagicMock()
        install_context.__aenter__.return_value = installer
        service_context.__aenter__.return_value = service
        with (
            tempfile.TemporaryDirectory() as root,
            patch(
                "pymobiledevice3.services.installation_proxy.InstallationProxyService",
                return_value=install_context,
            ),
            patch(
                "pymobiledevice3.services.misagent.MisagentService", return_value=service_context
            ),
            patch("pymobiledevice3.services.misagent.ProvisioningProfile") as parser,
        ):
            parser.return_value.plist = self.new
            path = self.archive(root)
            inventory = refresh.Inventory()
            if failure:
                with self.assertRaisesRegex(Exception, failure):
                    asyncio.run(refresh.refresh(Mock(udid="phone"), path, inventory))
                self.assertFalse((Path(root) / "profile-refresh-1.json").exists())
                self.assertIsNone(inventory.profiles)
            elif fallback:
                self.assertFalse(asyncio.run(refresh.refresh(Mock(udid="phone"), path, inventory)))
                self.assertIsNone(inventory.profiles)
                self.assertFalse((Path(root) / "profile-refresh-1.json").exists())
                report = json.loads((Path(root) / "profile-fallback-1.json").read_text())
                self.assertTrue(report["full_install_required"])
            else:
                self.assertTrue(asyncio.run(refresh.refresh(Mock(udid="phone"), path, inventory)))
                self.assertTrue((Path(root) / "profile-refresh-1.json").exists())
                report = json.loads((Path(root) / "profile-refresh-1.json").read_text())
                self.assertFalse(report["expiry_behavior_verified"])
                self.assertEqual(
                    report["expiration_changes"][0]["before"],
                    self.old["ExpirationDate"].isoformat() + "+00:00",
                )
            if expect_write:
                service.install.assert_awaited_once()
            else:
                service.install.assert_not_awaited()
