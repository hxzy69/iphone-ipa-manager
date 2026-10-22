"""Private Discord picker for received and managed IPAs."""

from pathlib import Path
import hashlib

from iphone_detector.ipa_inbox import import_ipa


def file_key(path):
    """Discord option values are limited to 100 characters; never send a filesystem path."""
    return hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:24]


def matching_files(files, query, limit=25):
    query = query.strip().casefold()
    return sorted(
        (path for path in files if query in path.name.casefold()),
        key=lambda path: not path.name.casefold().startswith(query),
    )[:limit]


def select_file(files, value):
    exact = [
        path
        for path in files
        if file_key(path) == value or path.name.casefold() == value.casefold()
    ]
    if len(exact) == 1:
        return exact[0]
    matches = matching_files(files, value, limit=2)
    if len(matches) == 1:
        return matches[0]
    raise ValueError(
        "Choose a matching file from the autocomplete suggestions, or leave file empty to browse all IPAs."
    )


def received_files(config):
    folder = Path(
        config.get("automation", {}).get("import_directory", str(Path.home() / "Downloads"))
    ).expanduser()
    if not folder.is_dir():
        raise ValueError("The source folder is missing. Choose it in Import IPA on the PC.")
    files = []
    for path in folder.iterdir():
        if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".ipa":
            files.append(path)
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def managed_files(config):
    folder = Path(
        config.get("automation", {}).get("ipa_directory", str(Path.home() / "Downloads/ipas"))
    ).expanduser()
    if not folder.is_dir():
        raise ValueError("The IPA folder is missing. Choose it in the PC app settings.")
    files = []
    for path in folder.iterdir():
        if path.is_file() and not path.is_symlink() and path.suffix.lower() == ".ipa":
            files.append(path)
    return sorted(files, key=lambda path: path.stat().st_mtime, reverse=True)


def _apply_assignment(account, source, signature, save, listing):
    """Shared validation: account range, exact file identity, then the caller's save step."""
    from iphone_detector.config import load_config
    from iphone_detector.settings import get_workflow

    config = load_config() or {}
    workflow = get_workflow(config)
    if type(account) is not int or account not in range(len(workflow)):
        raise ValueError("Choose one of the configured account numbers.")
    source = Path(source)
    if source not in listing(config):
        raise ValueError("This file is no longer in its folder. Open /inbox or /assign again.")
    stat = source.stat()
    if (stat.st_size, stat.st_mtime_ns) != signature:
        raise ValueError(
            "This file changed or is still arriving. Open /inbox after the file transfer finishes."
        )
    return save(config, workflow, source)


def assign_received(source, signature, account):
    """Serialize against installations; never trust Discord-supplied filesystem paths."""
    from iphone_detector.config import save_config
    from iphone_detector.run_lock import RunLock

    lock = RunLock()
    if not lock.acquire():
        raise RuntimeError("An installation or refresh is running. Try /inbox after it finishes.")
    destination = None
    try:

        def save(config, workflow, source):
            nonlocal destination
            directory = config.get("automation", {}).get(
                "ipa_directory", str(Path.home() / "Downloads/ipas")
            )
            destination = import_ipa(source, directory)
            workflow[account]["ipa"] = destination.stem
            from iphone_detector.ipa_assignments import mark_pending

            mark_pending(config, account, destination.stem)
            config.setdefault("automation", {})["accounts"] = [
                {"email": entry["email"], "ipa": entry["ipa"]} for entry in workflow
            ]
            save_config(config)
            return destination

        return _apply_assignment(account, source, signature, save, received_files)
    except Exception:
        if destination is not None:
            destination.unlink(missing_ok=True)
        raise
    finally:
        lock.close()


def assign_managed(source, signature, account):
    """Point an account at an IPA already in the managed folder; nothing is copied."""
    from iphone_detector.config import save_config
    from iphone_detector.ipa_inbox import validate_ipa
    from iphone_detector.run_lock import RunLock

    lock = RunLock()
    if not lock.acquire():
        raise RuntimeError("An installation or refresh is running. Try /assign after it finishes.")
    try:

        def save(config, workflow, source):
            validate_ipa(source)
            after = source.stat()
            if (after.st_size, after.st_mtime_ns) != signature:
                raise ValueError("This IPA changed during validation. Open /assign again.")
            from iphone_detector.settings import ipa_stem

            ipa_stem(source.name)
            workflow[account]["ipa"] = source.stem
            from iphone_detector.ipa_assignments import mark_pending

            mark_pending(config, account, source.stem)
            config.setdefault("automation", {})["accounts"] = [
                {"email": entry["email"], "ipa": entry["ipa"]} for entry in workflow
            ]
            save_config(config)
            return source

        return _apply_assignment(account, source, signature, save, managed_files)
    finally:
        lock.close()


def create_view(
    discord,
    files,
    owner_check,
    install_account=None,
    importer=None,
    selected=None,
    selected_account=None,
):
    """Ephemeral, paginated picker: file → account → explicit confirmation → optional install.

    importer defaults to the LocalSend receive-folder import (validated copy); the managed
    importer points an account at a file already in the IPA folder without copying it.
    """
    import asyncio
    from iphone_detector.config import load_config
    from iphone_detector.settings import get_workflow

    account_emails = [entry["email"] for entry in get_workflow(load_config() or {})]

    copying = importer is None or importer is assign_received
    heading = "IPA import" if copying else "IPA folder"
    picker_placeholder = (
        "1. Choose a received IPA" if copying else "1. Choose an IPA from the folder"
    )
    summary_note = (
        (
            "Wait for the file transfer to finish. Saving replaces only the selected account’s IPA assignment. "
            "It does not start an installation."
        )
        if copying
        else (
            "Saving points the selected account at this exact file; nothing is copied. "
            "/sideload and automatic renewals then use it for that account."
        )
    )
    saved_text = (
        (
            "Saved **%s** for **account %d**.\n"
            "This is now the saved IPA for /sideload, /refresh and automatic six-day renewals.\n"
            "The received file is preserved. Open /inbox to assign another IPA."
        )
        if copying
        else (
            "Assigned **%s** to **account %d**.\n"
            "/sideload, /refresh and automatic six-day renewals now use this file.\n"
            "Open /assign to point another account at a different file."
        )
    )

    class InstallNowView(discord.ui.View):
        def __init__(self, index):
            super().__init__(timeout=300)
            self.index = index
            self.started = False
            self.install_now.label = "Install account %d now" % (index + 1)

        async def interaction_check(self, interaction):
            if not owner_check(interaction):
                await interaction.response.send_message(
                    "This inbox belongs to the configured owner and private channel.",
                    ephemeral=True,
                )
                return False
            return True

        @discord.ui.button(label="Install now", style=discord.ButtonStyle.primary)
        async def install_now(self, interaction, button):
            if self.started:
                await interaction.response.send_message(
                    "This install was already requested.", ephemeral=True
                )
                return
            if hasattr(self, "expected_ipa"):
                from iphone_detector.config import load_config
                from iphone_detector.settings import get_workflow

                if get_workflow(load_config() or {})[self.index]["ipa"] != self.expected_ipa:
                    await interaction.response.send_message(
                        "This account’s IPA assignment changed. Open /inbox or /assign again.",
                        ephemeral=True,
                    )
                    return
            self.started = True
            button.disabled = True
            self.stop()
            await install_account(self.index, interaction)

    class InboxView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=300)
            self.page = 0
            self.source = selected
            stat = selected.stat() if selected is not None else None
            self.signature = (stat.st_size, stat.st_mtime_ns) if stat else None
            self.account = selected_account
            self.confirmed = False
            self.busy = False
            self.render()

        async def interaction_check(self, interaction):
            if not owner_check(interaction):
                await interaction.response.send_message(
                    "This inbox belongs to the configured owner and private channel.",
                    ephemeral=True,
                )
                return False
            return True

        def render(self):
            self.clear_items()
            chunk = files[self.page * 25 : (self.page + 1) * 25]
            picker = discord.ui.Select(
                placeholder=picker_placeholder,
                row=0,
                options=[
                    discord.SelectOption(
                        label=path.name[:100],
                        value=str(self.page * 25 + index),
                        default=path == self.source,
                    )
                    for index, path in enumerate(chunk)
                ],
            )

            async def choose_file(interaction):
                self.source = files[int(picker.values[0])]
                try:
                    stat = self.source.stat()
                    self.signature = (stat.st_size, stat.st_mtime_ns)
                except OSError:
                    await interaction.response.send_message(
                        "File disappeared. Open /inbox again.", ephemeral=True
                    )
                    return
                self.render()
                await interaction.response.edit_message(content=self.summary(), view=self)

            picker.callback = choose_file
            self.add_item(picker)
            accounts = discord.ui.Select(
                placeholder="2. Choose the Apple account",
                row=1,
                options=[
                    discord.SelectOption(
                        label=("Account %d — %s" % (index + 1, account_emails[index]))[:100],
                        value=str(index),
                        default=index == self.account,
                    )
                    for index in range(len(account_emails))
                ],
            )

            async def choose_account(interaction):
                self.account = int(accounts.values[0])
                self.render()
                await interaction.response.edit_message(content=self.summary(), view=self)

            accounts.callback = choose_account
            self.add_item(accounts)
            for label, delta in [("Previous", -1), ("Next", 1)]:
                button = discord.ui.Button(
                    label=label,
                    row=2,
                    disabled=not 0 <= self.page + delta <= (len(files) - 1) // 25,
                )

                async def turn(interaction, step=delta):
                    self.page += step
                    self.render()
                    await interaction.response.edit_message(content=self.summary(), view=self)

                button.callback = turn
                self.add_item(button)
            confirm = discord.ui.Button(
                label="Save account assignment",
                style=discord.ButtonStyle.success,
                row=2,
                disabled=self.source is None or self.account is None,
            )

            async def commit(interaction):
                if self.busy or self.confirmed:
                    await interaction.response.send_message(
                        "This selection is already being saved. Use /inbox for a new selection.",
                        ephemeral=True,
                    )
                    return
                self.busy = True
                # Snapshot the selections before yielding to another interaction.
                source, signature, account = self.source, self.signature, self.account
                for child in self.children:
                    child.disabled = True
                await interaction.response.edit_message(
                    content="Validating the IPA and saving its account…", view=self
                )
                try:
                    destination = await asyncio.to_thread(
                        importer or assign_received, source, signature, account
                    )
                    self.confirmed = True
                    self.stop()
                    install_view = InstallNowView(account) if install_account else None
                    if install_view is not None:
                        install_view.expected_ipa = destination.stem
                    await interaction.edit_original_response(
                        content=(
                            saved_text % (discord.utils.escape_markdown(source.name), account + 1)
                        ),
                        view=install_view,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception as exc:
                    import logging

                    logging.getLogger(__name__).exception("Discord IPA import failed")
                    text = (
                        str(exc)
                        if isinstance(exc, (ValueError, RuntimeError))
                        else "Import failed. Check the local log."
                    )
                    self.busy = False
                    self.render()
                    await interaction.edit_original_response(
                        content=text[:1000],
                        view=self,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

            confirm.callback = commit
            self.add_item(confirm)

        def summary(self):
            name = (
                discord.utils.escape_markdown(self.source.name)
                if self.source
                else "Choose an IPA below"
            )
            account = (
                "account %d" % (self.account + 1)
                if self.account is not None
                else "choose an account"
            )
            return ("**%s** · page %d/%d\n%s → **%s**\n%s Selection expires after 5 minutes.") % (
                heading,
                self.page + 1,
                (len(files) + 24) // 25,
                name,
                account,
                summary_note,
            )

    return InboxView()
