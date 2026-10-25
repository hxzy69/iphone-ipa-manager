from fixtures import configured
import json
import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch, AsyncMock

from iphone_detector import discord_bot as bot
from iphone_detector.run_lock import RunLock


class DiscordTests(unittest.TestCase):
    def test_unreadable_remote_settings_write_a_failure_receipt_before_any_launch(self):
        import ipa_manager

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.config.load_config", return_value=configured()),
            patch("iphone_detector.logger.setup_logging"),
            patch("iphone_detector.remote_tunnel.enabled", side_effect=ValueError("bad settings")),
            patch("iphone_detector.connection_router.run_automatic") as run,
        ):
            job = Path(directory)
            self.assertEqual(ipa_manager.worker("discord-run", job), 1)
            self.assertEqual(
                json.loads((job / "result.json").read_text())["error_code"],
                "REMOTE_SETTINGS_UNREADABLE",
            )
            run.assert_not_called()

    def test_optional_account_metadata_failure_preserves_the_completed_operation_receipt(self):
        import ipa_manager

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.config.load_config", return_value=configured()),
            patch("iphone_detector.logger.setup_logging"),
            patch("iphone_detector.background.validate_selection"),
            patch.object(RunLock, "acquire", return_value=True),
            patch.object(RunLock, "close"),
            patch("iphone_detector.worker_registry.register"),
            patch("iphone_detector.worker_registry.finish"),
            patch(
                "iphone_detector.connection_router.run_refresh",
                return_value={"ok": True, "completed_accounts": 3},
            ),
            patch(
                "iphone_detector.operations.collect_native",
                side_effect=OSError("metadata unavailable"),
            ),
            patch("iphone_detector.ipa_assignments.acknowledge_installs"),
            patch("iphone_detector.auto_refresh.record_success"),
        ):
            job = Path(directory)
            self.assertEqual(ipa_manager.worker("discord-refresh", job), 0)
            result = json.loads((job / "result.json").read_text())
            self.assertTrue(result["ok"])
            self.assertEqual(result["completed_accounts"], 3)

    def test_slow_preflight_keeps_gateway_responsive_and_can_be_cancelled(self):
        import threading

        async def exercise():
            entered, release = threading.Event(), threading.Event()
            controller = bot.Controller()

            def preflight(*args, **kwargs):
                entered.set()
                if not release.wait(5):
                    raise AssertionError("Preflight test timed out")
                return True

            with (
                tempfile.TemporaryDirectory() as directory,
                patch.object(bot, "DATA_ROOT", Path(directory)),
                patch("iphone_detector.connection_router.route_ready", side_effect=preflight),
                patch("iphone_detector.config.load_config", return_value=configured()),
                patch("iphone_detector.background.make_job", return_value=configured()),
                patch.object(bot.subprocess, "Popen") as spawn,
            ):
                task = asyncio.create_task(controller.start_async(refresh=True))
                try:
                    for _ in range(200):
                        if entered.is_set():
                            break
                        await asyncio.sleep(0.005)
                    self.assertTrue(entered.is_set())
                    with self.assertRaisesRegex(RuntimeError, "already starting"):
                        await controller.start_async(refresh=True)
                    self.assertIn("Cancelling", controller.stop())
                finally:
                    release.set()
                with self.assertRaisesRegex(RuntimeError, "cancelled before launch"):
                    await task
                self.assertFalse(controller.starting)
                spawn.assert_not_called()

        asyncio.run(exercise())

    def test_cancelled_start_waits_for_thread_and_does_not_launch_later(self):
        import threading

        async def exercise():
            release = threading.Event()
            controller = bot.Controller()

            def start(**kwargs):
                release.wait(5)
                self.assertTrue(controller._cancel_start.is_set())

            with patch.object(controller, "start", side_effect=start):
                task = asyncio.create_task(controller.start_async())
                await asyncio.sleep(0.02)
                task.cancel()
                await asyncio.sleep(0.02)
                try:
                    self.assertTrue(controller.starting)
                    self.assertFalse(task.done())
                finally:
                    release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertFalse(controller.starting)

        asyncio.run(exercise())

    def test_local_install_does_not_require_legacy_service_or_warm_remote_tunnel(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bot, "DATA_ROOT", Path(directory)),
            patch("iphone_detector.connection_router.route_ready", return_value=True) as ready,
            patch("iphone_detector.bridge_service.installed", return_value=False),
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch("iphone_detector.remote_tunnel.state", return_value={"state": "waiting"}),
            patch("iphone_detector.remote_tunnel.ensure_started"),
            patch("iphone_detector.config.load_config", return_value=configured()),
            patch("iphone_detector.background.make_job", return_value=configured()),
            patch.object(bot.subprocess, "Popen") as spawn,
        ):
            bot.Controller().start()
            ready.assert_called_once_with()
            spawn.assert_called_once()
            self.assertIn("discord-run", spawn.call_args.args[0])

    def test_unavailable_local_and_remote_routes_do_not_launch_an_install(self):
        with (
            patch("iphone_detector.connection_router.route_ready", return_value=False),
            patch("iphone_detector.bridge_service.installed", return_value=False),
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch("iphone_detector.remote_tunnel.state", return_value={"state": "waiting"}),
            patch("iphone_detector.remote_tunnel.ensure_started"),
            patch.object(bot.subprocess, "Popen") as spawn,
        ):
            with self.assertRaisesRegex(RuntimeError, "waiting"):
                bot.Controller().start()
            spawn.assert_not_called()

    def test_verified_local_refresh_is_not_blocked_by_an_offline_enabled_tunnel(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bot, "DATA_ROOT", Path(directory)),
            patch("iphone_detector.connection_router.route_ready", return_value=True),
            patch("iphone_detector.bridge_service.installed", return_value=False),
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch("iphone_detector.remote_tunnel.state", return_value={"state": "waiting"}),
            patch("iphone_detector.remote_tunnel.ensure_started") as remote,
            patch("iphone_detector.config.load_config", return_value=configured()),
            patch("iphone_detector.background.make_job", return_value=configured()),
            patch.object(bot.subprocess, "Popen") as spawn,
        ):
            controller = bot.Controller()
            controller.start(refresh=True)
            spawn.assert_called_once()
            remote.assert_not_called()
            self.assertIn("discord-refresh", spawn.call_args.args[0])

    def test_worker_records_corrupt_settings_without_starting_device_work(self):
        import ipa_manager
        from iphone_detector.config import ConfigurationError

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.logger.setup_logging"),
            patch(
                "iphone_detector.config.load_config",
                side_effect=ConfigurationError("Repair settings"),
            ),
            patch("iphone_detector.connection_router.run_automatic") as run,
        ):
            job = Path(directory)
            self.assertEqual(ipa_manager.worker("discord-run", job), 1)
            run.assert_not_called()
            self.assertEqual(
                json.loads((job / "result.json").read_text())["error_code"], "CONFIG_UNREADABLE"
            )

    def test_changed_selection_stops_worker_before_device_work_and_releases_lock(self):
        import ipa_manager

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.logger.setup_logging"),
            patch("iphone_detector.config.load_config", return_value=configured()),
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch.object(RunLock, "acquire", return_value=True),
            patch.object(RunLock, "close") as close,
            patch(
                "iphone_detector.background.validate_selection",
                side_effect=ValueError("IPA changed"),
            ),
            patch("iphone_detector.connection_router.run_automatic") as run,
        ):
            job = Path(directory)
            self.assertEqual(ipa_manager.worker("discord-run", job, accounts=[1]), 1)
            run.assert_not_called()
            close.assert_called_once()
            self.assertIn("IPA changed", json.loads((job / "result.json").read_text())["error"])

    def test_worker_exit_without_verified_receipt_never_reports_success(self):
        import discord

        settings = {"user_id": 1, "guild_id": 2, "channel_id": 3}
        for receipt in ({}, [], {"ok": True, "completed_accounts": 1}):
            with self.subTest(receipt=receipt), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "result.json").write_text(json.dumps(receipt))
                controllers = []

                def start(controller, **kwargs):
                    controllers.append(controller)
                    controller.job = root
                    controller.process = Mock(poll=Mock(return_value=0), returncode=0)

                async def check(client):
                    await client.setup_hook()
                    commands = {
                        c.name: c for c in client.tree.get_commands(guild=discord.Object(id=2))
                    }
                    interaction = Mock(user=Mock(id=1), guild_id=2, channel_id=3)
                    interaction.response.defer = AsyncMock()
                    interaction.followup.send = AsyncMock()
                    interaction.channel.send = AsyncMock()
                    await commands["sideload"].callback(interaction)
                    await controllers[0].task
                    self.assertIn("without a verified", interaction.channel.send.await_args.args[0])
                    await client.close()

                with (
                    patch.object(bot, "DATA_ROOT", root),
                    patch.object(bot.Controller, "start", start),
                    patch("iphone_detector.logger.setup_logging"),
                    patch("iphone_detector.remote_tunnel.ensure_started"),
                    patch.object(RunLock, "acquire", return_value=True),
                    patch.object(bot, "load_settings", return_value=(settings, "synthetic")),
                    patch("discord.app_commands.CommandTree.sync", new_callable=AsyncMock),
                    patch.object(
                        discord.Client, "run", lambda client, *a, **k: asyncio.run(check(client))
                    ),
                ):
                    bot.run()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        remote_settings = patch(
            "iphone_detector.remote_tunnel.SETTINGS", Path(directory.name) / "remote.json"
        )
        remote_settings.start()
        self.addCleanup(remote_settings.stop)
        remote_mode = patch("iphone_detector.remote_tunnel.enabled", return_value=False)
        remote_mode.start()
        self.addCleanup(remote_mode.stop)

    def test_authorization_requires_all_three_ids_and_rejects_dms(self):
        settings = {"user_id": 1, "guild_id": 2, "channel_id": 3}
        self.assertTrue(bot.authorized(settings, 1, 2, 3))
        for ids in ((9, 2, 3), (1, 9, 3), (1, 2, 9), (1, None, 3)):
            self.assertFalse(bot.authorized(settings, *ids))

    def test_invalid_ids_rejected(self):
        for value in ("", "-1", "abc", str(2**64), "0"):
            with self.assertRaises(ValueError):
                bot.validate_settings({"user_id": value, "guild_id": 2, "channel_id": 3})

    def test_token_is_encrypted_and_roundtrips_without_plaintext_on_disk(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bot, "SETTINGS", Path(directory) / "discord.json"),
        ):
            token = "synthetic-test-secret-not-a-real-token"
            bot.save_settings({"user_id": 1, "guild_id": 2, "channel_id": 3}, token)
            self.assertNotIn(token, bot.SETTINGS.read_text())
            self.assertEqual(bot.load_settings()[1], token)
            bot.save_settings({"user_id": 4, "guild_id": 2, "channel_id": 3}, "")
            self.assertEqual(bot.load_settings()[0]["user_id"], 4)
            self.assertEqual(bot.load_settings()[1], token)

    def test_remote_start_refuses_missing_service_without_launching_worker(self):
        with (
            patch("iphone_detector.bridge_service.installed", return_value=False),
            patch.object(bot.subprocess, "Popen") as launch,
        ):
            with self.assertRaisesRegex(RuntimeError, "Connect the paired iPhone"):
                bot.Controller().start()
            launch.assert_not_called()

    def test_existing_run_is_not_restarted(self):
        controller = bot.Controller()
        controller.task = Mock()
        controller.task.done.return_value = False
        with self.assertRaisesRegex(RuntimeError, "already running"):
            controller.start()

    def test_live_worker_prevents_restart_even_if_report_task_failed(self):
        controller = bot.Controller()
        controller.process = Mock()
        controller.process.poll.return_value = None
        controller.task = Mock()
        controller.task.done.return_value = True
        with self.assertRaisesRegex(RuntimeError, "already running"):
            controller.start()

    def test_refresh_requires_ready_remote_tunnel_before_spawning_worker(self):
        controller = bot.Controller()
        with (
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch("iphone_detector.remote_tunnel.state", return_value={"state": "waiting"}),
            patch.object(bot.subprocess, "Popen") as spawn,
        ):
            with self.assertRaisesRegex(RuntimeError, "ready remote tunnel"):
                controller.start(refresh=True)
            spawn.assert_not_called()

    def test_refresh_worker_uses_shared_refresh_service_without_desktop(self):
        import ipa_manager

        config = configured({"automation": {"notify_on_finish": False}})
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.logger.setup_logging"),
            patch.object(RunLock, "acquire", return_value=True),
            patch("iphone_detector.config.load_config", return_value=config),
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch(
                "iphone_detector.connection_router.run_refresh",
                return_value={"ok": True, "completed_accounts": 3, "refreshed_accounts": 3},
            ) as submit,
            patch("iphone_detector.connection_router.run_automatic") as automatic,
        ):
            job = Path(directory)
            self.assertEqual(ipa_manager.worker("discord-refresh", job), 0)
            submit.assert_called_once()
            automatic.assert_not_called()

    def test_progress_ignores_incomplete_json_and_never_exposes_email(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = bot.Controller()
            controller.expected = 3
            controller.job = Path(directory)
            (controller.job / "backend-events.jsonl").write_text(
                json.dumps({"stage": "install", "account": 2, "email": "private"}) + '\n{"sta'
            )
            self.assertEqual(controller.progress(), "Installing — account 2 of 3.")

    def test_single_account_progress_omits_the_three_account_total(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = bot.Controller()
            controller.expected = 3
            controller.job = Path(directory)
            controller.accounts = [1]
            (controller.job / "backend-events.jsonl").write_text(
                json.dumps({"stage": "install", "account": 2}) + "\n"
            )
            self.assertEqual(controller.progress(), "Installing — account 2.")

    def test_start_with_account_spawns_worker_with_account_flag(self):
        controller = bot.Controller()
        with (
            patch("iphone_detector.bridge_service.installed", return_value=True),
            patch(
                "iphone_detector.config.load_config",
                return_value=configured({"device": {"serial": "phone"}}),
            ),
            patch("iphone_detector.background.make_job", return_value=configured()) as make,
            patch.object(bot.subprocess, "Popen") as launch,
        ):
            controller.start(accounts=[1])
            args = launch.call_args.args[0]
            self.assertEqual(args[args.index("--account") + 1], "2")
            self.assertEqual(controller.accounts, [1])
            self.assertIn("discord-run", args)
        self.assertEqual(make.call_args.kwargs.get("accounts"), [1])

    def test_refresh_start_keeps_worker_cli_compatible(self):
        controller = bot.Controller()
        with (
            patch("iphone_detector.bridge_service.installed", return_value=True),
            patch(
                "iphone_detector.config.load_config",
                return_value=configured({"device": {"serial": "phone"}}),
            ),
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch("iphone_detector.remote_tunnel.state", return_value={"state": "ready"}),
            patch("iphone_detector.remote_tunnel.ensure_started"),
            patch("iphone_detector.background.make_job", return_value=configured()),
            patch.object(bot.subprocess, "Popen") as launch,
        ):
            controller.start(refresh=True)
            args = launch.call_args.args[0]
            self.assertIn("discord-refresh", args)
            self.assertNotIn("--account", args)
            self.assertIsNone(controller.accounts)

    def test_refresh_rejects_partial_selection_before_spawning(self):
        with patch.object(bot.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(ValueError, "all configured"):
                bot.Controller().start(refresh=True, accounts=[1])
            launch.assert_not_called()

    def test_start_rejects_account_selection_without_files_before_spawning(self):
        controller = bot.Controller()
        with (
            patch("iphone_detector.bridge_service.installed", return_value=True),
            patch(
                "iphone_detector.config.load_config",
                return_value=configured({"device": {"serial": "phone"}}),
            ),
            patch(
                "iphone_detector.background.make_job", side_effect=ValueError("IPA file not found")
            ),
            patch.object(bot.subprocess, "Popen") as launch,
        ):
            with self.assertRaisesRegex(ValueError, "IPA file not found"):
                controller.start(accounts=[1])
            launch.assert_not_called()
            self.assertIsNone(controller.accounts)

    def test_stop_only_signals_its_own_job(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = bot.Controller()
            controller.expected = 3
            controller.job = Path(directory)
            controller.process = Mock()
            controller.process.poll.return_value = None
            controller.stop()
            self.assertTrue((controller.job / "stop").exists())
            controller.process.terminate.assert_not_called()

    def test_progress_distinguishes_active_transfer_from_signing_ahead(self):
        with tempfile.TemporaryDirectory() as directory:
            controller = bot.Controller()
            controller.expected = 3
            controller.job = Path(directory)
            (controller.job / "backend-events.jsonl").write_text(
                json.dumps({"stage": "sign", "account": 2}) + "\n"
            )
            (controller.job / "remote-transfer.json").write_text(
                json.dumps(
                    {"stage": "upload", "account": 1, "uploaded_bytes": 50, "archive_bytes": 100}
                )
            )
            description = controller.progress()
            self.assertIn("Uploading — account 1 of 3.", description)
            self.assertIn("50% uploaded.", description)
            self.assertIn("Preparing account 2.", description)

    def test_gui_and_bot_workers_share_exclusion(self):
        first, second = RunLock(), RunLock()
        first.name = second.name = "Local\\iPhoneIPAManagerTestLock"
        try:
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
        finally:
            first.close()
            second.close()
        self.assertTrue(second.acquire())
        second.close()

    def test_slash_commands_register_and_reject_unauthorized_user_offline(self):
        import discord

        settings = {"user_id": 1, "guild_id": 2, "channel_id": 3}

        async def check(client):
            await client.setup_hook()
            commands = client.tree.get_commands(guild=discord.Object(id=2))
            self.assertEqual(
                {c.name for c in commands},
                {
                    "sideload",
                    "refresh",
                    "status",
                    "updates",
                    "diagnostics",
                    "dashboard",
                    "queue",
                    "retry",
                    "backup",
                    "update",
                    "maintenance",
                    "stop",
                    "inbox",
                    "assign",
                    "accounts",
                    "history",
                    "help",
                    "files",
                    "check",
                    "settings",
                    "autorefresh",
                    "panel",
                    "login",
                    "login_cancel",
                },
            )
            for item in commands:
                interaction = Mock(user=Mock(id=999), guild_id=2, channel_id=3)
                interaction.response.send_message = AsyncMock()
                if item.name == "inbox":
                    await item.callback(interaction, account=1, file="example")
                else:
                    await item.callback(interaction)
                interaction.response.send_message.assert_awaited_once()
            from iphone_detector.updates import Release, REPOSITORY, UpdateError

            updates_command = next(item for item in commands if item.name == "updates")
            interaction = Mock(user=Mock(id=1), guild_id=2, channel_id=3)
            interaction.response.defer = AsyncMock()
            interaction.followup.send = AsyncMock()
            with patch(
                "iphone_detector.updates.check_updates",
                return_value=Release("99.0.0", REPOSITORY + "/releases/tag/v99.0.0"),
            ):
                await updates_command.callback(interaction)
            interaction.response.defer.assert_awaited_once_with(ephemeral=True)
            self.assertIn("99.0.0", interaction.followup.send.call_args.args[0])
            self.assertTrue(interaction.followup.send.call_args.kwargs["ephemeral"])
            interaction.followup.send.reset_mock()
            with patch(
                "iphone_detector.updates.check_updates", side_effect=UpdateError("Try again later.")
            ):
                await updates_command.callback(interaction)
            self.assertIn("Try again later", interaction.followup.send.call_args.args[0])
            await client.close()

        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bot, "DATA_ROOT", Path(directory)),
            patch("iphone_detector.logger.setup_logging"),
            patch.object(RunLock, "acquire", return_value=True),
            patch.object(bot, "load_settings", return_value=(settings, "synthetic")),
            patch("discord.app_commands.CommandTree.sync", new_callable=AsyncMock),
            patch.object(discord.Client, "run", lambda client, *a, **k: asyncio.run(check(client))),
        ):
            bot.run()

    def test_accounts_and_default_inbox_show_the_same_saved_mapping(self):
        import discord

        settings = {"user_id": 1, "guild_id": 2, "channel_id": 3}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Chosen.ipa").write_bytes(b"file-list-only")
            config = configured(
                {
                    "automation": {
                        "ipa_directory": directory,
                        "pending_ipa_installs": {"2": "Chosen"},
                        "accounts": [
                            {"email": "one@example.com", "ipa": "One"},
                            {"email": "two@example.com", "ipa": "Chosen"},
                            {"email": "three@example.com", "ipa": "Three"},
                        ],
                    }
                }
            )

            async def check(client):
                await client.setup_hook()
                commands = {
                    item.name: item for item in client.tree.get_commands(guild=discord.Object(id=2))
                }
                interaction = Mock(user=Mock(id=1), guild_id=2, channel_id=3)
                interaction.response.send_message = AsyncMock()
                interaction.response.defer = AsyncMock()
                interaction.followup.send = AsyncMock()
                await commands["accounts"].callback(interaction)
                embed = interaction.response.send_message.await_args.kwargs["embed"]
                self.assertIn("two@example.com", embed.fields[1].value)
                self.assertIn("Chosen.ipa", embed.fields[1].value)
                self.assertIn("pending", embed.fields[1].value)
                await commands["inbox"].callback(interaction, account=2, file="Chosen.ipa")
                view = interaction.followup.send.await_args.kwargs["view"]
                self.assertEqual(view.children[0].options[0].label, "Chosen.ipa")
                self.assertIn("two@example.com", view.children[1].options[1].label)
                self.assertEqual(view.account, 1)
                self.assertFalse(view.children[-1].disabled)
                params = commands["inbox"]._params
                self.assertEqual(list(params), ["account", "file"])
                self.assertTrue(params["account"].required)
                self.assertTrue(params["file"].required)
                self.assertIn(
                    "two@example.com",
                    (await params["account"].autocomplete(interaction, ""))[1].name,
                )
                view.stop()
                interaction.namespace = Mock(source="managed")
                account_suggestions = (
                    await commands["sideload"]._params["account"].autocomplete(interaction, "")
                )
                self.assertEqual([choice.value for choice in account_suggestions], [1, 2, 3])
                self.assertIn("two@example.com", account_suggestions[1].name)
                autocomplete = commands["inbox"]._params["file"].autocomplete
                choices = await autocomplete(interaction, "cho")
                self.assertEqual([item.name for item in choices], ["Chosen.ipa"])
                await commands["inbox"].callback(interaction, account=2, file=choices[0].value)
                selected_view = interaction.followup.send.await_args.kwargs["view"]
                self.assertEqual(selected_view.source.name, "Chosen.ipa")
                self.assertIn("Chosen.ipa", selected_view.summary())
                selected_view.stop()
                interaction.user.id = 999
                self.assertEqual(await autocomplete(interaction, ""), [])
                await client.close()

            with (
                patch.object(bot, "DATA_ROOT", root),
                patch("iphone_detector.logger.setup_logging"),
                patch("iphone_detector.config.load_config", return_value=config),
                patch("iphone_detector.remote_tunnel.ensure_started"),
                patch.object(RunLock, "acquire", return_value=True),
                patch.object(bot, "load_settings", return_value=(settings, "synthetic")),
                patch("discord.app_commands.CommandTree.sync", new_callable=AsyncMock),
                patch.object(
                    discord.Client, "run", lambda client, *a, **k: asyncio.run(check(client))
                ),
            ):
                bot.run()

    def test_discord_worker_forces_background_even_if_visual_mode_saved(self):
        import ipa_manager
        from contextlib import nullcontext

        config = configured(
            {
                "device": {"serial": "phone"},
                "automation": {"background_mode": False, "notify_on_finish": False},
            }
        )
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.logger.setup_logging"),
            patch.object(RunLock, "acquire", return_value=True),
            patch("iphone_detector.config.load_config", return_value=config),
            patch("iphone_detector.bridge_service.installed", return_value=True),
            patch("iphone_detector.wifi.WifiBridge", return_value=nullcontext(Mock())),
            patch(
                "iphone_detector.connection_router.run_automatic",
                return_value={"ok": True, "completed_accounts": 3},
            ) as native,
        ):
            self.assertEqual(ipa_manager.worker("discord-run", Path(directory)), 0)
            self.assertEqual(native.call_args.args[2], None)
            native.assert_called_once()

    def test_worker_single_account_routes_selection_to_router(self):
        import ipa_manager

        config = configured({"automation": {"notify_on_finish": False}})
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.logger.setup_logging"),
            patch.object(RunLock, "acquire", return_value=True),
            patch("iphone_detector.config.load_config", return_value=config),
            patch("iphone_detector.remote_tunnel.enabled", return_value=True),
            patch(
                "iphone_detector.connection_router.run_automatic",
                return_value={"ok": True, "completed_accounts": 1},
            ) as automatic,
            patch("iphone_detector.remote_tunnel.submit") as submit,
        ):
            self.assertEqual(ipa_manager.worker("discord-run", Path(directory), None, [1]), 0)
            self.assertEqual(automatic.call_args.args[2], [1])
            submit.assert_not_called()

    def test_installed_service_avoids_elevation_and_restores_after_lease(self):
        from iphone_detector import wifi

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.bridge_service.installed", return_value=True),
            patch("iphone_detector.bridge_service.ServiceLease") as lease,
            patch.object(wifi, "elevate") as elevate,
            patch.object(wifi, "authenticate", return_value={"name": "test phone"}),
        ):
            with wifi.WifiBridge(Path(directory), "phone", Mock()):
                pass
            elevate.assert_not_called()
            lease.return_value.close.assert_called_once()
            self.assertTrue(
                json.loads((Path(directory) / "bridge-status.json").read_text())[
                    "apple_service_restored"
                ]
            )

    def test_remote_bridge_never_falls_back_to_uac_if_service_removed(self):
        from iphone_detector import wifi

        with (
            tempfile.TemporaryDirectory() as directory,
            patch("iphone_detector.bridge_service.installed", return_value=False),
            patch.object(wifi, "elevate") as elevate,
        ):
            with self.assertRaisesRegex(RuntimeError, "service is unavailable"):
                with wifi.WifiBridge(Path(directory), "phone", Mock(), allow_elevation=False):
                    pass
            elevate.assert_not_called()

    def test_gui_bridge_cannot_request_elevation_even_with_legacy_flag(self):
        from iphone_detector import wifi

        for legacy_flag in (False, True):
            with (
                tempfile.TemporaryDirectory() as directory,
                patch("iphone_detector.bridge_service.installed", return_value=False),
                patch.object(wifi, "elevate") as elevate,
            ):
                with self.assertRaisesRegex(RuntimeError, "Administrator prompts are disabled"):
                    with wifi.WifiBridge(
                        Path(directory), "phone", Mock(), allow_elevation=legacy_flag
                    ):
                        pass
                elevate.assert_not_called()

    def test_elevation_entry_point_is_disabled(self):
        from iphone_detector.wifi import elevate_arguments

        with self.assertRaisesRegex(RuntimeError, "Administrator prompts are disabled"):
            elevate_arguments(["--install-bridge", "test"])

    def test_initial_network_failure_retries_with_a_fresh_client(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Mock(), Mock()
            first.run.side_effect = OSError("network not ready")
            factory = Mock(side_effect=[first, second])
            sleep = Mock()
            bot.connect_with_retry(factory, "synthetic", Path(directory) / "stop", sleep)
            self.assertEqual(factory.call_count, 2)
            self.assertEqual(sleep.call_count, 5)
            second.run.assert_called_once_with("synthetic", log_handler=None)

    def test_stop_interrupts_network_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "stop"
            client = Mock()
            client.run.side_effect = OSError("offline")
            factory = Mock(return_value=client)
            bot.connect_with_retry(factory, "synthetic", marker, lambda _: marker.touch())
            factory.assert_called_once()

    def test_invalid_token_does_not_retry_forever(self):
        import discord

        with tempfile.TemporaryDirectory() as directory:
            client = Mock()
            client.run.side_effect = discord.LoginFailure("invalid token")
            factory, sleep = Mock(return_value=client), Mock()
            bot.connect_with_retry(factory, "synthetic", Path(directory) / "stop", sleep)
            factory.assert_called_once()
            sleep.assert_not_called()

    def test_save_keeps_startup_preference(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(bot, "SETTINGS", Path(directory) / "discord.json"),
        ):
            bot.save_settings({"user_id": 1, "guild_id": 2, "channel_id": 3}, "synthetic")
            saved = json.loads(bot.SETTINGS.read_text())
            saved["startup"] = True
            bot.SETTINGS.write_text(json.dumps(saved))
            bot.save_settings({"user_id": 1, "guild_id": 2, "channel_id": 3}, "")
            self.assertTrue(json.loads(bot.SETTINGS.read_text())["startup"])

    def test_startup_registration_saves_preference_and_migrates_shortcut(self):
        import os
        import winreg
        from iphone_detector import discord_setup as setup

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings = root / "discord.json"
            settings.write_text(json.dumps({"token": "encrypted", "startup": False}))
            shortcut = (
                root
                / "Microsoft/Windows/Start Menu/Programs/Startup/iPhone IPA Community Discord.lnk"
            )
            shortcut.parent.mkdir(parents=True)
            shortcut.touch()
            from unittest.mock import MagicMock

            with (
                patch.dict(os.environ, APPDATA=directory),
                patch.object(setup, "SETTINGS", settings),
                patch.object(
                    setup, "command", return_value=[r"C:\My App\manager.exe", "--discord"]
                ),
                patch("iphone_detector.startup_task.configure") as scheduled,
                patch.object(winreg, "CreateKeyEx", return_value=MagicMock()),
                patch.object(winreg, "SetValueEx") as write,
                patch.object(winreg, "DeleteValue") as delete,
            ):
                setup.set_startup(True)
                scheduled.assert_called_once_with(True, [r"C:\My App\manager.exe", "--discord"])
                write.assert_not_called()
                self.assertFalse(shortcut.exists())
                self.assertEqual(
                    json.loads(settings.read_text()), {"token": "encrypted", "startup": True}
                )
                setup.set_startup(False)
                self.assertEqual(delete.call_count, 2)
                self.assertFalse(json.loads(settings.read_text())["startup"])

    def test_scheduled_logon_task_has_no_admin_or_password_and_restarts(self):
        from iphone_detector import startup_task
        from unittest.mock import MagicMock

        scheduler = MagicMock()
        task = scheduler.NewTask.return_value
        with (
            patch("win32com.client.Dispatch", return_value=scheduler),
            patch("iphone_detector.bridge_service.current_sid", return_value="owner-sid"),
        ):
            startup_task.configure(True, [r"C:\My App\manager.exe", "--discord"])
        self.assertEqual(task.Principal.RunLevel, 0)
        self.assertEqual(task.Principal.LogonType, 3)
        self.assertEqual(task.Triggers.Create.return_value.UserId, "owner-sid")
        self.assertEqual(task.Triggers.Create.return_value.Delay, "PT15S")
        self.assertEqual(task.Settings.ExecutionTimeLimit, "PT0S")
        self.assertEqual(task.Settings.RestartInterval, "PT1M")
        self.assertFalse(task.Settings.DisallowStartIfOnBatteries)
        self.assertFalse(task.Settings.StopIfGoingOnBatteries)
        self.assertEqual(
            scheduler.GetFolder.return_value.RegisterTaskDefinition.call_args.args[-2:], (None, 3)
        )
