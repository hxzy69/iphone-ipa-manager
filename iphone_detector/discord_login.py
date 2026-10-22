"""Owner-only modal dialogue for Apple sign-in and account enrollment."""

import asyncio
import logging
import re

from iphone_detector.account_login import LoginSession, append_account, save_iloader_account


def register(tree, guild, allow, authorized, discord):
    from iphone_detector.config import load_config
    from iphone_detector.discord_inbox import managed_files

    class Manager:
        session = None
        task = None

        async def close(self):
            session = self.session
            if session:
                await session.close()
            if self.task and not self.task.done():
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
            self.session = self.task = None

    manager = Manager()

    async def interaction_error(interaction, error):
        # Avoid default discord.py traceback/repr logging for credential forms.
        logging.getLogger("iphone-detector.discord").error(
            "Account dialogue interaction failed (%s).", type(error).__name__
        )
        message = "Could not complete that sign-in action. Use /login_cancel, then start a fresh /login dialogue."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            pass

    class PrivateModal(discord.ui.Modal):
        async def on_error(self, interaction, error):
            await interaction_error(interaction, error)

        async def interaction_check(self, interaction):
            if authorized(interaction):
                return True
            await interaction.response.send_message(
                "This dialogue belongs to the configured owner and private channel.", ephemeral=True
            )
            return False

    class PrivateView(discord.ui.View):
        async def on_error(self, interaction, error, item):
            await interaction_error(interaction, error)

        async def interaction_check(self, interaction):
            if authorized(interaction):
                return True
            await interaction.response.send_message(
                "This dialogue belongs to the configured owner and private channel.", ephemeral=True
            )
            return False

    class Enroll(PrivateView):
        def __init__(self, email, files):
            super().__init__(timeout=600)
            self.email, self.files, self.page = email, files, 0
            self.source = self.signature = None
            self.busy = False
            self.render()

        def render(self):
            self.clear_items()
            picker = discord.ui.Select(
                placeholder="Choose the IPA for this new automated account",
                row=0,
                options=[
                    discord.SelectOption(label=path.name[:100], value=str(self.page * 25 + index))
                    for index, path in enumerate(self.files[self.page * 25 : (self.page + 1) * 25])
                ],
            )

            async def choose(interaction):
                self.source = self.files[int(picker.values[0])]
                try:
                    stat = self.source.stat()
                    self.signature = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    await interaction.response.send_message(
                        "That file disappeared. Save an IPA in the folder, then use /login again.",
                        ephemeral=True,
                    )
                    return
                self.render()
                await interaction.response.edit_message(
                    content="Selected **%s** for **%s**. Confirm to append a new account; existing slots stay unchanged."
                    % (
                        discord.utils.escape_markdown(self.source.name),
                        discord.utils.escape_markdown(self.email),
                    ),
                    view=self,
                )

            picker.callback = choose
            self.add_item(picker)
            for label, step in [("Previous", -1), ("Next", 1)]:
                button = discord.ui.Button(
                    label=label,
                    row=1,
                    disabled=not 0 <= self.page + step <= (len(self.files) - 1) // 25,
                )

                async def turn(interaction, delta=step):
                    self.page += delta
                    self.render()
                    await interaction.response.edit_message(view=self)

                button.callback = turn
                self.add_item(button)
            confirm = discord.ui.Button(
                label="Add account to automation",
                style=discord.ButtonStyle.success,
                row=2,
                disabled=self.source is None,
            )

            async def commit(interaction):
                if self.busy:
                    await interaction.response.send_message(
                        "This account assignment is already being saved.", ephemeral=True
                    )
                    return
                self.busy = True
                await interaction.response.defer(ephemeral=True)
                try:
                    number = await asyncio.to_thread(
                        append_account, self.email, self.source, self.signature
                    )
                    self.stop()
                    await interaction.edit_original_response(
                        content="Account **%d** added: **%s** → **%s**. "
                        "It participates in future /sideload, /refresh and automatic renewals. "
                        "To install it now, use /sideload account:%d. Apple device/app limits still apply."
                        % (
                            number,
                            discord.utils.escape_markdown(self.email),
                            discord.utils.escape_markdown(self.source.name),
                            number,
                        ),
                        view=None,
                    )
                except Exception as exc:
                    self.busy = False
                    text = (
                        str(exc)
                        if isinstance(exc, (ValueError, RuntimeError))
                        else "Could not save the assignment; existing accounts were not replaced."
                    )
                    await interaction.followup.send(text[:1000], ephemeral=True)

            confirm.callback = commit
            self.add_item(confirm)

    class Code(PrivateModal, title="Apple verification code"):
        def __init__(self, session, challenge):
            super().__init__(timeout=180)
            self.session, self.challenge = session, challenge
            self.code = discord.ui.TextInput(
                label="Six-digit code from Apple", min_length=6, max_length=6
            )
            self.add_item(self.code)

        async def on_submit(self, interaction):
            value = str(self.code.value).strip()
            if not re.fullmatch(r"[0-9]{6}", value):
                await interaction.response.send_message(
                    "Enter six digits. Use Enter code again.", ephemeral=True
                )
                return
            await interaction.response.defer(ephemeral=True)
            try:
                await self.session.respond(self.challenge, {"action": "code", "code": value})
                await interaction.followup.send(
                    "Code submitted to Apple. Waiting for the next step.", ephemeral=True
                )
            except ValueError, OSError:
                await interaction.followup.send(
                    "This prompt has ended. Use the latest verification prompt or /login again.",
                    ephemeral=True,
                )

    class Challenge(PrivateView):
        def __init__(self, session, event):
            super().__init__(timeout=180)
            challenge = session.challenge
            code = discord.ui.Button(
                label="Enter code",
                style=discord.ButtonStyle.primary,
                disabled=bool(event.get("unknown")),
            )

            async def enter(interaction):
                if not session.waiting or challenge != session.challenge:
                    await interaction.response.send_message(
                        "Use the newest verification prompt.", ephemeral=True
                    )
                    return
                await interaction.response.send_modal(Code(session, challenge))

            code.callback = enter
            self.add_item(code)
            for label, action in [("Trusted devices", "devices"), ("Resend code", "resend")]:
                button = discord.ui.Button(
                    label=label, disabled=action == "resend" and bool(event.get("unknown"))
                )

                async def send(interaction, selected=action):
                    await interaction.response.defer(ephemeral=True)
                    try:
                        await session.respond(challenge, {"action": selected})
                        await interaction.followup.send(
                            "Request submitted to Apple. Waiting for the next prompt.",
                            ephemeral=True,
                        )
                    except ValueError, OSError:
                        await interaction.followup.send(
                            "This prompt has ended. Use the latest prompt.", ephemeral=True
                        )

                button.callback = send
                self.add_item(button)
            numbers = event.get("numbers", [])[:25]
            if numbers:
                picker = discord.ui.Select(
                    placeholder="Send SMS to a trusted number",
                    options=[
                        discord.SelectOption(
                            label="Trusted number ending in " + str(number["last_digits"])[-2:],
                            value=str(number["id"]),
                        )
                        for number in numbers
                    ],
                )

                async def sms(interaction):
                    await interaction.response.defer(ephemeral=True)
                    try:
                        await session.respond(
                            challenge, {"action": "sms", "number": int(picker.values[0])}
                        )
                        await interaction.followup.send(
                            "SMS requested. Waiting for Apple’s next step.", ephemeral=True
                        )
                    except ValueError, OSError:
                        await interaction.followup.send(
                            "This prompt has ended. Use the latest prompt.", ephemeral=True
                        )

                picker.callback = sms
                self.add_item(picker)
            cancel = discord.ui.Button(label="Cancel sign-in", style=discord.ButtonStyle.danger)

            async def stop(interaction):
                await interaction.response.defer(ephemeral=True)
                await session.close()
                await interaction.followup.send(
                    "Sign-in cancelled. Existing account assignments are unchanged.", ephemeral=True
                )

            cancel.callback = stop
            self.add_item(cancel)

    async def drive(session, interaction):
        complete = False
        try:
            async with asyncio.timeout(600):
                async for event in session.events():
                    stage = event.get("stage")
                    if stage == "challenge":
                        await interaction.followup.send(
                            (
                                "Apple needs a different verification method or a new code. "
                                if event.get("retry") or event.get("unknown")
                                else ""
                            )
                            + "Approve Apple’s sign-in notification if shown, then choose a verification action below. "
                            "Enter the code using the button, never as a channel message. This step expires after three minutes.",
                            view=Challenge(session, event),
                            ephemeral=True,
                        )
                    elif stage == "developer_session":
                        await interaction.followup.send(
                            "Apple accepted sign-in. Checking developer access before saving credentials.",
                            ephemeral=True,
                        )
                    elif stage == "complete":
                        await asyncio.to_thread(save_iloader_account, session.email)
                        complete = True
                        break
                    elif stage == "failed":
                        raise ValueError(
                            "Apple sign-in did not complete. Check your credentials, verification method or Apple account requirements, then try /login again."
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            text = (
                "Sign-in cancelled."
                if session.cancelled
                else str(exc)
                if isinstance(exc, ValueError)
                else "Sign-in timed out or lost its connection. Existing account assignments are unchanged. Start /login again."
            )
            try:
                await interaction.followup.send(text[:1200], ephemeral=True)
            except Exception:
                pass
        finally:
            await session.close()
            if manager.session is session:
                manager.session = None
        if complete:
            try:
                from iphone_detector.settings import get_workflow

                files = await asyncio.to_thread(managed_files, load_config() or {})
                existing = get_workflow(load_config() or {})
                if any(entry["email"].casefold() == session.email for entry in existing):
                    await interaction.followup.send(
                        "Signed in and saved in Windows Credential Manager. This email already has an automated account slot; use /accounts or /inbox.",
                        ephemeral=True,
                    )
                elif not files:
                    await interaction.followup.send(
                        "Signed in and saved in iloader. Add an IPA to the folder shown by /settings, then use /login again to assign it to a new account.",
                        ephemeral=True,
                    )
                else:
                    await interaction.followup.send(
                        "Signed in and saved in Windows Credential Manager and iloader. "
                        "Choose an IPA below to add a new automated account. Nothing is installed until you run /sideload or a scheduled renewal is due.",
                        view=Enroll(session.email, files),
                        ephemeral=True,
                    )
            except Exception:
                await interaction.followup.send(
                    "Sign-in succeeded, but the IPA picker could not open. Existing assignments are unchanged. Check /settings.",
                    ephemeral=True,
                )

    class Credentials(PrivateModal, title="Sign in to an Apple account"):
        def __init__(self, session):
            super().__init__(timeout=300)
            self.session = session
            self.email = discord.ui.TextInput(label="Apple account email", max_length=254)
            self.password = discord.ui.TextInput(label="Apple account password", max_length=4000)
            self.add_item(self.email)
            self.add_item(self.password)

        async def on_timeout(self):
            if not self.session.started and manager.session is self.session:
                manager.session = None

        async def on_submit(self, interaction):
            await interaction.response.defer(ephemeral=True)
            if manager.session is not self.session:
                await interaction.followup.send(
                    "This sign-in form expired. Use /login again.", ephemeral=True
                )
                return
            try:
                await self.session.start(str(self.email.value), str(self.password.value))
                await interaction.followup.send(
                    "Signing in to Apple. Follow-up verification prompts will appear privately here. Use /login_cancel to stop.",
                    ephemeral=True,
                )
                manager.task = asyncio.create_task(drive(self.session, interaction))
            except Exception as exc:
                await self.session.close()
                manager.session = None
                await interaction.followup.send(
                    str(exc)[:1000]
                    if isinstance(exc, ValueError)
                    else "Could not start Apple sign-in. Existing accounts are unchanged.",
                    ephemeral=True,
                )

    @tree.command(
        name="login",
        description="Privately sign in to Apple, handle verification and add an automated account",
        guild=guild,
    )
    async def login(interaction: discord.Interaction):
        if not await allow(interaction):
            return
        if manager.session:
            await interaction.response.send_message(
                "A sign-in dialogue is already open. Finish it or use /login_cancel.",
                ephemeral=True,
            )
            return
        view = PrivateView(timeout=300)
        button = discord.ui.Button(
            label="Open Apple sign-in form", style=discord.ButtonStyle.primary
        )

        async def begin(item):
            if manager.session:
                await item.response.send_message(
                    "A sign-in is already open. Use /login_cancel to stop it.", ephemeral=True
                )
                return
            session = LoginSession()
            manager.session = session
            try:
                await item.response.send_modal(Credentials(session))
            except Exception as exc:
                if manager.session is session:
                    manager.session = None
                await session.close()
                await interaction_error(item, exc)

        button.callback = begin
        view.add_item(button)
        await interaction.response.send_message(
            "Sign-in uses private Discord forms and Apple verification follow-ups. "
            "**Discord processes the password and codes you submit; this is not end-to-end encrypted, and the password field is not masked.** "
            "Credentials are saved in Windows Credential Manager only after Apple accepts sign-in and developer access. "
            "Do not send passwords or codes as channel messages. Apple may still require you to approve a sign-in on a trusted device or resolve account requirements on its website.",
            view=view,
            ephemeral=True,
        )

    @tree.command(
        name="login_cancel", description="Cancel an open Apple sign-in dialogue", guild=guild
    )
    async def login_cancel(interaction: discord.Interaction):
        if await allow(interaction):
            await interaction.response.defer(ephemeral=True)
            await manager.close()
            await interaction.followup.send(
                "Sign-in dialogue cancelled. Existing automated accounts are unchanged.",
                ephemeral=True,
            )

    return manager
