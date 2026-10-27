"""Local Discord credentials, startup, and renewal preferences."""

import json
import os
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

from iphone_detector.paths import DATA_ROOT, FROZEN
from iphone_detector.discord_bot import SETTINGS, save_settings, command


def launch(parent=None, *, preview=False):
    saved = (
        {}
        if preview
        else (json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {})
    )
    if preview:
        saved = {
            "user_id": "123456789012345678",
            "guild_id": "234567890123456789",
            "channel_id": "345678901234567890",
        }
    if not isinstance(saved, dict):
        raise ValueError(
            "Discord settings are unreadable. Restore a valid settings file before continuing."
        )
    root = tk.Toplevel(parent) if parent is not None else tk.Tk()
    from iphone_detector.dialog_ui import shell, style_window, section, preview_notice

    if parent is None:
        style_window(root)
    else:
        root.transient(parent)
    frame, footer = shell(
        root,
        "Discord control",
        "Your private bot runs on this PC. Only the configured user, server and channel can issue commands.",
        width=760,
        height=750,
    )
    tabs = ttk.Notebook(frame)
    tabs.pack(fill="both", expand=True)
    credentials = ttk.Frame(tabs, padding=12)
    automation = ttk.Frame(tabs, padding=12)
    guide = ttk.Frame(tabs, padding=12)
    tabs.add(credentials, text="Bot connection")
    tabs.add(automation, text="Startup & renewal")
    tabs.add(guide, text="Setup instructions")
    tabs.enable_traversal()
    section(
        guide,
        "1. Create and invite your bot",
        "In the Discord Developer Portal, create an application and add a bot. Invite it to your private server with bot and applications.commands scopes, plus View Channel and Send Messages. Administrator permission and Message Content intent are not needed.",
    )
    section(
        guide,
        "2. Copy your IDs",
        "Enable Developer Mode in Discord settings → Advanced. Right-click your user, server and command channel to copy their IDs. Paste them in Bot connection.",
    )
    section(
        guide,
        "3. Connect",
        "Paste the bot token locally, then choose Save and start bot. Test /status in your channel. Leave the token blank to retain an existing saved token. Never send the token in a message.",
    )
    section(
        guide,
        "4. Keep this PC available",
        "The bot needs a signed-in Windows session. For remote phone access, establish the tunnel on home Wi-Fi before leaving. Startup & renewal contains the optional background settings.",
    )
    frame = credentials
    values = {}
    startup = tk.BooleanVar(value=bool(saved.get("startup", False)))
    defaults = {"user_id": "", "guild_id": "", "channel_id": ""}
    for key, label in [
        ("user_id", "Your Discord user ID"),
        ("guild_id", "Private server ID"),
        ("channel_id", "Command channel ID"),
        ("token", "Bot token (stored encrypted for this Windows user)"),
    ]:
        ttk.Label(frame, text=label).pack(anchor="w", pady=(7, 3))
        values[key] = tk.StringVar(
            value="" if key == "token" else str(saved.get(key, defaults[key]))
        )
        ttk.Entry(frame, textvariable=values[key], show="•" if key == "token" else "").pack(
            fill="x"
        )
    status = tk.StringVar(
        value="Enter the bot token here, never in Discord or chat. Leave blank to keep a saved token."
    )

    def startup_changed():
        if preview:
            preview_notice(root)
            return False
        try:
            set_startup(startup.get())
            status.set(
                "Windows sign-in startup "
                + ("enabled." if startup.get() else "disabled.")
                + " Saved immediately."
            )
        except Exception as exc:
            messagebox.showerror("Windows startup", str(exc), parent=root)

    frame = automation
    ttk.Label(frame, text="These switches save immediately.", style="Muted.TLabel").pack(
        anchor="w", pady=8
    )
    ttk.Checkbutton(
        frame,
        text="Start the bot when I sign in to Windows",
        variable=startup,
        command=startup_changed,
    ).pack(anchor="w", pady=(10, 0))
    from iphone_detector import remote_tunnel

    remote_enabled = tk.BooleanVar(value=False if preview else remote_tunnel.enabled())

    def remote_changed():
        if preview:
            preview_notice(root)
            return False
        try:
            config = remote_tunnel.settings()
            if remote_enabled.get() and not all(
                config.get(key) for key in ("ip", "port", "serial")
            ):
                remote_enabled.set(False)
                raise ValueError(
                    "This iPhone needs its one-time Tailscale RemotePairing setup first."
                )
            config["enabled"] = remote_enabled.get()
            remote_tunnel.atomic(remote_tunnel.SETTINGS, config)
            from iphone_detector.startup_task import configure

            configure(
                remote_enabled.get() and startup.get(),
                command(["--remote-daemon"]),
                name="iPhone IPA Community Remote Tunnel",
            )
            if remote_enabled.get():
                remote_tunnel.ensure_started()
            status.set(
                "Remote tunnel "
                + ("enabled. " + remote_tunnel.describe() if remote_enabled.get() else "disabled.")
            )
        except Exception as exc:
            messagebox.showerror("Remote tunnel", str(exc), parent=root)

    ttk.Checkbutton(
        frame,
        text="Keep the Tailscale tunnel open for remote sideloading",
        variable=remote_enabled,
        command=remote_changed,
    ).pack(anchor="w", pady=(8, 0))
    from iphone_detector import auto_refresh

    automatic = tk.BooleanVar(
        value=False if preview else bool(auto_refresh.read(auto_refresh.CONFIG).get("enabled"))
    )

    def automatic_changed():
        if preview:
            preview_notice(root)
            return False
        try:
            auto_refresh.configure(automatic.get())
            status.set(auto_refresh.describe())
        except Exception as exc:
            automatic.set(bool(auto_refresh.read(auto_refresh.CONFIG).get("enabled")))
            messagebox.showerror("Automatic refresh", str(exc), parent=root)

    ttk.Checkbutton(
        frame,
        text="Automatically refresh every 6 days (experimental profile renewal)",
        variable=automatic,
        command=automatic_changed,
    ).pack(anchor="w", pady=(8, 0))
    remote_status = tk.StringVar()
    ttk.Label(frame, textvariable=remote_status, wraplength=650).pack(anchor="w")

    def refresh_remote():
        if not root.winfo_exists():
            return
        if preview:
            remote_status.set(
                "Sample status: bot and remote services are not started in this preview."
            )
            return
        try:
            enabled = remote_tunnel.enabled()
            remote_enabled.set(enabled)
            automatic.set(bool(auto_refresh.read(auto_refresh.CONFIG).get("enabled")))
            remote_status.set(
                (remote_tunnel.describe() if enabled else "Remote tunnel disabled.")
                + "\n"
                + auto_refresh.describe()
            )
        except OSError, ValueError, TypeError, AttributeError:
            remote_status.set(
                "Connection or renewal settings need attention. Saved settings were not changed."
            )
        root.after(2000, refresh_remote)

    refresh_remote()
    frame = credentials
    ttk.Label(frame, textvariable=status, wraplength=600).pack(anchor="w", pady=12)

    def save():
        if preview:
            preview_notice(root)
            return False
        try:
            save_settings({k: values[k].get().strip() for k in defaults}, values["token"].get())
            from iphone_detector.discord_setup import set_startup

            set_startup(startup.get())
            values["token"].set("")
            status.set("Saved. Token is encrypted with Windows DPAPI for your Windows account.")
            return True
        except Exception as exc:
            messagebox.showerror("Discord setup", str(exc), parent=root)
            return False

    def install():
        if preview:
            preview_notice(root)
            return False
        from iphone_detector.bridge_service import installed

        if installed():
            status.set(
                "Wi-Fi service is already installed. Subsequent runs do not request administrator approval."
            )
            return
        status.set(
            "Administrator prompts are disabled. The Wi-Fi service must be installed separately by an administrator; no elevation was requested."
        )

    def start():
        if preview:
            preview_notice(root)
            return False
        if not save():
            return
        try:
            start_listener()
        except OSError, ValueError:
            status.set(
                "Windows could not start the bot. Check folder access and try again; settings were preserved."
            )
            return
        status.set(
            "Listener launched. Once connected, use /sideload, /status and /stop in the configured channel. Logs: discord.log."
        )

    def stop():
        if preview:
            preview_notice(root)
            return False
        (DATA_ROOT / "discord-stop").touch()
        status.set(
            "Listener stop requested. An active Discord installation will be cancelled and cleaned up."
        )

    row = ttk.Frame(frame)
    row.pack(fill="x", pady=5)
    ttk.Button(row, text="Save settings", command=save).pack(side="left", padx=(0, 7))
    ttk.Button(row, text="Check Wi-Fi service", command=install).pack(side="left")
    row2 = ttk.Frame(frame)
    row2.pack(fill="x", pady=8)
    ttk.Button(row2, text="Save and start bot", command=start).pack(side="left", padx=(0, 7))
    ttk.Button(row2, text="Stop Discord bot", command=stop).pack(side="left")
    ttk.Label(
        frame,
        text="Invite your bot with bot + applications.commands scopes and View Channel / Send Messages permissions.\nNo Administrator permission or Message Content intent is needed in Discord.",
        wraplength=650,
    ).pack(anchor="w", pady=10)
    if parent is None:
        root.mainloop()
    return root


def start_listener():
    """Discord controls can start before any optional device transport is ready."""
    from iphone_detector.startup_task import start_existing

    if FROZEN and start_existing(command(["--discord"])):
        return None
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    with (DATA_ROOT / "discord-launch.log").open("a", encoding="utf-8") as output:
        return subprocess.Popen(
            command(["--discord"]),
            stdout=output,
            stderr=output,
            creationflags=subprocess.CREATE_NO_WINDOW,
            env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"),
        )


def set_startup(enabled):
    import winreg
    from iphone_detector.startup_task import configure

    configure(enabled, command(["--discord"]))
    from iphone_detector import remote_tunnel

    if remote_tunnel.SETTINGS.exists():
        configure(
            enabled and remote_tunnel.enabled(),
            command(["--remote-daemon"]),
            name="iPhone IPA Community Remote Tunnel",
        )
    startup = (
        Path(os.environ["APPDATA"])
        / "Microsoft/Windows/Start Menu/Programs/Startup/iPhone IPA Community Discord.lnk"
    )
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, "iPhone IPA Community Discord")
        except FileNotFoundError:
            pass
    startup.unlink(missing_ok=True)  # Migrate the old shortcut; one startup entry only.
    saved = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
    saved["startup"] = bool(enabled)
    from iphone_detector.storage import write_json

    write_json(SETTINGS, saved)
