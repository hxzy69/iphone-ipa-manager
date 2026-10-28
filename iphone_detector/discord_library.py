"""Private IPA update suggestions, assignment recovery and setup/progress summaries."""

import asyncio
from typing import Optional
from iphone_detector.config import load_config


def register(tree, guild, allow, can_suggest, discord, reply):
    from discord import app_commands

    @tree.command(
        name="ipa_updates",
        description="Find a newer IPA in the saved folder; optionally replace one account's assignment",
        guild=guild,
    )
    async def ipa_updates(
        interaction: discord.Interaction, account: Optional[app_commands.Range[int, 1, 25]] = None
    ):
        if not await allow(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        from iphone_detector import ipa_library
        from iphone_detector.settings import get_workflow

        def find():
            config = load_config() or {}
            workflow = get_workflow(config)
            return workflow, ipa_library.updates(ipa_library.scan(config), workflow)

        try:
            workflow, suggestions = await asyncio.to_thread(find)
            if account is None:
                text = (
                    "\n".join(
                        "Account %d: %s → %s · %s"
                        % (index + 1, old.version[:8], new.version[:8], new.path.name[:24])
                        for index, (old, new) in suggestions.items()
                    )
                    or "No unambiguous newer numeric app versions found in the IPA folder."
                )
                await interaction.followup.send(
                    text[:1750]
                    + "\nUse /ipa_updates account to review a replacement. Different bundle IDs and ambiguous same-version files are not auto-selected.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return
            suggestion = suggestions.get(account - 1)
            if suggestion is None:
                raise ValueError(
                    "No unambiguous newer version found for that account. Use /inbox to select a file manually."
                )
            old, new = suggestion
            email = workflow[account - 1]["email"]

            class Replace(discord.ui.View):
                def __init__(self):
                    super().__init__(timeout=180)
                    self.used = False

                async def interaction_check(self, item):
                    return await allow(item)

                @discord.ui.button(
                    label="Replace with newer IPA", style=discord.ButtonStyle.primary
                )
                async def replace(self, item, button):
                    if self.used:
                        await item.response.send_message(
                            "This suggestion was already used. Request a fresh one.", ephemeral=True
                        )
                        return
                    self.used = True
                    await reply(item, save)

            def save():
                from iphone_detector.discord_inbox import assign_managed

                assign_managed(
                    new.path,
                    new.signature,
                    account - 1,
                    expected_email=email,
                    expected_ipa=old.path.stem,
                    expected_directory=str(new.path.parent),
                )
                return (
                    "Saved for account %d. The previous IPA assignment was replaced. Installs, refresh and automatic renewal will use the new IPA; no installation started."
                    % account
                )

            await interaction.followup.send(
                "Account %d · %s\n%s %s → %s\n%s\nSaving replaces this account's only IPA assignment."
                % (
                    account,
                    email,
                    new.name[:80],
                    old.version[:30],
                    new.version[:30],
                    new.path.name[:200],
                ),
                view=Replace(),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (ValueError, OSError) as exc:
            await interaction.followup.send(
                str(exc)[:1000]
                if isinstance(exc, ValueError)
                else "Could not read the IPA folder.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    @ipa_updates.autocomplete("account")
    async def account_autocomplete(interaction: discord.Interaction, current: int):
        if not can_suggest(interaction):
            return []
        from iphone_detector.settings import get_workflow

        try:
            workflow = await asyncio.to_thread(lambda: get_workflow(load_config() or {}))
            return [
                app_commands.Choice(
                    name=("Account %d · %s" % (i + 1, row["email"]))[:100], value=i + 1
                )
                for i, row in enumerate(workflow)
                if str(i + 1).startswith(str(current))
            ][:25]
        except ValueError, OSError:
            return []

    @tree.command(
        name="assignments",
        description="View assignment history or restore a previous IPA assignment",
        guild=guild,
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="View recent changes", value="history"),
            app_commands.Choice(name="Restore selected previous assignment", value="undo"),
        ]
    )
    async def assignments(
        interaction: discord.Interaction, action: str = "history", entry: Optional[str] = None
    ):
        if not await allow(interaction):
            return
        from iphone_detector.assignment_history import entries, undo

        def operation():
            if action == "undo":
                if not entry:
                    raise ValueError("Choose a history entry using autocomplete.")
                return (
                    "Restored account %d. The next install, refresh or renewal will use its previous IPA."
                    % undo(entry)
                )
            if action != "history":
                raise ValueError("Choose history or undo.")
            rows = entries(load_config() or {})[-10:]
            return (
                "\n".join(
                    "%s · %s → %s"
                    % (row["email"][:45], row["previous"]["ipa"][:50], row["current"]["ipa"][:50])
                    for row in reversed(rows)
                )
                or "No assignment changes recorded by this build yet."
            )

        await reply(interaction, operation)

    @assignments.autocomplete("entry")
    async def history_autocomplete(interaction: discord.Interaction, current: str):
        if not can_suggest(interaction):
            return []
        from iphone_detector.assignment_history import entries

        try:
            rows = await asyncio.to_thread(lambda: entries(load_config() or {}))
            return [
                app_commands.Choice(
                    name=(
                        row["email"]
                        + " · "
                        + row["current"]["ipa"]
                        + " → "
                        + row["previous"]["ipa"]
                    )[:100],
                    value=row["id"],
                )
                for row in reversed(rows)
                if current.casefold()
                in (row["email"] + row["current"]["ipa"] + row["previous"]["ipa"]).casefold()
            ][:25]
        except ValueError, OSError:
            return []

    @tree.command(
        name="progress",
        description="Current account stages, elapsed time and recovery guidance",
        guild=guild,
    )
    async def progress(interaction: discord.Interaction):
        if not await allow(interaction):
            return

        def operation():
            from iphone_detector.worker_registry import active_jobs
            from iphone_detector.paths import DATA_ROOT
            from iphone_detector.job_progress import snapshot

            active = active_jobs(DATA_ROOT)
            if not active:
                return "No active run. Use /history or /dashboard for earlier outcomes."
            value = snapshot(active[0]["job"])
            return (
                value["state"]
                + " · "
                + value["elapsed"]
                + "\n"
                + "\n".join(
                    "Account %d · %s" % (row["account"], row["stage"]) for row in value["accounts"]
                )
                + "\n"
                + value["hint"]
            )

        await reply(interaction, operation)

    @tree.command(
        name="setup", description="Guided setup steps and saved configuration checks", guild=guild
    )
    async def setup(interaction: discord.Interaction):
        if not await allow(interaction):
            return

        def operation():
            from iphone_detector.setup_flow import STEPS
            from iphone_detector.setup_flow import inspect

            checks = {row["code"]: row for row in inspect()["checks"]}
            return (
                "\n\n".join(
                    "%d. %s\n%s" % (i + 1, step[0], checks.get(step[2], {}).get("detail", step[1]))
                    for i, step in enumerate(STEPS)
                )
                + "\n\nUse /login, /inbox, /check and /autorefresh. Initial USB trust and remote pairing require local interaction. The desktop Guided setup opens each tool."
            )

        await reply(interaction, operation)
