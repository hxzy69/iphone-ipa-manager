import unittest
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import discord
from discord import app_commands
from iphone_detector.discord_review import request


def interaction(owner=1):
    item = Mock(user=Mock(id=owner), guild_id=2, channel_id=3)
    item.response.defer = AsyncMock()
    item.response.send_message = AsyncMock()
    item.response.edit_message = AsyncMock()
    item.followup.send = AsyncMock()
    return item


def review(count=1):
    return {
        "mode": "install",
        "token": "sample",
        "rows": [
            {
                "account": i + 1,
                "email": "long-account" * 10 + "@example.com",
                "app": "*" * 200,
                "version": "1.2.3.4.5",
                "ipa": "_" * 200 + ".ipa",
                "operation": "Full installation (changed assignment)",
            }
            for i in range(count)
        ],
    }


class DiscordReviewTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_after_confirmation_does_not_claim_run_was_cancelled(self):
        item, controller, report, view, plan = await self.open()
        started, release = asyncio.Event(), asyncio.Event()

        async def start(**kwargs):
            started.set()
            await release.wait()

        controller.start_async.side_effect = start
        with patch("iphone_detector.discord_review.verify", return_value=plan):
            confirming = asyncio.create_task(view.children[0].callback(item))
            try:
                await asyncio.wait_for(started.wait(), 2)
                cancel = interaction()
                await view.children[1].callback(cancel)
                cancel.response.edit_message.assert_not_awaited()
                self.assertIn("already used", cancel.response.send_message.await_args.args[0])
            finally:
                release.set()
                await confirming
                await controller.task

    async def open(self, *, queued=False, count=1):
        item = interaction()
        controller = Mock(start_async=AsyncMock())
        report = AsyncMock()
        allow = AsyncMock(return_value=True)
        plan = review(count)
        with (
            patch("iphone_detector.discord_review.plan", return_value=plan),
            patch("iphone_detector.discord_review.load_config", return_value={}),
        ):
            await request(item, controller, report, allow, discord, queued=queued)
        view = item.followup.send.await_args.kwargs["view"]
        self.addCleanup(view.stop)
        return item, controller, report, view, plan

    async def test_no_start_until_confirm_and_duplicate_click_does_not_start_twice(self):
        item, controller, report, view, plan = await self.open()
        controller.start_async.assert_not_awaited()
        with patch("iphone_detector.discord_review.verify", return_value=plan):
            await view.children[0].callback(item)
            await controller.task
            await view.children[0].callback(item)
        controller.start_async.assert_awaited_once_with(
            refresh=False, accounts=None, expected_assignment=None, expected_review=plan
        )
        report.assert_awaited_once()

    async def test_changed_plan_cannot_start(self):
        item, controller, report, view, plan = await self.open()
        with patch(
            "iphone_detector.discord_review.verify", side_effect=ValueError("Files changed")
        ):
            await view.children[0].callback(item)
        controller.start_async.assert_not_awaited()
        self.assertIn("Files changed", item.followup.send.await_args.args[0])

    async def test_other_user_and_cancel_cannot_start_job(self):
        item, controller, report, view, plan = await self.open()
        self.assertFalse(await view.interaction_check(interaction(owner=99)))
        await view.children[1].callback(item)
        await view.children[0].callback(item)
        controller.start_async.assert_not_awaited()
        item.response.edit_message.assert_awaited_once()

    async def test_twenty_five_account_embed_stays_under_discord_limit(self):
        item, controller, report, view, plan = await self.open(count=25)
        embed = item.followup.send.await_args.kwargs["embed"]
        self.assertEqual(len(embed.fields), 25)
        self.assertLessEqual(len(embed), 6000)

    async def test_queue_confirmation_binds_review_without_starting_controller(self):
        item, controller, report, view, plan = await self.open(queued=True)
        with (
            patch("iphone_detector.discord_review.verify"),
            patch(
                "iphone_detector.job_queue.enqueue",
                return_value={"id": "sample", "startup_registered": True},
            ) as enqueue,
        ):
            await view.children[0].callback(item)
        enqueue.assert_called_once_with("install", None, expected_review=plan)
        controller.start_async.assert_not_awaited()

    async def test_new_commands_and_autocomplete_do_not_leak_to_unauthorized_users(self):
        from iphone_detector.discord_management import register

        client = discord.Client(intents=discord.Intents.none())
        self.addAsyncCleanup(client.close)
        tree = app_commands.CommandTree(client)
        guild = discord.Object(id=2)
        register(tree, guild, AsyncMock(return_value=False), discord, lambda item: False)
        with patch(
            "iphone_detector.discord_library.load_config",
            side_effect=AssertionError("Unauthorized settings read"),
        ):
            for name in ("ipa_updates", "assignments", "progress", "setup"):
                await tree.get_command(name, guild=guild).callback(interaction(owner=99))
            for name, parameter in (("ipa_updates", "account"), ("assignments", "entry")):
                self.assertEqual(
                    await tree.get_command(name, guild=guild)
                    ._params[parameter]
                    .autocomplete(interaction(owner=99), ""),
                    [],
                )

    async def test_update_button_replaces_the_expected_single_account_assignment(self):
        from iphone_detector.discord_management import register
        from iphone_detector.ipa_library import LibraryEntry

        client = discord.Client(intents=discord.Intents.none())
        self.addAsyncCleanup(client.close)
        tree = app_commands.CommandTree(client)
        guild = discord.Object(id=2)
        register(tree, guild, AsyncMock(return_value=True), discord, lambda item: True)
        value = {"automation": {"accounts": [{"email": "one@example.com", "ipa": "First"}]}}
        old = LibraryEntry(Path("First.ipa"), (1, 1), "App", "1", "org.example.app", (1,))
        new = LibraryEntry(Path("Second.ipa"), (2, 2), "App", "2", "org.example.app")
        item = interaction()
        with (
            patch("iphone_detector.discord_library.load_config", return_value=value),
            patch("iphone_detector.ipa_library.scan", return_value=[old, new]),
        ):
            await tree.get_command("ipa_updates", guild=guild).callback(item, account=1)
        view = item.followup.send.await_args.kwargs["view"]
        self.addCleanup(view.stop)
        with patch("iphone_detector.discord_inbox.assign_managed") as save:
            await view.children[0].callback(item)
            await view.children[0].callback(item)
            save.assert_called_once_with(
                new.path,
                new.signature,
                0,
                expected_email="one@example.com",
                expected_ipa="First",
                expected_directory=".",
            )
