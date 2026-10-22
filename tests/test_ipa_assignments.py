from fixtures import configured
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

from iphone_detector.ipa_assignments import acknowledge_installs, mark_pending, needs_install


class AssignmentTests(unittest.TestCase):
    def test_replaced_source_never_clears_pending_install(self):
        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            source = job / "New.ipa"
            source.write_bytes(b"old")
            stat = source.stat()
            request = {
                "accounts": [
                    {
                        "account": 2,
                        "ipa": str(source),
                        "file_identity": {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns},
                    }
                ]
            }
            (job / "backend-job.json").write_text(json.dumps(request))
            source.write_bytes(b"new-content")
            config = configured(
                {
                    "automation": {
                        "pending_ipa_installs": {"2": "New"},
                        "accounts": [
                            {"email": "one@example.com", "ipa": "One"},
                            {"email": "two@example.com", "ipa": "New"},
                            {"email": "three@example.com", "ipa": "Three"},
                        ],
                    }
                }
            )
            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config") as save,
            ):
                acknowledge_installs(job, {"ok": True, "completed_accounts": 1})
                save.assert_not_called()
                self.assertTrue(needs_install(config, 2, "New"))

    def test_assignment_persists_and_every_job_reads_new_file(self):
        from iphone_detector import config as store
        from iphone_detector.background import make_job
        from iphone_detector.discord_inbox import assign_managed
        import zipfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "New App.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("Payload/New.app/Info.plist", b"plist")
            initial = configured(
                {"device": {"serial": "phone"}, "automation": {"ipa_directory": directory}}
            )
            with patch.object(store, "CONFIG_PATH", root / "config.json"):
                store.save_config(initial)
                stat = source.stat()
                assign_managed(source, (stat.st_size, stat.st_mtime_ns), 1)
                reloaded = store.load_config()
                self.assertTrue(needs_install(reloaded, 2, source.stem))
                for transport in ("USB", "Network", "Remote"):
                    request = make_job(reloaded, transport, accounts=[1])
                    self.assertEqual(request["accounts"][0]["ipa"], str(source))

    def test_only_verified_full_install_clears_pending_assignment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = configured(
                {
                    "automation": {
                        "accounts": [
                            {"email": "one@example.com", "ipa": "One"},
                            {"email": "two@example.com", "ipa": "New"},
                            {"email": "three@example.com", "ipa": "Three"},
                        ]
                    }
                }
            )
            mark_pending(config, 1, "New")
            request = {
                "profile_refresh": True,
                "accounts": [{"account": 2, "ipa": str(root / "New.ipa")}],
            }
            (root / "backend-job.json").write_text(json.dumps(request))
            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config") as save,
            ):
                for result in (
                    {"ok": False, "completed_accounts": 1},
                    {"ok": True, "completed_accounts": 1, "installed_accounts": []},
                    {"ok": True, "completed_accounts": 0, "installed_accounts": [2]},
                ):
                    acknowledge_installs(root, result)
                    self.assertTrue(needs_install(config, 2, "New"))
                save.assert_not_called()
                acknowledge_installs(
                    root, {"ok": True, "completed_accounts": 1, "installed_accounts": [2]}
                )
                self.assertFalse(needs_install(config, 2, "New"))
                save.assert_called_once()

    def test_pending_new_ipa_forces_full_install_during_refresh(self):
        from iphone_detector import remote_tunnel as remote

        with tempfile.TemporaryDirectory() as directory:
            job = Path(directory)
            remote.atomic(job / "remote-request.json", {"accounts": [2]})
            (job / "remote-heartbeat").touch()
            (job / "refresh-request").touch()
            config = configured(
                {
                    "device": {"serial": "phone"},
                    "automation": {"pending_ipa_installs": {"2": "New"}},
                }
            )
            request = {"timeout": 5, "accounts": [{"account": 2, "ipa": str(job / "New.ipa")}]}

            def native(*args):
                (job / "remote-2.ready").touch()
                (job / "remote-2.ipa").write_bytes(b"ipa")
                import time

                for _ in range(100):
                    if (job / "remote-2.installed").exists():
                        return {"ok": True, "completed_accounts": 1}
                    time.sleep(0.03)
                raise RuntimeError("No install acknowledgement")

            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.background.make_job", return_value=request),
                patch("iphone_detector.background.run_background", side_effect=native),
                patch.object(remote, "compression_level", return_value=9),
                patch.object(remote, "upload_streams", return_value=1),
                patch.object(remote, "lookup", new_callable=AsyncMock, return_value=configured()),
                patch.object(remote, "require_full_archive", new_callable=AsyncMock) as full,
                patch.object(remote, "install", new_callable=AsyncMock) as install,
                patch("iphone_detector.profile_refresh.refresh", new_callable=AsyncMock) as refresh,
            ):
                asyncio.run(remote.execute_job(job, Mock(udid="phone", name="iPhone")))
            refresh.assert_not_awaited()
            full.assert_awaited_once()
            install.assert_awaited_once()
            result = json.loads((job / "remote-result.json").read_text())
            self.assertTrue(result["ok"])
            self.assertEqual(result["installed_accounts"], [2])
            self.assertEqual(result["refreshed_accounts"], 0)
