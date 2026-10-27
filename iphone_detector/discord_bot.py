"""Private slash-command controller. Runs as the interactive Windows user."""

import asyncio
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from iphone_detector.paths import DATA_ROOT, ASSET_ROOT, FROZEN
from iphone_detector import __version__

SETTINGS = DATA_ROOT / "discord.json"
LOG = logging.getLogger("iphone-detector.discord")


def validate_settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("Discord settings must be a JSON object. Repair the local configuration.")
    for name in ("user_id", "guild_id", "channel_id"):
        value = str(settings.get(name, ""))
        if not value.isdecimal() or not 0 < int(value) < 2**64:
            raise ValueError("Enter a valid Discord " + name.replace("_", " ") + ".")
    return {name: int(settings[name]) for name in ("user_id", "guild_id", "channel_id")}


def authorized(settings, user, guild, channel):
    return (user, guild, channel) == (
        settings["user_id"],
        settings["guild_id"],
        settings["channel_id"],
    )


def save_settings(values, token):
    import base64
    import win32crypt

    settings = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    settings.update(validate_settings(values))
    if token.strip():
        settings["token"] = base64.b64encode(
            win32crypt.CryptProtectData(
                token.strip().encode(), "iPhone IPA Manager Discord bot", None, None, None, 1
            )
        ).decode("ascii")
    elif SETTINGS.exists():
        settings["token"] = json.loads(SETTINGS.read_text(encoding="utf-8")).get("token", "")
    if not settings.get("token"):
        raise ValueError("Enter the bot token locally.")
    from iphone_detector.storage import write_json

    write_json(SETTINGS, settings)


def load_settings():
    import base64
    import win32crypt

    saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
    settings = validate_settings(saved)
    token = win32crypt.CryptUnprotectData(base64.b64decode(saved["token"]), None, None, None, 1)[
        1
    ].decode()
    return settings, token


def command(arguments):
    prefix = [sys.executable] + ([] if FROZEN else [str(ASSET_ROOT / "ipa_manager.py")])
    if os.environ.get("IPA_MANAGER_DATA_DIR"):
        prefix += ["--data-dir", str(DATA_ROOT)]
    return prefix + arguments


def connect_with_retry(factory, token, stop_file, sleep=time.sleep):
    """Gateway reconnect alone does not retry failures during login/command sync."""
    import aiohttp
    import discord

    delay = 5
    while not stop_file.exists():
        try:
            factory().run(token, log_handler=None)
            return
        except (OSError, TimeoutError, aiohttp.ClientError, discord.HTTPException) as exc:
            if isinstance(exc, discord.HTTPException) and exc.status < 500 and exc.status != 429:
                LOG.error(
                    "Discord configuration rejected (HTTP %s). Check the local token and server permissions.",
                    exc.status,
                )
                return
            LOG.warning(
                "Discord connection unavailable (%s); retrying in %ss.", type(exc).__name__, delay
            )
            for _ in range(delay):
                if stop_file.exists():
                    return
                sleep(1)
            delay = min(delay * 2, 60)
        except discord.LoginFailure:
            LOG.error("Discord token rejected. Update it in Discord setup.")
            return


class Controller:
    def __init__(self):
        self.process = None
        self.job = None
        self.task = None
        self.state = "Ready."
        self.refresh = False
        self.accounts = None
        self.expected = 0
        self.starting = False
        self._cancel_start = threading.Event()
        self._launch_lock = threading.Lock()

    async def start_async(self, refresh=False, accounts=None):
        """Keep discovery and IPA hashing off the Discord gateway event loop."""
        if self.starting:
            raise RuntimeError("An installation is already starting.")
        self.starting = True
        self._cancel_start.clear()
        worker = asyncio.create_task(
            asyncio.to_thread(self.start, refresh=refresh, accounts=accounts)
        )
        try:
            await asyncio.shield(worker)
        except asyncio.CancelledError:
            # Cancelling an await cannot kill its thread. Withdraw launch permission
            # and wait for preflight to finish before accepting another request.
            self.stop()
            try:
                await worker
            except Exception:
                LOG.info("Cancelled Discord installation startup finished.")
            raise
        finally:
            self.starting = False

    def start(self, refresh=False, accounts=None):
        if refresh and accounts is not None:
            raise ValueError("Profile refresh always renews all configured apps.")
        if (self.process is not None and self.process.poll() is None) or (
            self.task is not None and not self.task.done()
        ):
            raise RuntimeError("An installation is already running.")
        from iphone_detector.bridge_service import installed
        from iphone_detector.background import make_job, selected_accounts
        from iphone_detector.config import load_config
        from iphone_detector.settings import get_workflow
        from iphone_detector import remote_tunnel

        if refresh:
            from iphone_detector.connection_router import route_ready

            if not route_ready(refresh=True):
                raise RuntimeError(
                    "Refresh needs an authenticated local phone or a ready remote tunnel. Use /queue to wait for connectivity."
                )
        if not refresh and remote_tunnel.enabled():
            remote_tunnel.ensure_started()
            if not installed() and remote_tunnel.state()["state"] != "ready":
                from iphone_detector.connection_router import route_ready

                if not route_ready():
                    raise RuntimeError(remote_tunnel.describe())
        elif not refresh and not installed():
            from iphone_detector.connection_router import route_ready

            if not route_ready():
                raise RuntimeError(
                    "Connect the paired iPhone by USB, or configure a local/remote connection. Use /queue to wait for connectivity."
                )
        config = load_config() or {}
        chosen = selected_accounts(get_workflow(config), accounts)
        request = make_job(config, "Network", accounts=accounts)
        self.job = (
            DATA_ROOT
            / "automation-runs"
            / ("discord-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        )
        self.job.mkdir(parents=True)
        if request.get("serial") and request.get("accounts"):
            from iphone_detector.storage import write_json

            write_json(
                self.job / "selection.json", {key: request[key] for key in ("serial", "accounts")}
            )
        self.refresh = refresh
        self.accounts = chosen if accounts is not None else None
        self.expected = len(chosen)
        arguments = [
            "--worker",
            "discord-refresh" if refresh else "discord-run",
            "--job-dir",
            str(self.job),
            "--parent-pid",
            str(os.getpid()),
        ]
        for account in chosen if not refresh and accounts is not None else []:
            arguments += ["--account", str(account + 1)]
        with self._launch_lock:
            if self._cancel_start.is_set():
                raise RuntimeError("Installation cancelled before launch; no device work started.")
            self.process = subprocess.Popen(
                command(arguments), creationflags=subprocess.CREATE_NO_WINDOW
            )
        self.state = (
            "Selecting the fastest available iPhone connection."
            if refresh or remote_tunnel.enabled()
            else "Connecting to the iPhone over Wi-Fi."
        )

    def stop(self):
        with self._launch_lock:
            if self.starting:
                self._cancel_start.set()
            if self.process is None or self.process.poll() is not None:
                return (
                    "Cancelling installation startup."
                    if self.starting
                    else "No Discord installation is running."
                )
            (self.job / "stop").touch()
            self.state = "Stopping the installation and cleaning up."
            return self.state

    def progress(self):
        path = self.job / "backend-events.jsonl"
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return self.state
        scope = "" if self.accounts else " of %d" % self.expected
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if not isinstance(event, dict) or not isinstance(event.get("stage"), str):
                continue
            labels = {
                "login": "Signing in",
                "login_retry": "Retrying sign-in",
                "install": "Installing",
                "completed_account": "Completed",
                "sign": "Signing",
                "prepare_profiles": "Downloading renewal profiles",
            }
            description = "{} — account {}{}.".format(
                labels.get(event.get("stage"), "Working"), event.get("account", "?"), scope
            )
            try:
                transfer = json.loads(
                    (self.job / "remote-transfer.json").read_text(encoding="utf-8")
                )
                active = transfer["account"]
                if (
                    type(active) is int
                    and 1 <= active <= 25
                    and transfer["stage"] in ("upload", "install", "verify", "refresh")
                ):
                    phase = {
                        "upload": "Uploading",
                        "install": "Installing",
                        "verify": "Verifying",
                        "refresh": "Renewing profiles",
                    }[transfer["stage"]]
                    description = "{} — account {}{}.".format(phase, active, scope)
                    if transfer["stage"] == "upload":
                        percent = min(
                            100,
                            transfer["uploaded_bytes"] * 100 // max(1, transfer["archive_bytes"]),
                        )
                        description += " {}% uploaded.".format(percent)
                    if event.get("account", 0) > active and event.get("stage") in (
                        "login",
                        "sign",
                        "install",
                    ):
                        description += " Preparing account {}.".format(event["account"])
            except OSError, ValueError, KeyError, TypeError:
                pass
            try:
                route = json.loads((self.job / "route.json").read_text(encoding="utf-8"))[
                    "transport"
                ]
                description += {
                    "local-wifi": " Local Wi-Fi.",
                    "remote": " Remote tunnel.",
                    "usb": " USB.",
                }.get(route, "")
            except OSError, ValueError, KeyError, TypeError:
                pass
            return description
        return self.state


def run():
    import discord
    from discord import app_commands
    from typing import Optional
    from iphone_detector.logger import setup_logging
    from iphone_detector.run_lock import RunLock

    singleton = RunLock()
    # Separate mutex for the listener; workers use the installation mutex.
    singleton.name = "Local\\iPhoneIPAManagerDiscord"
    if not singleton.acquire():
        raise RuntimeError("The Discord listener is already running.")
    setup_logging(log_file=str(DATA_ROOT / "discord.log"))
    LOG.info("Discord listener starting; waiting for network if necessary.")
    from iphone_detector import remote_tunnel

    try:
        remote_tunnel.ensure_started()
    except Exception:
        LOG.exception("Tunnel startup failed; keeping Discord controls available for diagnosis.")
    settings, token = load_settings()
    from iphone_detector.activation_health import ready

    ready("discord")
    controller = Controller()
    stop_file = DATA_ROOT / "discord-stop"
    stop_file.unlink(missing_ok=True)
    guild = discord.Object(id=settings["guild_id"])

    class Bot(discord.Client):
        async def on_ready(self):
            LOG.info("Discord bot online and ready for commands.")

        async def on_disconnect(self):
            LOG.info("Discord connection lost; automatic reconnect is enabled.")

        async def setup_hook(self):
            self.tree = app_commands.CommandTree(self)

            async def allow(interaction):
                if not authorized(
                    settings, interaction.user.id, interaction.guild_id, interaction.channel_id
                ):
                    await interaction.response.send_message(
                        "This command is restricted to its configured owner and private channel.",
                        ephemeral=True,
                    )
                    return False
                return True

            async def report(channel):
                try:
                    while controller.process.poll() is None:
                        try:
                            controller.state = controller.progress()
                        except OSError:
                            # A transient log read must not detach monitoring from a running job.
                            LOG.warning(
                                "Could not read current background progress; waiting for result."
                            )
                        await asyncio.sleep(2)
                    code = controller.process.returncode
                    try:
                        details = json.loads(
                            (controller.job / "result.json").read_text(encoding="utf-8")
                        )
                    except OSError, ValueError:
                        details = {}
                    if not isinstance(details, dict):
                        details = {}
                    installed_count = details.get("completed_accounts")
                    expected = controller.expected
                    if code == 0 and (
                        details.get("ok") is not True
                        or type(installed_count) is not int
                        or installed_count != expected
                    ):
                        controller.state = "The worker exited without a verified completion receipt. Inspect the local job before retrying."
                    elif code == 0 and controller.refresh:
                        try:
                            count = details["refreshed_accounts"]
                            controller.state = (
                                "Refresh completed: {} apps renewed using profiles; {} fully installed. "
                                "Profile readback verified. Launch after the old expiration remains unverified."
                            ).format(count, expected - count)
                        except OSError, ValueError, KeyError:
                            controller.state = "Refresh worker finished; check the local result for verification details."
                    elif code == 0:
                        if isinstance(installed_count, int) and controller.accounts is not None:
                            controller.state = (
                                "Installed account {} only. The other accounts keep their current apps; "
                                "use /sideload without an account to install all configured accounts."
                            ).format(", ".join(str(number + 1) for number in controller.accounts))
                        else:
                            controller.state = "All configured apps installed successfully."
                    else:
                        controller.state = (
                            "Installation cancelled; Apple connection cleanup finished."
                            if code == 130
                            else "Installation stopped with an error. Check the local manager logs; no automatic retry was started."
                        )
                    from iphone_detector.discord_outbox import enqueue

                    queued = await asyncio.to_thread(
                        enqueue, DATA_ROOT, "job:" + str(controller.job), controller.state
                    )
                    if not queued:
                        await channel.send(
                            controller.state, allowed_mentions=discord.AllowedMentions.none()
                        )
                except Exception:
                    LOG.exception("Could not report Discord installation outcome")

            async def start_install(index, install_interaction):
                """Start a single-account install from a picker; reports in the channel."""
                await install_interaction.response.defer(ephemeral=True)
                try:
                    await controller.start_async(accounts=[index])
                    controller.task = asyncio.create_task(report(install_interaction.channel))
                    await install_interaction.followup.send(
                        "Started installing account %d only. "
                        "I’ll report the result in this channel." % (index + 1),
                        ephemeral=True,
                    )
                except Exception as exc:
                    LOG.exception("Discord picker install rejected")
                    message = (
                        str(exc)
                        if isinstance(exc, (RuntimeError, ValueError))
                        else "Could not start. Check the local Discord log."
                    )
                    await install_interaction.followup.send(
                        message[:1000],
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

            @self.tree.command(
                name="sideload",
                description="Install all configured apps using the fastest available connection",
                guild=guild,
            )
            @app_commands.describe(
                account="Optional: install only this account (number); leave empty for all configured accounts"
            )
            async def sideload(
                interaction: discord.Interaction,
                account: Optional[app_commands.Range[int, 1, 25]] = None,
            ):
                if not await allow(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    await controller.start_async(
                        accounts=[account - 1] if account is not None else None
                    )
                    controller.task = asyncio.create_task(report(interaction.channel))
                    await interaction.followup.send(
                        "Started. I’ll report the result in this channel.", ephemeral=True
                    )
                except Exception as exc:
                    LOG.exception("Discord start rejected")
                    # Only our actionable validation text, never credentials/backend logs.
                    message = (
                        str(exc)
                        if isinstance(exc, (RuntimeError, ValueError))
                        else "Could not start. Check the local Discord log."
                    )
                    await interaction.followup.send(
                        message[:1000],
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

            @sideload.autocomplete("account")
            async def sideload_account_autocomplete(interaction: discord.Interaction, current: int):
                if not authorized(
                    settings, interaction.user.id, interaction.guild_id, interaction.channel_id
                ):
                    return []
                try:
                    from iphone_detector.settings import get_workflow
                    from iphone_detector.config import load_config

                    return [
                        app_commands.Choice(
                            name=("Account %d — %s" % (index + 1, entry["email"]))[:100],
                            value=index + 1,
                        )
                        for index, entry in enumerate(get_workflow(load_config() or {}))
                        if str(index + 1).startswith(str(current))
                    ]
                except OSError, ValueError:
                    return []

            @self.tree.command(
                name="refresh",
                description="Experimental profile-only renewal; changed versions use full installation",
                guild=guild,
            )
            async def refresh(interaction: discord.Interaction):
                if not await allow(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    await controller.start_async(refresh=True)
                    controller.task = asyncio.create_task(report(interaction.channel))
                    await interaction.followup.send(
                        "Refresh started. Preparing profiles locally, then renewing the installed apps. "
                        "Use /sideload when you want to install changed IPA contents.",
                        ephemeral=True,
                    )
                except Exception as exc:
                    LOG.exception("Discord refresh rejected")
                    message = (
                        str(exc)
                        if isinstance(exc, (RuntimeError, ValueError))
                        else "Could not start. Check the local Discord log."
                    )
                    await interaction.followup.send(
                        message[:1000],
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

            @self.tree.command(
                name="diagnostics",
                description="Show a support report without accounts or credentials",
                guild=guild,
            )
            async def diagnostics(interaction: discord.Interaction):
                if not await allow(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                from iphone_detector.diagnostics import report

                snapshot = await asyncio.to_thread(report)
                await interaction.followup.send(
                    "Support snapshot (no accounts, paths, credentials or raw logs):\n```json\n"
                    + json.dumps(snapshot, indent=2)
                    + "\n```",
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

            @self.tree.command(
                name="updates",
                description="Check the installed bot version and official releases",
                guild=guild,
            )
            async def updates(interaction: discord.Interaction):
                if not await allow(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                from iphone_detector.updates import UpdateError, check_updates

                try:
                    release = await asyncio.to_thread(check_updates)
                    message = (
                        release.summary()
                        + "\n"
                        + release.url
                        + "\nUpdates require replacing the Windows app. Let active jobs finish first. "
                        "Restarting the remote tunnel may require home Wi-Fi."
                    )
                except UpdateError as exc:
                    message = f"Running version {__version__}. {exc}"
                except Exception:
                    LOG.exception("Discord update check failed")
                    message = f"Running version {__version__}. Could not check for updates. Try again later."
                await interaction.followup.send(
                    message, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
                )

            @self.tree.command(
                name="status", description="Show the current installation status", guild=guild
            )
            async def status(interaction: discord.Interaction):
                if await allow(interaction):
                    from iphone_detector.auto_refresh import describe as automatic_status

                    detail = ("\n" + remote_tunnel.describe()) if remote_tunnel.enabled() else ""
                    detail += "\n" + automatic_status()
                    from iphone_detector.worker_registry import active_jobs

                    active = await asyncio.to_thread(active_jobs, DATA_ROOT)
                    state = controller.state
                    if active:
                        state = "Active: %s · accounts %s" % (
                            active[0]["mode"],
                            ", ".join(map(str, active[0]["accounts"])),
                        )
                    await interaction.response.send_message(
                        f"iPhone IPA Manager {__version__}\n" + state + detail, ephemeral=True
                    )

            @self.tree.command(
                name="accounts",
                description="Show each account number, Apple account and saved IPA",
                guild=guild,
            )
            async def accounts(
                interaction: discord.Interaction, page: app_commands.Range[int, 1, 5] = 1
            ):
                if not await allow(interaction):
                    return
                from iphone_detector.ipa_assignments import needs_install
                from iphone_detector.config import load_config
                from iphone_detector.settings import get_workflow

                try:
                    config = load_config() or {}
                    workflow = get_workflow(config)
                except ValueError as exc:
                    await interaction.response.send_message(
                        str(exc)[:1000],
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                    return
                folder = Path(
                    config.get("automation", {}).get(
                        "ipa_directory", str(Path.home() / "Downloads/ipas")
                    )
                )
                embed = discord.Embed(
                    title="Saved account assignments",
                    description="These saved files are used by /sideload, /refresh and automatic six-day renewals.",
                )
                pages = max(1, (len(workflow) + 4) // 5)
                if page > pages:
                    await interaction.response.send_message(
                        "There are %d account pages." % pages, ephemeral=True
                    )
                    return
                for index, entry in enumerate(
                    workflow[(page - 1) * 5 : page * 5], (page - 1) * 5 + 1
                ):
                    filename = entry["ipa"] + ".ipa"
                    status_text = (
                        "Missing file — select another IPA with /inbox."
                        if not (folder / filename).is_file()
                        else "New IPA pending installation; the next install or refresh will install it."
                        if needs_install(config, index, entry["ipa"])
                        else "Saved IPA available."
                    )
                    embed.add_field(
                        name="Account %d" % index,
                        value="%s\n%s\n%s"
                        % (
                            discord.utils.escape_markdown(entry["email"]),
                            discord.utils.escape_markdown(filename),
                            status_text,
                        ),
                        inline=False,
                    )
                embed.description += "\nIPA folder: " + str(folder)[:800]
                embed.set_footer(
                    text="Page %d/%d. Use /accounts page to browse, /login to add, or /inbox to assign an IPA."
                    % (page, pages)
                )
                await interaction.response.send_message(
                    embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
                )

            @accounts.autocomplete("page")
            async def account_page_autocomplete(interaction: discord.Interaction, current: int):
                if not authorized(
                    settings, interaction.user.id, interaction.guild_id, interaction.channel_id
                ):
                    return []
                try:
                    from iphone_detector.settings import get_workflow

                    pages = max(1, (len(get_workflow()) + 4) // 5)
                    return [
                        app_commands.Choice(name="Page %d of %d" % (number, pages), value=number)
                        for number in range(1, pages + 1)
                        if str(number).startswith(str(current))
                    ]
                except OSError, ValueError:
                    return []

            @self.tree.command(
                name="inbox",
                description="Choose the account first, then choose an IPA filename",
                guild=guild,
            )
            @app_commands.describe(
                account="First choose the numbered Apple account",
                file="Then start typing an IPA filename to see matching files",
            )
            async def inbox(
                interaction: discord.Interaction, account: app_commands.Range[int, 1, 25], file: str
            ):
                if not await allow(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    from iphone_detector.config import load_config
                    from iphone_detector.discord_inbox import (
                        managed_files,
                        assign_managed,
                        create_view,
                        select_file,
                    )

                    files = await asyncio.to_thread(managed_files, load_config() or {})
                    if not files:
                        await interaction.followup.send(
                            "No IPAs in the configured IPA folder. Use /accounts to see its location, "
                            "then save an IPA there and try /inbox again.",
                            ephemeral=True,
                        )
                        return
                    selected = select_file(files, file) if file else None
                    view = create_view(
                        discord,
                        [selected] if selected else files,
                        lambda item: authorized(
                            settings, item.user.id, item.guild_id, item.channel_id
                        ),
                        start_install,
                        assign_managed,
                        selected=selected,
                        selected_account=account - 1,
                    )
                    await interaction.followup.send(
                        view.summary(),
                        view=view,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception as exc:
                    LOG.exception("Discord inbox unavailable")
                    message = (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else "Could not open the inbox. Check the local log."
                    )
                    await interaction.followup.send(
                        message[:1000],
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

            @inbox.autocomplete("account")
            async def inbox_account_autocomplete(interaction: discord.Interaction, current: int):
                return await sideload_account_autocomplete(interaction, current)

            @inbox.autocomplete("file")
            async def inbox_file_autocomplete(interaction: discord.Interaction, current: str):
                if not authorized(
                    settings, interaction.user.id, interaction.guild_id, interaction.channel_id
                ):
                    return []
                try:
                    from iphone_detector.config import load_config
                    from iphone_detector.discord_inbox import (
                        managed_files,
                        matching_files,
                        file_key,
                    )

                    files = await asyncio.to_thread(managed_files, load_config() or {})
                    return [
                        app_commands.Choice(name=path.name[:100], value=file_key(path))
                        for path in matching_files(files, current)
                    ]
                except OSError, ValueError:
                    return []

            @self.tree.command(
                name="assign",
                description="Assign an IPA already in the PC app folder to an account",
                guild=guild,
            )
            async def assign(interaction: discord.Interaction):
                if not await allow(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    from iphone_detector.config import load_config
                    from iphone_detector.discord_inbox import (
                        managed_files,
                        assign_managed,
                        create_view,
                    )

                    files = await asyncio.to_thread(managed_files, load_config() or {})
                    if not files:
                        await interaction.followup.send(
                            "No IPAs in the configured IPA folder. Use /accounts to see where to save them.",
                            ephemeral=True,
                        )
                        return
                    view = create_view(
                        discord,
                        files,
                        lambda item: authorized(
                            settings, item.user.id, item.guild_id, item.channel_id
                        ),
                        start_install,
                        assign_managed,
                    )
                    await interaction.followup.send(
                        view.summary(),
                        view=view,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
                except Exception as exc:
                    LOG.exception("Discord assignment picker unavailable")
                    message = (
                        str(exc)
                        if isinstance(exc, ValueError)
                        else "Could not open the IPA folder. Check the local log."
                    )
                    await interaction.followup.send(
                        message[:1000],
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )

            @self.tree.command(
                name="history",
                description="Show the last five runs and their verified outcomes",
                guild=guild,
            )
            async def history(interaction: discord.Interaction):
                if not await allow(interaction):
                    return
                await interaction.response.defer(ephemeral=True)
                try:
                    from iphone_detector.job_history import recent_jobs

                    entries = await asyncio.to_thread(recent_jobs, DATA_ROOT)
                    embed = discord.Embed(title="Recent runs")
                    for entry in entries:
                        scope = (
                            ", ".join(str(number) for number in entry["accounts"]) or "all accounts"
                        )
                        when = (
                            datetime.fromtimestamp(entry["updated"])
                            .astimezone()
                            .strftime("%d %b %H:%M")
                        )
                        embed.add_field(
                            name="%s — %s" % (when, entry["operation"]),
                            value="%s · %s%s"
                            % (
                                entry["status"],
                                scope,
                                " · " + entry["transport"] if entry["transport"] else "",
                            ),
                            inline=False,
                        )
                    if not entries:
                        embed.description = "No runs recorded yet."
                    await interaction.followup.send(
                        embed=embed, ephemeral=True, allowed_mentions=discord.AllowedMentions.none()
                    )
                except Exception:
                    LOG.exception("Could not read job history")
                    await interaction.followup.send(
                        "Could not read recent runs. Check the local log.", ephemeral=True
                    )

            @self.tree.command(
                name="stop",
                description="Safely cancel active Discord, GUI or scheduled installations",
                guild=guild,
            )
            async def stop(interaction: discord.Interaction):
                if await allow(interaction):
                    from iphone_detector.worker_registry import stop_active

                    count = await asyncio.to_thread(stop_active, DATA_ROOT)
                    own = controller.stop()
                    await interaction.response.send_message(
                        "Cancellation requested for %d active run(s). Cleanup may take a moment."
                        % count
                        if count
                        else own,
                        ephemeral=True,
                    )

            from iphone_detector.discord_management import register

            register(
                self.tree,
                guild,
                allow,
                discord,
                lambda item: authorized(settings, item.user.id, item.guild_id, item.channel_id),
            )
            from iphone_detector.discord_login import register as register_login
            from iphone_detector.discord_operations import register as register_operations

            register_operations(
                self.tree,
                guild,
                allow,
                lambda item: authorized(settings, item.user.id, item.guild_id, item.channel_id),
                discord,
            )

            self.account_login = register_login(
                self.tree,
                guild,
                allow,
                lambda item: authorized(settings, item.user.id, item.guild_id, item.channel_id),
                discord,
            )
            await self.tree.sync(guild=guild)
            LOG.info("Discord commands registered in configured private server.")

            async def local_stop():
                last_remote_check = 0
                last_delivery = 0
                while not self.is_closed():
                    if stop_file.exists():
                        await self.close()
                        return
                    if time.monotonic() - last_remote_check > 30:
                        try:
                            await asyncio.to_thread(remote_tunnel.ensure_started)
                            from iphone_detector.job_queue import ensure_started

                            await asyncio.to_thread(ensure_started)
                        except Exception:
                            LOG.exception("Tunnel maintenance failed; will check again.")
                        last_remote_check = time.monotonic()
                    if self.is_ready() and time.monotonic() - last_delivery > 20:
                        try:
                            from iphone_detector.discord_outbox import deliver

                            async def send_report(message):
                                channel = self.get_channel(
                                    settings["channel_id"]
                                ) or await self.fetch_channel(settings["channel_id"])
                                await channel.send(
                                    message, allowed_mentions=discord.AllowedMentions.none()
                                )

                            await deliver(DATA_ROOT, settings, send_report)
                        except Exception:
                            LOG.warning(
                                "Operational report delivery unavailable; queued reports retained."
                            )
                        last_delivery = time.monotonic()
                    await asyncio.sleep(1)

            self.local_stop_task = asyncio.create_task(local_stop())

        async def close(self):
            if hasattr(self, "account_login"):
                await self.account_login.close()
            controller.stop()
            if controller.process and controller.process.poll() is None:
                await asyncio.to_thread(controller.process.wait)
            await super().close()

    try:
        connect_with_retry(
            lambda: Bot(
                intents=discord.Intents.none(), allowed_mentions=discord.AllowedMentions.none()
            ),
            token,
            stop_file,
        )
    finally:
        controller.stop()
        singleton.close()
