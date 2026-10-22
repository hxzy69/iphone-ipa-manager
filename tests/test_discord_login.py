import asyncio
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch


class DiscordLoginTests(unittest.TestCase):
    def test_rejected_modal_releases_reservation_and_returns_private_error(self):
        import discord
        from discord import app_commands
        from iphone_detector import discord_login

        async def run():
            client = discord.Client(intents=discord.Intents.none())
            tree = app_commands.CommandTree(client)
            guild = discord.Object(id=2)
            interaction = Mock()
            interaction.response.send_message = AsyncMock()
            interaction.response.send_modal = AsyncMock(
                side_effect=RuntimeError("rejected payload")
            )
            interaction.response.is_done.return_value = False
            session = Mock(close=AsyncMock())
            with patch.object(discord_login, "LoginSession", return_value=session):
                manager = discord_login.register(
                    tree, guild, AsyncMock(return_value=True), lambda item: True, discord
                )
                await tree.get_command("login", guild=guild).callback(interaction)
                view = interaction.response.send_message.await_args.kwargs["view"]
                await view.children[0].callback(interaction)
                self.assertIsNone(manager.session)
                session.close.assert_awaited_once()
                self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
                self.assertIn("fresh /login", interaction.response.send_message.await_args.args[0])
                view.stop()
                await manager.close()
            await client.close()

        asyncio.run(run())

    def test_private_dialogue_handles_code_then_offers_enrollment_without_installing(self):
        import discord
        from discord import app_commands
        from iphone_detector import discord_login

        async def run():
            client = discord.Client(intents=discord.Intents.none())
            tree = app_commands.CommandTree(client)
            guild = discord.Object(id=2)
            permitted = [True]
            session = Mock(
                email="new@example.com", challenge=1, waiting=True, started=False, cancelled=False
            )
            session.start = AsyncMock()
            session.close = AsyncMock()
            answered = asyncio.Event()

            async def respond(*args):
                answered.set()

            session.respond = AsyncMock(side_effect=respond)

            async def events():
                yield {"stage": "challenge", "numbers": [{"id": 7, "last_digits": "42"}]}
                await answered.wait()
                yield {"stage": "developer_session"}
                yield {"stage": "complete", "email": session.email}

            session.events = events
            interaction = Mock()
            interaction.response.send_message = AsyncMock()
            interaction.response.send_modal = AsyncMock()
            interaction.response.defer = AsyncMock()
            interaction.followup.send = AsyncMock()
            with (
                patch.object(discord_login, "LoginSession", return_value=session),
                patch.object(discord_login, "save_iloader_account") as save,
                patch.object(discord_login, "append_account") as append,
                patch(
                    "iphone_detector.discord_inbox.managed_files", return_value=[Path("Ready.ipa")]
                ),
                patch("iphone_detector.config.load_config", return_value={}),
            ):
                manager = discord_login.register(
                    tree, guild, AsyncMock(return_value=True), lambda item: permitted[0], discord
                )
                await tree.get_command("login", guild=guild).callback(interaction)
                initial = interaction.response.send_message.await_args
                self.assertTrue(initial.kwargs["ephemeral"])
                self.assertIn("not end-to-end encrypted", initial.args[0])
                view = initial.kwargs["view"]
                permitted[0] = False
                self.assertFalse(await view.interaction_check(interaction))
                session.start.assert_not_awaited()
                permitted[0] = True
                await view.children[0].callback(interaction)
                modal = interaction.response.send_modal.await_args.args[0]
                self.assertLessEqual(len(modal.title), 45)
                self.assertLessEqual(len(modal.children), 5)
                for field in modal.children:
                    payload = field.to_component_dict()
                    self.assertLessEqual(payload.get("max_length", 4000), 4000)
                    self.assertLessEqual(len(payload["label"]), 45)
                modal.email._value = "new@example.com"
                modal.password._value = "test-only-secret"
                await modal.on_submit(interaction)
                await asyncio.sleep(0)
                challenge = next(
                    call.kwargs["view"]
                    for call in interaction.followup.send.await_args_list
                    if "view" in call.kwargs
                )
                enter = next(
                    child
                    for child in challenge.children
                    if isinstance(child, discord.ui.Button) and child.label == "Enter code"
                )
                await enter.callback(interaction)
                code = interaction.response.send_modal.await_args.args[0]
                code.code._value = "123456"
                await code.on_submit(interaction)
                await manager.task
                session.respond.assert_awaited_once_with(1, {"action": "code", "code": "123456"})
                save.assert_called_once_with("new@example.com")
                append.assert_not_called()
                self.assertIsNone(manager.session)
                self.assertTrue(
                    any(
                        "Choose an IPA" in str(call.args)
                        for call in interaction.followup.send.await_args_list
                    )
                )
                for call in interaction.followup.send.await_args_list:
                    self.assertTrue(call.kwargs["ephemeral"])
                    self.assertNotIn("test-only-secret", str(call.args))
                await manager.close()
                view.stop()
                challenge.stop()
                modal.stop()
                code.stop()
            await client.close()

        asyncio.run(run())
