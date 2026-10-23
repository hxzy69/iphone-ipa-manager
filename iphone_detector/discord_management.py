"""Private management commands, shared by slash commands and the dashboard."""

import asyncio
from pathlib import Path
from typing import Optional


def register(tree, guild, allow, discord, can_suggest=None):
    from discord import app_commands
    from iphone_detector import discord_controls as controls
    from iphone_detector.config import load_config
    from iphone_detector.discord_inbox import managed_files

    can_suggest = can_suggest or (lambda interaction: False)

    async def reply(interaction, operation):
        await interaction.response.defer(ephemeral=True)
        try:
            text = await asyncio.to_thread(operation)
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception("Discord management request failed")
            text = (
                str(exc)[:1500]
                if isinstance(exc, ValueError)
                else "Could not complete this request. Check the local manager log."
            )
        await interaction.followup.send(
            text[:1950], ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @tree.command(
        name="help", description="Explain commands and the everyday workflow", guild=guild
    )
    async def help_command(interaction: discord.Interaction):
        if not await allow(interaction):
            return
        await interaction.response.send_message(
            "**Choose and install**\n"
            "/login — privately sign in, handle Apple verification and add an account with its IPA.\n"
            "/login_cancel — cancel an open sign-in dialogue.\n"
            "/accounts — account numbers, Apple accounts and saved apps.\n"
            "/files — browse the PC IPA folder.\n"
            "/inbox — select account first, then type the filename; Save keeps this assignment for future runs.\n"
            "/assign — open the same assignment picker without typing.\n"
            "/sideload [account] — full installation; omit account for all configured accounts.\n"
            "/refresh — renew all configured accounts; changed IPAs receive a full installation.\n\n"
            "**Manage and monitor**\n"
            "/panel — buttons for common actions.\n"
            "/status — active job, connection and next renewal.\n"
            "/updates — running version and official release downloads.\n"
            "/diagnostics — support health flags without accounts, paths or credentials.\n"
            "/history — recent verified outcomes.\n"
            "/stop — request safe cancellation, including GUI and scheduled jobs.\n"
            "/check — read-only setup checks.\n"
            "/settings — view or change IPA folder, timeout and Windows notifications.\n"
            "/autorefresh — view, enable or disable six-day renewals.\n\n"
            "The PC must be on and signed in. Keep Tailscale connected for remote use; "
            "establish the phone tunnel on home Wi-Fi before leaving. "
            "Initial device pairing, Apple verification and service installation require local interaction.",
            ephemeral=True,
        )

    @tree.command(
        name="files",
        description="Browse available IPA filenames and their account assignments",
        guild=guild,
    )
    async def files(interaction: discord.Interaction, page: app_commands.Range[int, 1, 10000] = 1):
        if not await allow(interaction):
            return

        def listing():
            from iphone_detector.settings import get_workflow

            config = load_config() or {}
            entries = managed_files(config)
            pages = max(1, (len(entries) + 9) // 10)
            if page > pages:
                raise ValueError("There are %d pages. Use /files page:%d." % (pages, pages))
            lines = ["**IPA files — page %d/%d (%d files)**" % (page, pages, len(entries))]
            workflow = get_workflow(config)
            for path in entries[(page - 1) * 10 : page * 10]:
                assigned = [
                    str(i + 1) for i, account in enumerate(workflow) if account["ipa"] == path.stem
                ]
                lines.append(
                    "%s · %.1f MB%s"
                    % (
                        discord.utils.escape_markdown(path.name)[:140],
                        path.stat().st_size / 1048576,
                        " · account " + ", ".join(assigned) if assigned else "",
                    )
                )
            lines.append(
                "Use /inbox to choose an account and type a filename.\nFolder: "
                + controls.settings_summary(config)["folder"]
            )
            return "\n".join(lines)

        await reply(interaction, listing)

    @files.autocomplete("page")
    async def page_autocomplete(interaction: discord.Interaction, current: int):
        if not can_suggest(interaction):
            return []
        try:
            entries = await asyncio.to_thread(managed_files, load_config() or {})
            pages = min(10000, max(1, (len(entries) + 9) // 10))
            return [
                app_commands.Choice(name="Page %d of %d" % (number, pages), value=number)
                for number in range(1, pages + 1)
                if str(number).startswith(str(current))
            ][:25]
        except OSError, ValueError:
            return []

    @tree.command(
        name="check",
        description="Check pairing configuration, IPA files, backend and connection",
        guild=guild,
    )
    async def check(interaction: discord.Interaction):
        if not await allow(interaction):
            return

        def inspect():
            value = controls.setup_check()
            return (
                "**Setup check**\nPaired device configured: {paired}\nIPA folder exists: {folder_exists}"
                "\nBackend available: {backend_exists}\nWi-Fi service installed: {wifi_service}"
                "\nMissing IPA accounts: {missing}\n{tunnel}\n{schedule}\n{note}"
            ).format(**value, missing=", ".join(value["missing_accounts"]) or "none")

        await reply(interaction, inspect)

    @tree.command(
        name="settings",
        description="View or change the IPA folder, installation timeout and notifications",
        guild=guild,
    )
    @app_commands.describe(
        folder="Existing absolute folder; first copy the currently saved IPAs into it",
        timeout_minutes="Installation timeout per account, in minutes",
        notifications="Windows completion notifications",
    )
    async def settings(
        interaction: discord.Interaction,
        folder: Optional[str] = None,
        timeout_minutes: Optional[app_commands.Range[int, 1, 120]] = None,
        notifications: Optional[bool] = None,
    ):
        if not await allow(interaction):
            return

        def update():
            changed = any(value is not None for value in (folder, timeout_minutes, notifications))
            value = (
                controls.change_settings(folder, timeout_minutes, notifications)
                if changed
                else controls.settings_summary(load_config() or {})
            )
            return (
                "**%s**\nIPA folder: %s\nTimeout: %d minutes per account\nWindows notifications: %s"
                % (
                    "Settings saved" if changed else "Current settings",
                    value["folder"],
                    value["timeout_minutes"],
                    "enabled" if value["notifications"] else "disabled",
                )
            )

        await reply(interaction, update)

    @settings.autocomplete("timeout_minutes")
    async def timeout_autocomplete(interaction: discord.Interaction, current: int):
        if not can_suggest(interaction):
            return []
        try:
            saved = controls.settings_summary(load_config() or {})["timeout_minutes"]
            numbers = sorted(set([5, 10, 15, 20, 30, 45, 60, 90, 120, saved]))
            return [
                app_commands.Choice(
                    name="%d minutes%s" % (number, " (current)" if number == saved else ""),
                    value=number,
                )
                for number in numbers
                if 1 <= number <= 120 and str(number).startswith(str(current))
            ][:25]
        except OSError, ValueError, TypeError:
            return []

    @settings.autocomplete("folder")
    async def folder_autocomplete(interaction: discord.Interaction, current: str):
        if not can_suggest(interaction):
            return []

        def suggestions():
            saved = controls.settings_summary(load_config() or {})["folder"]
            candidates = [
                Path(saved),
                Path.home() / "Downloads/ipas",
                Path.home() / "Downloads",
                Path.home() / "Desktop",
            ]
            if current:
                from itertools import islice

                typed = Path(current).expanduser()
                parent = typed if current.endswith(("\\", "/")) else typed.parent
                if parent.is_absolute() and parent.is_dir():
                    candidates.extend(
                        path for path in islice(parent.iterdir(), 1000) if path.is_dir()
                    )
            result = []
            seen = set()
            for path in candidates:
                value = str(path)
                if (
                    len(value) <= 100
                    and value.casefold() not in seen
                    and path.is_dir()
                    and (not current or current.casefold() in value.casefold())
                ):
                    seen.add(value.casefold())
                    result.append(
                        app_commands.Choice(
                            name=("Current: " + value if value == saved else value)[:100],
                            value=value,
                        )
                    )
            return result[:25]

        try:
            return await asyncio.to_thread(suggestions)
        except OSError, ValueError, TypeError:
            return []

    @tree.command(
        name="autorefresh",
        description="View or enable/disable automatic six-day renewals",
        guild=guild,
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name=name.capitalize(), value=name)
            for name in ("status", "enable", "disable")
        ]
    )
    async def autorefresh(interaction: discord.Interaction, action: str = "status"):
        if not await allow(interaction):
            return

        def update():
            from iphone_detector import auto_refresh

            if action not in ("status", "enable", "disable"):
                raise ValueError("Choose status, enable or disable.")
            if action != "status":
                auto_refresh.configure(action == "enable")
            return auto_refresh.describe() + (
                "\nAn active run continues; use /stop to cancel it." if action == "disable" else ""
            )

        await reply(interaction, update)

    class Panel(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=300)
            for name, label, args in (
                ("status", "Status", ()),
                ("accounts", "Accounts", ()),
                ("files", "IPA files", ()),
                ("history", "History", ()),
                ("check", "Setup check", ()),
                ("settings", "Settings", ()),
                ("autorefresh", "Renewal schedule", ()),
                ("assign", "Choose an IPA", ()),
                ("help", "Help", ()),
                ("refresh", "Refresh all apps", ()),
                ("stop", "Stop active run", ()),
                ("login", "Add Apple account", ()),
                ("login_cancel", "Cancel sign-in", ()),
            ):
                button = discord.ui.Button(label=label, style=discord.ButtonStyle.secondary)

                async def invoke(interaction, command=name, arguments=args):
                    await tree.get_command(command, guild=guild).callback(interaction, *arguments)

                button.callback = invoke
                self.add_item(button)

        async def interaction_check(self, interaction):
            return await allow(interaction)

    @tree.command(
        name="panel", description="Open the private control panel with action buttons", guild=guild
    )
    async def panel(interaction: discord.Interaction):
        if await allow(interaction):
            await interaction.response.send_message(
                "**iPhone IPA Manager**\nChoose an action. Buttons remain available for five minutes. "
                "Use /sideload for full installations or /inbox for filename autocomplete.",
                view=Panel(),
                ephemeral=True,
            )
