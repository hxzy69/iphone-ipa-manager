from fixtures import write_sample_app
from fixtures import configured
import asyncio
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch, Mock, AsyncMock

from iphone_detector.discord_inbox import (
    assign_received,
    assign_managed,
    received_files,
    create_view,
    matching_files,
    file_key,
    select_file,
)


class DiscordInboxTests(unittest.TestCase):
    def test_autocomplete_is_case_insensitive_and_prefers_prefix(self):
        files = [Path("Other twitch.ipa"), Path("Twitch IPA 24.ipa"), Path("Spotify.ipa")]
        self.assertEqual(matching_files(files, "TW"), [files[1], files[0]])
        self.assertEqual(select_file(files, file_key(files[1])), files[1])
        self.assertEqual(select_file(files, "Twitch IPA 24.ipa"), files[1])
        with self.assertRaises(ValueError):
            select_file(files, "tw")
        with self.assertRaises(ValueError):
            select_file(files, "../not-an-ipa")

    def test_autocomplete_caps_results_and_handles_long_filenames(self):
        files = [Path("Twitch" + str(index) + "x" * 120 + ".ipa") for index in range(30)]
        self.assertEqual(len(matching_files(files, "tw")), 25)
        self.assertLessEqual(len(file_key(files[0])), 100)
        self.assertEqual(select_file(files, file_key(files[-1])), files[-1])

    def test_managed_assignment_does_not_depend_on_receive_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Managed.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                write_sample_app(archive, "Payload/Example.app/Info.plist")
            config = configured(
                {
                    "automation": {
                        "ipa_directory": directory,
                        "import_directory": str(root / "missing-receive-folder"),
                    }
                }
            )
            stat = source.stat()
            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config") as save,
            ):
                self.assertEqual(
                    assign_managed(source, (stat.st_size, stat.st_mtime_ns), 2), source
                )
                self.assertEqual(
                    save.call_args.args[0]["automation"]["accounts"][2]["ipa"], "Managed"
                )
            self.assertEqual(len(list(root.iterdir())), 1)

    def test_received_file_cannot_be_assigned_as_managed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            managed = root / "managed"
            managed.mkdir()
            source = root / "Received.ipa"
            source.write_bytes(b"not-important")
            config = configured(
                {"automation": {"ipa_directory": str(managed), "import_directory": directory}}
            )
            stat = source.stat()
            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config") as save,
            ):
                with self.assertRaises(ValueError):
                    assign_managed(source, (stat.st_size, stat.st_mtime_ns), 0)
                save.assert_not_called()

    def test_failed_save_removes_import_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "Received.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                write_sample_app(archive, "Payload/Example.app/Info.plist")
            managed = root / "managed"
            config = configured(
                {"automation": {"ipa_directory": str(managed), "import_directory": directory}}
            )
            stat = source.stat()
            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config", side_effect=OSError("disk full")),
            ):
                with self.assertRaises(OSError):
                    assign_received(source, (stat.st_size, stat.st_mtime_ns), 0)
            self.assertEqual(list(managed.iterdir()), [])
            self.assertTrue(source.exists())

    def test_only_received_ipas_are_listed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.IPA").write_bytes(b"x")
            (root / "partial.part").write_bytes(b"x")
            (root / "folder.ipa").mkdir()
            self.assertEqual(
                [
                    p.name
                    for p in received_files(
                        configured({"automation": {"import_directory": directory}})
                    )
                ],
                ["one.IPA"],
            )

    def test_assignment_changes_only_selected_account(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "received.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                write_sample_app(archive, "Payload/Example.app/Info.plist")
            config = configured(
                {
                    "automation": {
                        "import_directory": directory,
                        "ipa_directory": str(root / "managed"),
                        "accounts": [
                            {"email": "a@example.com", "ipa": "One"},
                            {"email": "b@example.com", "ipa": "Two"},
                            {"email": "c@example.com", "ipa": "Three"},
                        ],
                    }
                }
            )
            stat = source.stat()
            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config") as save,
            ):
                result = assign_received(source, (stat.st_size, stat.st_mtime_ns), 1)
            accounts = save.call_args.args[0]["automation"]["accounts"]
            self.assertEqual(accounts[0]["ipa"], "One")
            self.assertEqual(accounts[1]["ipa"], result.stem)
            self.assertEqual(accounts[2]["ipa"], "Three")
            self.assertTrue(source.exists())

    def test_changed_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "changed.ipa"
            source.write_bytes(b"changed")
            config = configured({"automation": {"import_directory": directory}})
            with (
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.config.save_config") as save,
            ):
                with self.assertRaisesRegex(ValueError, "changed"):
                    assign_received(source, (0, 0), 0)
                save.assert_not_called()

    def test_picker_paginates_and_confirmation_starts_disabled(self):
        import discord

        async def check():
            view = create_view(
                discord, [Path("File%d.ipa" % i) for i in range(26)], lambda item: True
            )
            self.assertEqual(len(view.children[0].options), 25)
            self.assertTrue(view.children[-1].disabled)
            view.page = 1
            view.render()
            self.assertEqual(len(view.children[0].options), 1)
            self.assertIn("page 2/2", view.summary())
            view.stop()

        asyncio.run(check())

    async def _assign_through_picker(
        self, discord, root, source, config, install_account=None, file_index="0", account_index="1"
    ):
        from unittest.mock import PropertyMock

        with patch("iphone_detector.config.load_config", return_value=config):
            view = create_view(discord, [source], lambda item: True, install_account)
            select_interaction = Mock()
            select_interaction.response.edit_message = AsyncMock()
            picker, accounts = view.children[0], view.children[1]
            with patch.object(
                discord.ui.Select,
                "values",
                new_callable=PropertyMock,
                side_effect=[[file_index], [account_index]],
            ):
                await picker.callback(select_interaction)
                await accounts.callback(select_interaction)
            return view

    def test_successful_assignment_offers_single_account_install(self):
        import discord

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "received.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                write_sample_app(archive, "Payload/Example.app/Info.plist")
            config = configured(
                {
                    "automation": {
                        "import_directory": directory,
                        "ipa_directory": str(root / "managed"),
                        "accounts": [
                            {"email": "a@example.com", "ipa": "One"},
                            {"email": "b@example.com", "ipa": "Two"},
                            {"email": "c@example.com", "ipa": "Three"},
                        ],
                    }
                }
            )
            started = []

            async def install_account(index, interaction, expected=None):
                self.assertEqual(expected, {"email": "b@example.com", "ipa": "x"})
                started.append((index, interaction))

            async def check():
                view = await self._assign_through_picker(
                    discord, root, source, config, install_account
                )
                self.assertFalse(view.children[-1].disabled)
                with (
                    patch(
                        "iphone_detector.discord_inbox.assign_received",
                        return_value=root / "managed" / "x.ipa",
                    ) as assign,
                    patch("iphone_detector.config.save_config"),
                ):
                    commit = Mock()
                    commit.response.edit_message = AsyncMock()
                    commit.edit_original_response = AsyncMock()
                    await view.children[-1].callback(commit)
                self.assertEqual(assign.call_args.args[2], 1)
                install_view = commit.edit_original_response.await_args.kwargs["view"]
                self.assertIsNotNone(install_view)
                button = install_view.children[0]
                self.assertEqual(button.label, "Install account 2 now")
                self.assertFalse(button.disabled)
                stale = Mock()
                stale.response.send_message = AsyncMock()
                with patch("iphone_detector.config.load_config", return_value=config):
                    await button.callback(stale)
                stale.response.send_message.assert_awaited_once()
                self.assertEqual(started, [])
                click = Mock()
                click.response.defer = AsyncMock()
                config["automation"]["accounts"][1]["ipa"] = "x"
                with patch("iphone_detector.config.load_config", return_value=config):
                    await button.callback(click)
                self.assertEqual(started, [(1, click)])
                self.assertTrue(button.disabled)
                again = Mock()
                again.response.send_message = AsyncMock()
                await button.callback(again)
                self.assertEqual(len(started), 1)
                again.response.send_message.assert_awaited_once()

            asyncio.run(check())

    def test_assignment_without_install_callback_keeps_old_confirmation(self):
        import discord

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "received.ipa"
            with zipfile.ZipFile(source, "w") as archive:
                write_sample_app(archive, "Payload/Example.app/Info.plist")
            config = configured(
                {
                    "automation": {
                        "import_directory": directory,
                        "ipa_directory": str(root / "managed"),
                        "accounts": [
                            {"email": "a@example.com", "ipa": "One"},
                            {"email": "b@example.com", "ipa": "Two"},
                            {"email": "c@example.com", "ipa": "Three"},
                        ],
                    }
                }
            )

            async def check():
                view = await self._assign_through_picker(discord, root, source, config)
                with (
                    patch(
                        "iphone_detector.discord_inbox.assign_received",
                        return_value=root / "managed" / "x.ipa",
                    ),
                    patch("iphone_detector.config.save_config"),
                ):
                    commit = Mock()
                    commit.response.edit_message = AsyncMock()
                    commit.edit_original_response = AsyncMock()
                    await view.children[-1].callback(commit)
                self.assertIsNone(commit.edit_original_response.await_args.kwargs["view"])

            asyncio.run(check())
