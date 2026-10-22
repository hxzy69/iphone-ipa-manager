import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from iphone_detector import discord_controls as controls, worker_registry as registry


class ManagementTests(unittest.TestCase):
    def test_management_autocomplete_suggests_values_without_leaking_to_other_users(self):
        import discord
        from discord import app_commands
        from iphone_detector.discord_management import register

        async def run():
            client = discord.Client(intents=discord.Intents.none())
            tree = app_commands.CommandTree(client)
            guild = discord.Object(id=2)
            permitted = [True]
            with tempfile.TemporaryDirectory() as directory:
                config = self.config(directory)
                config["automation"]["install_timeout"] = 17 * 60
                with (
                    patch("iphone_detector.config.load_config", return_value=config),
                    patch(
                        "iphone_detector.discord_inbox.managed_files",
                        return_value=[Path("sample.ipa")] * 21,
                    ),
                ):
                    register(
                        tree,
                        guild,
                        AsyncMock(return_value=True),
                        discord,
                        lambda item: permitted[0],
                    )
                    settings = tree.get_command("settings", guild=guild)
                    folder = settings._params["folder"].autocomplete
                    timeout = settings._params["timeout_minutes"].autocomplete
                    page = tree.get_command("files", guild=guild)._params["page"].autocomplete
                    interaction = Mock()
                    self.assertIn(
                        directory, [choice.value for choice in await folder(interaction, "")]
                    )
                    self.assertIn(17, [choice.value for choice in await timeout(interaction, "")])
                    self.assertEqual(
                        [choice.value for choice in await page(interaction, "")], [1, 2, 3]
                    )
                    self.assertEqual([choice.value for choice in await page(interaction, "2")], [2])
                    permitted[0] = False
                    for callback in (folder, timeout, page):
                        self.assertEqual(await callback(interaction, ""), [])
            await client.close()

        asyncio.run(run())

    def config(self, folder):
        return {
            "automation": {
                "ipa_directory": str(folder),
                "accounts": [
                    {"email": "account%d@example.com" % i, "ipa": "App%d" % i} for i in range(3)
                ],
            }
        }

    def test_settings_are_locked_preserve_unrelated_fields_and_mark_new_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old, new = root / "old", root / "new"
            old.mkdir()
            new.mkdir()
            for i in range(3):
                (new / ("App%d.ipa" % i)).write_bytes(b"file")
            config = self.config(old)
            config["device"] = {"serial": "unchanged"}
            with (
                patch.object(controls, "load_config", return_value=config),
                patch.object(controls, "save_config") as save,
            ):
                value = controls.change_settings(str(new), 20, False)
                saved = save.call_args.args[0]
                self.assertEqual(saved["device"], config["device"])
                self.assertEqual(
                    saved["automation"]["pending_ipa_installs"],
                    {"1": "App0", "2": "App1", "3": "App2"},
                )
                self.assertEqual(value["timeout_minutes"], 20)
                self.assertFalse(value["notifications"])
                self.assertNotIn("pending_ipa_installs", config["automation"])

    def test_invalid_settings_or_active_run_cannot_save(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(controls, "load_config", return_value=self.config(directory)),
            patch.object(controls, "save_config") as save,
        ):
            for kwargs in (
                {"folder": directory},
                {"timeout": 0},
                {"timeout": True},
                {"notifications": "yes"},
            ):
                with self.assertRaises(ValueError):
                    controls.change_settings(**kwargs)
            with patch.object(controls.RunLock, "acquire", return_value=False):
                with self.assertRaisesRegex(ValueError, "active"):
                    controls.change_settings(timeout=30)
            save.assert_not_called()

    def test_registry_ignores_stale_identity_and_finishes(self):
        from iphone_detector.storage import write_json

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / "automation-runs" / "gui-live"
            stale = root / "automation-runs" / "discord-stale"
            live.mkdir(parents=True)
            stale.mkdir()
            registry.register(live, "wifi-run", [1])
            value = registry.active_jobs(root)[0]
            self.assertEqual(value["accounts"], [2])
            write_json(stale / "worker.json", dict(value, job=None, process_created=0))
            self.assertEqual(registry.stop_active(root), 1)
            self.assertTrue((live / "stop").exists())
            self.assertFalse((stale / "stop").exists())
            registry.finish(live)
            self.assertEqual(registry.active_jobs(root), [])

    def test_commands_and_panel_dispatch_only_when_authorized(self):
        import discord
        from discord import app_commands
        from iphone_detector.discord_management import register

        async def run():
            client = discord.Client(intents=discord.Intents.none())
            tree = app_commands.CommandTree(client)
            guild = discord.Object(id=2)
            allow = AsyncMock(return_value=True)
            interaction = Mock()
            interaction.response.send_message = AsyncMock()
            interaction.response.defer = AsyncMock()
            interaction.followup.send = AsyncMock()
            with (
                tempfile.TemporaryDirectory() as directory,
                patch("iphone_detector.config.load_config", return_value=self.config(directory)),
                patch.object(controls, "load_config", return_value=self.config(directory)),
                patch.object(
                    controls,
                    "change_settings",
                    return_value={
                        "folder": directory,
                        "timeout_minutes": 30,
                        "notifications": False,
                    },
                ) as update,
            ):
                register(tree, guild, allow, discord)
                await tree.get_command("settings", guild=guild).callback(interaction)
                update.assert_not_called()
                await tree.get_command("settings", guild=guild).callback(
                    interaction, timeout_minutes=30
                )
                update.assert_called_once_with(None, 30, None)
                await tree.get_command("files", guild=guild).callback(interaction)
                self.assertIn("0 files", interaction.followup.send.await_args.args[0])
                await tree.get_command("panel", guild=guild).callback(interaction)
                panel = interaction.response.send_message.await_args.kwargs["view"]
                help_button = next(button for button in panel.children if button.label == "Help")
                await help_button.callback(interaction)
                self.assertIn("/inbox", interaction.response.send_message.await_args.args[0])
                allow.return_value = False
                self.assertFalse(await panel.interaction_check(interaction))
                update.reset_mock()
                await tree.get_command("settings", guild=guild).callback(
                    interaction, timeout_minutes=20
                )
                update.assert_not_called()
                panel.stop()
            await client.close()

        asyncio.run(run())

    def test_schedule_disable_uses_existing_scheduler_and_status_is_read_only(self):
        import discord
        from discord import app_commands
        from iphone_detector.discord_management import register

        async def run():
            client = discord.Client(intents=discord.Intents.none())
            tree = app_commands.CommandTree(client)
            guild = discord.Object(id=2)
            register(tree, guild, AsyncMock(return_value=True), discord)
            interaction = Mock()
            interaction.response.defer = AsyncMock()
            interaction.followup.send = AsyncMock()
            with (
                patch("iphone_detector.auto_refresh.configure") as configure,
                patch("iphone_detector.auto_refresh.describe", return_value="Scheduled"),
            ):
                command = tree.get_command("autorefresh", guild=guild)
                await command.callback(interaction)
                configure.assert_not_called()
                await command.callback(interaction, "disable")
                configure.assert_called_once_with(False)
                self.assertIn("/stop", interaction.followup.send.await_args.args[0])
            await client.close()

        asyncio.run(run())
