"""Private dashboard, explicit queue/recovery and configuration backup controls."""

import asyncio
import io
import json
from pathlib import Path
from typing import Optional

from iphone_detector.paths import DATA_ROOT


def register(tree, guild, allow, can_suggest, discord):
    from discord import app_commands
    from iphone_detector import job_queue, operations
    from iphone_detector.config import load_config

    @tree.command(
        name="maintenance",
        description="Check free disk space or remove old completed-run logs",
        guild=guild,
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Show storage", value="storage"),
            app_commands.Choice(name="Clean old logs and terminal records", value="cleanup"),
        ]
    )
    async def maintenance(interaction: discord.Interaction, action: str = "storage"):
        if not await allow(interaction):
            return
        from iphone_detector import maintenance as service

        if action == "storage":

            def overview():
                value = service.preview()
                return f"Free space: {value['free_bytes'] / 1024**3:.1f} GiB. Safe cleanup: {value['files']} files, {value['reclaimable_bytes'] / 1024**2:.1f} MiB. IPA files and recovery receipts are preserved."

            await reply(interaction, overview)
            return
        if action != "cleanup":
            await interaction.response.send_message("Choose storage or cleanup.", ephemeral=True)
            return

        class Clean(discord.ui.View):
            async def interaction_check(self, item):
                return await allow(item)

            @discord.ui.button(
                label="Clean old completed-run logs", style=discord.ButtonStyle.secondary
            )
            async def clean(self, item, button):
                await reply(
                    item,
                    lambda: (
                        f"Removed {service.clean()} old files. IPA files and recovery receipts were preserved."
                    ),
                )
                self.stop()

        await interaction.response.send_message(
            "Remove successful inactive-run logs older than 30 days and terminal queue/delivered reports older than 90 days? Unresolved runs, IPA files, credentials and completion receipts are preserved.",
            ephemeral=True,
            view=Clean(timeout=120),
        )

    @tree.command(
        name="update",
        description="Prepare a verified app update or activate/roll back with confirmation",
        guild=guild,
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name=label, value=value)
            for label, value in (
                ("Download and verify latest release", "download"),
                ("Activate prepared update", "activate"),
                ("Roll back to previous executable", "rollback"),
            )
        ]
    )
    async def update(interaction: discord.Interaction, action: str = "download"):
        if not await allow(interaction):
            return
        from iphone_detector import guided_update
        from iphone_detector.job_history import read
        from iphone_detector.updates import UpdateError, check_updates

        if action == "download":
            await interaction.response.defer(ephemeral=True)
            try:
                release = await asyncio.to_thread(check_updates)
                if not release.available:
                    await interaction.followup.send(release.summary(), ephemeral=True)
                    return
                await interaction.followup.send(
                    "Downloading and verifying the Windows app and source. Running jobs stay intact.",
                    ephemeral=True,
                )
                await asyncio.to_thread(guided_update.prepare, release)
                await interaction.followup.send(
                    "Release verified. Use /update → Activate prepared update when ready. Activation restarts the bot and tunnel; home Wi-Fi may be needed afterward.",
                    ephemeral=True,
                )
            except Exception as exc:
                import logging

                logging.getLogger("iphone-detector.discord").exception("Update preparation failed.")
                reason = (
                    str(exc)
                    if isinstance(exc, UpdateError)
                    else "At least 1 GiB of free disk space is required."
                    if isinstance(exc, ValueError)
                    and str(exc)
                    == "Keep at least 1 GiB free to download and prepare an update safely."
                    else "The downloaded release failed validation."
                    if isinstance(exc, ValueError)
                    else "The release could not be downloaded or accessed."
                    if isinstance(exc, OSError)
                    else "The update service reported an error. See the local Discord log."
                )
                await interaction.followup.send(
                    reason + " The running version was preserved.",
                    ephemeral=True,
                )
            return
        if action not in ("activate", "rollback"):
            await interaction.response.send_message(
                "Choose download, activate or rollback.", ephemeral=True
            )
            return
        plan = (
            guided_update.PREVIOUS
            if action == "rollback"
            else read(DATA_ROOT / "prepared-update.json").get("plan")
        )
        if not plan or not Path(plan).is_file():
            await interaction.response.send_message(
                "No prepared update is available. Download one first.", ephemeral=True
            )
            return

        class Activate(discord.ui.View):
            async def interaction_check(self, item):
                return await allow(item)

            @discord.ui.button(
                label="Restart components and activate", style=discord.ButtonStyle.danger
            )
            async def activate(self, item, button):
                await item.response.send_message(
                    "Activation requested. The bot will go offline briefly. If the connection is lost, rejoin home Wi-Fi to restore the phone tunnel.",
                    ephemeral=True,
                )
                try:
                    await asyncio.to_thread(guided_update.start, plan)
                except Exception:
                    import logging

                    logging.getLogger("iphone-detector.discord").exception(
                        "Update activation could not start."
                    )
                    await item.followup.send(
                        "Activation could not start. Check active work and the local updater status.",
                        ephemeral=True,
                    )
                self.stop()

        await interaction.response.send_message(
            "Finish or cancel queued requests first. This closes the old desktop app, restarts background components, and migrates startup tasks without administrator prompts. The phone tunnel may need home Wi-Fi to reconnect. Continue?",
            view=Activate(timeout=120),
            ephemeral=True,
        )

    async def reply(interaction, operation):
        await interaction.response.defer(ephemeral=True)
        try:
            text = await asyncio.to_thread(operation)
        except (OSError, ValueError, RuntimeError) as exc:
            text = (
                str(exc)[:1500]
                if isinstance(exc, (ValueError, RuntimeError))
                else "Could not complete the request. Check the local activity log."
            )
        await interaction.followup.send(
            text[:1900], ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
        )

    @tree.command(
        name="dashboard",
        description="Account outcomes, profile expiry, connection and renewal",
        guild=guild,
    )
    async def dashboard(interaction: discord.Interaction, page: app_commands.Range[int, 1, 5] = 1):
        if not await allow(interaction):
            return

        def overview():
            from iphone_detector.dashboard import snapshot

            value = snapshot()
            pages = max(1, (len(value["accounts"]) + 4) // 5)
            if page > pages:
                raise ValueError(f"Choose a page from 1 to {pages}.")
            connection = value["connection"]
            lines = [
                connection["route"],
                connection["explanation"],
                value["renewal"],
                value["queue"],
            ]
            if connection["upload_mib_s"] is not None:
                lines.append(f"Average upload: {connection['upload_mib_s']} MiB/s")
            for account in value["accounts"][(page - 1) * 5 : page * 5]:
                lines.append(
                    f"\nAccount {account['number']} — {account['email']}\n{account['ipa'][:100]}\nLast verified: {account['last_verified']} ({account['operation']}); profile expiry: {account['profile_expiry']}"
                )
            lines.append(
                "\nUnknown expiry means no matching verified profile evidence. See /accounts for assignments."
            )
            return "\n".join(lines)

        await reply(interaction, overview)

    @tree.command(
        name="queue",
        description="Queue network installations/refreshes, view or cancel requests",
        guild=guild,
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name=label, value=value)
            for label, value in (
                ("Show queue", "show"),
                ("Queue installation", "install"),
                ("Queue refresh (all accounts)", "refresh"),
                ("Cancel waiting request", "cancel"),
                ("Dismiss failed request and resume", "dismiss"),
                ("Enable queue at Windows sign-in", "startup_on"),
                ("Disable queue at Windows sign-in", "startup_off"),
            )
        ]
    )
    async def queue(
        interaction: discord.Interaction,
        action: str = "show",
        account: Optional[app_commands.Range[int, 1, 25]] = None,
        request: Optional[str] = None,
    ):
        if not await allow(interaction):
            return

        def execute():
            if action in ("startup_on", "startup_off"):
                job_queue.configure_startup(action == "startup_on")
                return "Queue startup " + (
                    "enabled."
                    if action == "startup_on"
                    else "disabled. Open the app to resume waiting requests after a reboot."
                )
            if action == "show":
                values = job_queue.entries()[-15:]
                return (
                    job_queue.summary()
                    + "\n"
                    + "\n".join(
                        f"{e['id']} · {e['mode']} · {e['state']}"
                        + (" · " + job_queue.failure_label(e) if job_queue.failure_label(e) else "")
                        for e in values
                    )
                )
            if action in ("install", "refresh"):
                if action == "refresh" and account is not None:
                    raise ValueError("Queued refresh renews all accounts; leave account empty.")
                entry = job_queue.enqueue(action, [account - 1] if account is not None else None)
                return (
                    "Queued "
                    + entry["id"]
                    + ". Assignments are frozen; changed files pause the queue."
                    + (
                        " Windows sign-in startup could not be registered; reopen the app to resume after a reboot."
                        if not entry.get("startup_registered")
                        else ""
                    )
                )
            if action in ("cancel", "dismiss") and request:
                return "Request " + job_queue.change(request, action)["state"] + "."
            raise ValueError("Choose an action and select its request with autocomplete.")

        await reply(interaction, execute)

    @queue.autocomplete("request")
    async def queue_requests(interaction: discord.Interaction, current: str):
        if not can_suggest(interaction):
            return []
        try:
            return [
                app_commands.Choice(
                    name=f"{entry['state']} · {entry['mode']} · {entry['id']}"[:100],
                    value=entry["id"],
                )
                for entry in job_queue.entries()
                if entry["state"] in ("queued", "failed", "interrupted") and current in entry["id"]
            ][:25]
        except OSError, ValueError:
            return []

    @queue.autocomplete("account")
    async def queue_accounts(interaction: discord.Interaction, current: int):
        if not can_suggest(interaction):
            return []
        from iphone_detector.settings import get_workflow

        try:
            return [
                app_commands.Choice(name=f"Account {index} — {entry['email']}"[:100], value=index)
                for index, entry in enumerate(get_workflow(load_config() or {}), 1)
                if str(index).startswith(str(current))
            ]
        except OSError, ValueError:
            return []

    @tree.command(
        name="retry",
        description="Queue full installs for unfinished accounts, skipping verified completions",
        guild=guild,
    )
    async def retry(interaction: discord.Interaction, job: Optional[str] = None):
        if not await allow(interaction):
            return

        def execute():
            path = next((path for path in operations.jobs(DATA_ROOT) if path.name == job), None)
            if path is None:
                raise ValueError(
                    "Select a recent run with autocomplete. Inspect uncertain outcomes before retrying."
                )
            accounts = operations.retry_plan(path, load_config() or {})
            entry = job_queue.enqueue("install", accounts)
            return (
                "Queued full installation for unfinished accounts "
                + ", ".join(str(n + 1) for n in accounts)
                + ". Matching verified completions were skipped. Request: "
                + entry["id"]
            )

        await reply(interaction, execute)

    @retry.autocomplete("job")
    async def retry_jobs(interaction: discord.Interaction, current: str):
        if not can_suggest(interaction):
            return []
        return [
            app_commands.Choice(name=path.name[:100], value=path.name)
            for path in operations.jobs(DATA_ROOT)
            if current.casefold() in path.name.casefold()
        ][:25]

    @tree.command(
        name="backup",
        description="Export assignments/preferences or preview a configuration restore",
        guild=guild,
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Export private configuration", value="export"),
            app_commands.Choice(name="Preview restore from JSON", value="restore"),
        ]
    )
    async def backup(
        interaction: discord.Interaction,
        action: str = "export",
        file: Optional[discord.Attachment] = None,
    ):
        if not await allow(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        from iphone_detector import config_backup

        try:
            if action == "export":
                value = await asyncio.to_thread(config_backup.export)
                await interaction.followup.send(
                    "Private configuration backup: includes account emails and assignments. Credentials, pairing and local paths are excluded. Keep it private.",
                    file=discord.File(
                        io.BytesIO(json.dumps(value, indent=2).encode()),
                        filename="ipa-manager-settings.json",
                    ),
                    ephemeral=True,
                )
                return
            if action != "restore" or file is None or file.size > 256 * 1024:
                raise ValueError(
                    "Choose restore and attach a configuration JSON file smaller than 256 KiB."
                )
            value = config_backup.validate(json.loads(await file.read()))

            class Confirm(discord.ui.View):
                async def interaction_check(self, item):
                    return await allow(item)

                @discord.ui.button(
                    label="Restore these assignments", style=discord.ButtonStyle.danger
                )
                async def restore(self, item, button):
                    await reply(
                        item,
                        lambda: (
                            f"Restored {config_backup.restore(value)} accounts. Check local sign-in and IPA availability before running."
                        ),
                    )
                    self.stop()

            await interaction.followup.send(
                f"Replace saved assignments with {len(value['accounts'])} accounts? Credentials, pairing and the IPA folder stay local. A run or queued request blocks restore. Imported accounts still need saved Apple sign-in and available IPAs.",
                view=Confirm(timeout=120),
                ephemeral=True,
            )
        except (OSError, ValueError, RuntimeError, discord.HTTPException) as exc:
            import logging

            logging.getLogger("iphone-detector.discord").exception("Backup operation failed.")
            reason = (
                "The backup file is invalid or contains unsupported settings."
                if isinstance(exc, ValueError)
                else "Discord could not transfer the backup; check Attach Files permission."
                if isinstance(exc, discord.HTTPException)
                else "The local backup could not be accessed or is blocked by active work."
            )
            await interaction.followup.send(
                reason + " See the local Discord log for details.",
                ephemeral=True,
            )
