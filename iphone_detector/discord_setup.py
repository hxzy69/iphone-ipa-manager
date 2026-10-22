"""Local Discord credentials, startup, and renewal preferences."""

import json
import os
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import ttk, messagebox

from iphone_detector.paths import DATA_ROOT
from iphone_detector.discord_bot import SETTINGS, save_settings, command


def launch():
    root = tk.Tk()
    root.title("iPhone IPA Manager — Private Discord bot")
    root.geometry("710x710")
    root.minsize(650, 690)
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Private Discord control", font=("Segoe UI", 20, "bold")).pack(anchor="w")
    ttk.Label(
        frame,
        text="Only your Discord account in this server and channel can run commands.\nKeep this PC signed in. Establish the remote tunnel on Wi-Fi before leaving home.",
        wraplength=650,
    ).pack(anchor="w", pady=(8, 14))
    saved = json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {}
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
        try:
            set_startup(startup.get())
            status.set(
                "Windows sign-in startup "
                + ("enabled." if startup.get() else "disabled.")
                + " Saved immediately."
            )
        except Exception as exc:
            messagebox.showerror("Windows startup", str(exc), parent=root)

    ttk.Checkbutton(
        frame,
        text="Start the bot when I sign in to Windows",
        variable=startup,
        command=startup_changed,
    ).pack(anchor="w", pady=(10, 0))
    from iphone_detector import remote_tunnel

    remote_enabled = tk.BooleanVar(value=remote_tunnel.enabled())

    def remote_changed():
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

    automatic = tk.BooleanVar(value=bool(auto_refresh.read(auto_refresh.CONFIG).get("enabled")))

    def automatic_changed():
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
    ttk.Label(frame, textvariable=status, wraplength=650).pack(anchor="w", pady=12)

    def save():
        try:
            save_settings({k: values[k].get().strip() for k in defaults}, values["token"].get())
            from iphone_detector.discord_setup import set_startup

            set_startup(startup.get())
            preferences = json.loads(SETTINGS.read_text(encoding="utf-8"))
            preferences["startup"] = startup.get()
            SETTINGS.write_text(json.dumps(preferences, indent=2), encoding="utf-8")
            values["token"].set("")
            status.set("Saved. Token is encrypted with Windows DPAPI for your Windows account.")
            return True
        except Exception as exc:
            messagebox.showerror("Discord setup", str(exc), parent=root)
            return False

    def install():
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
        if not save():
            return
        from iphone_detector.bridge_service import installed

        if not installed():
            status.set("Install the Wi-Fi service first using the button above.")
            return
        output_path = DATA_ROOT / "discord-launch.log"
        with output_path.open("a", encoding="utf-8") as output:
            subprocess.Popen(
                command(["--discord"]),
                stdout=output,
                stderr=output,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        status.set(
            "Listener launched. Once connected, use /sideload, /status and /stop in the configured channel. Logs: discord.log."
        )

    def stop():
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
    ttk.Button(row2, text="Start Discord bot", command=start).pack(side="left", padx=(0, 7))
    ttk.Button(row2, text="Stop Discord bot", command=stop).pack(side="left")
    ttk.Label(
        frame,
        text="Invite your bot with bot + applications.commands scopes and View Channel / Send Messages permissions.\nNo Administrator permission or Message Content intent is needed in Discord.",
        wraplength=650,
    ).pack(anchor="w", pady=10)
    root.mainloop()


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
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    temporary = SETTINGS.with_suffix(".startup.tmp")
    temporary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
    temporary.replace(SETTINGS)
