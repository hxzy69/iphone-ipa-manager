from fixtures import configured
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch
import plistlib
import zipfile

from iphone_detector import account_login as login
from iphone_detector.settings import get_workflow
from fixtures import ACCOUNTS as DEFAULT_ACCOUNTS


class AccountLoginTests(unittest.TestCase):
    def test_dynamic_workflow_and_pending_install_for_fourth_account(self):
        from iphone_detector.ipa_assignments import needs_install

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Fourth.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr(
                    "Payload/Fourth.app/Info.plist",
                    plistlib.dumps(
                        {
                            "CFBundleIdentifier": "app.fourth",
                            "CFBundleVersion": "1",
                            "CFBundleExecutable": "Fourth",
                        }
                    ),
                )
                archive.writestr("Payload/Fourth.app/Fourth", b"sample executable")
            stat = source.stat()
            config = configured(
                {"automation": {"ipa_directory": directory}, "device": {"serial": "preserved"}}
            )
            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config") as save,
            ):
                self.assertEqual(
                    login.append_account(
                        "New@Example.com", source, (stat.st_size, stat.st_mtime_ns)
                    ),
                    4,
                )
                saved = save.call_args.args[0]
            workflow = get_workflow(saved)
            self.assertEqual(len(workflow), 4)
            self.assertEqual(workflow[3]["email"], "new@example.com")
            self.assertTrue(needs_install(saved, 4, "Fourth"))
            self.assertEqual(
                [entry["ipa"] for entry in workflow[:3]],
                [entry["ipa"] for entry in DEFAULT_ACCOUNTS],
            )
            self.assertEqual(saved["device"], {"serial": "preserved"})
            self.assertTrue(saved["automation"]["background_mode"])

    def test_account_enrollment_rejects_duplicate_changed_or_unmanaged_file(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Fourth.ipa"
            source.write_bytes(b"not-an-archive")
            config = configured({"automation": {"ipa_directory": directory}})
            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config") as save,
            ):
                for email, path, signature in (
                    (DEFAULT_ACCOUNTS[0]["email"], source, (0, 0)),
                    ("new@example.com", source, (0, 0)),
                    ("new@example.com", source.parent / "missing.ipa", (0, 0)),
                ):
                    with self.assertRaises(ValueError):
                        login.append_account(email, path, signature)
                save.assert_not_called()

    def test_authentication_uses_pipes_not_arguments_files_or_logs(self):
        async def run():
            process = Mock(returncode=None)
            process.stdin = Mock(drain=AsyncMock())
            process.wait = AsyncMock(return_value=0)
            with (
                patch.object(
                    login.asyncio,
                    "create_subprocess_exec",
                    new_callable=AsyncMock,
                    return_value=process,
                ) as spawn,
                patch.object(login, "server_url", return_value="https://example.com"),
                patch.object(login.RunLock, "acquire", return_value=True),
                patch.object(login.RunLock, "close") as release,
            ):
                session = login.LoginSession()
                await session.start("new@example.com", "secret-password")
                self.assertNotIn("secret-password", str(spawn.call_args))
                packet = json.loads(process.stdin.write.call_args.args[0])
                self.assertEqual(packet["password"], "secret-password")
                self.assertEqual(spawn.call_args.args[-1], "--account-auth")
                self.assertEqual(spawn.call_args.kwargs["stderr"], asyncio.subprocess.DEVNULL)
                await session.close()
                await session.close()
                process.terminate.assert_called_once()
                release.assert_called_once()

        asyncio.run(run())

    def test_stale_and_replayed_verification_responses_are_rejected(self):
        async def run():
            session = login.LoginSession()
            session.process = Mock(returncode=None, stdin=Mock(drain=AsyncMock()))
            session.challenge, session.waiting = 2, True
            with self.assertRaises(ValueError):
                await session.respond(1, {"action": "code", "code": "123456"})
            await session.respond(2, {"action": "code", "code": "123456"})
            with self.assertRaises(ValueError):
                await session.respond(2, {"action": "resend"})
            self.assertEqual(session.process.stdin.write.call_count, 1)

        asyncio.run(run())

    def test_incomplete_or_wrong_email_receipt_is_never_a_success(self):
        async def run():
            for events, code in (
                ([b""], 0),
                ([b'{"stage":"complete","email":"other@example.com"}\n'], 0),
                ([b'{"stage":"complete","email":"new@example.com"}\n'], 1),
            ):
                session = login.LoginSession()
                session.email = "new@example.com"
                session.process = Mock(
                    stdout=Mock(readline=AsyncMock(side_effect=events)),
                    wait=AsyncMock(return_value=code),
                )
                with self.assertRaises(ValueError):
                    async for event in session.events():
                        self.fail("Unverified completion was yielded")

        asyncio.run(run())

    def test_cancellation_during_launch_does_not_leave_a_helper_running(self):
        async def run():
            started, finish = asyncio.Event(), asyncio.Event()
            process = Mock(returncode=None, stdin=Mock(), wait=AsyncMock(return_value=0))

            async def spawn(*args, **kwargs):
                started.set()
                await finish.wait()
                return process

            with (
                patch.object(login.asyncio, "create_subprocess_exec", side_effect=spawn),
                patch.object(login, "server_url", return_value="https://example.com"),
                patch.object(login.RunLock, "acquire", return_value=True),
                patch.object(login.RunLock, "close"),
            ):
                session = login.LoginSession()
                task = asyncio.create_task(session.start("new@example.com", "password"))
                await started.wait()
                await session.close()
                finish.set()
                with self.assertRaises(ValueError):
                    await task
                process.terminate.assert_called_once()
                process.stdin.write.assert_not_called()

        asyncio.run(run())

    def test_saved_iloader_list_keeps_other_preferences_and_deduplicates(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.dict("os.environ", {"APPDATA": directory}),
        ):
            path = Path(directory) / "me.nabdev.iloader/data.json"
            path.parent.mkdir()
            path.write_text(json.dumps({"ids": ["OLD@example.com"], "other": 7}))
            login.save_iloader_account("new@example.com")
            login.save_iloader_account("new@example.com")
            self.assertEqual(
                json.loads(path.read_text()),
                {"ids": ["OLD@example.com", "new@example.com"], "other": 7},
            )
