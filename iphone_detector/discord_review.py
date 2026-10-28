"""Private, expiring confirmation of the exact saved run plan."""

import asyncio
from iphone_detector.config import load_config
from iphone_detector.run_review import plan, verify


async def request(
    interaction,
    controller,
    report,
    allow,
    discord,
    *,
    refresh=False,
    accounts=None,
    expected_assignment=None,
    queued=False,
):
    review = await asyncio.to_thread(
        lambda: plan(load_config() or {}, "refresh" if refresh else "install", accounts)
    )

    class Confirmation(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=180)
            self.started = False

        async def interaction_check(self, item):
            if item.user.id != interaction.user.id:
                await item.response.send_message(
                    "This review belongs to the user who opened it.", ephemeral=True
                )
                return False
            return await allow(item)

        @discord.ui.button(label="Confirm and start", style=discord.ButtonStyle.primary)
        async def confirm(self, item, button):
            if self.started:
                await item.response.send_message(
                    "This review was already used. Start a fresh command.", ephemeral=True
                )
                return
            self.started = True
            await item.response.defer(ephemeral=True)
            try:
                await asyncio.to_thread(lambda: verify(review, load_config() or {}))
                if queued:
                    from iphone_detector.job_queue import enqueue

                    entry = await asyncio.to_thread(
                        enqueue, review["mode"], accounts, expected_review=review
                    )
                    await item.followup.send(
                        "Queued the reviewed run: "
                        + entry["id"]
                        + ". Assignments are frozen; changed files pause the queue."
                        + (
                            " Windows startup registration failed; reopen the app to resume the queue after a reboot."
                            if entry.get("startup_registered") is False
                            else ""
                        ),
                        ephemeral=True,
                    )
                    return
                await controller.start_async(
                    refresh=refresh,
                    accounts=accounts,
                    expected_assignment=expected_assignment,
                    expected_review=review,
                )
                controller.task = asyncio.create_task(report(item.channel))
                await item.followup.send(
                    "Started the reviewed run. I’ll report the result in this channel.",
                    ephemeral=True,
                )
            except Exception as exc:
                import logging

                logging.getLogger(__name__).exception("Reviewed Discord run rejected")
                await item.followup.send(
                    str(exc)[:1000]
                    if isinstance(exc, (ValueError, RuntimeError))
                    else "Could not start the reviewed run. Check the local log.",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, item, button):
            if self.started:
                await item.response.send_message(
                    "This review was already used. Use /stop for active work or /queue to manage waiting requests.",
                    ephemeral=True,
                )
                return
            self.started = True
            self.stop()
            await item.response.edit_message(
                content="Review cancelled. No run was started.", embed=None, view=None
            )

    embed = discord.Embed(
        title="Review changes",
        description="Saved assignments only. Confirm within three minutes; changed settings or files require a new review.",
    )
    # 25 accounts fit the embed's 25-field and 6,000-character limits.
    for row in review["rows"]:

        def text(value, limit):
            return discord.utils.escape_markdown(value)[:limit].rstrip("\\")

        embed.add_field(
            name="Account %d · %s" % (row["account"], text(row["email"], 45)),
            value="%s %s\n%s\n%s"
            % (
                text(row["app"], 24),
                text(row["version"], 12),
                text(row["ipa"], 45),
                row["operation"],
            ),
            inline=False,
        )
    embed.set_footer(
        text="Refresh eligibility is checked on the phone; a full installation may be required."
    )
    view = Confirmation()
    if queued:
        view.confirm.label = "Confirm and queue"
    await interaction.followup.send(
        embed=embed,
        view=view,
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )
