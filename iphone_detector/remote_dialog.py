"""Optional remote setup, with network work outside the Tk event loop."""

import queue
import threading
import tkinter as tk
from tkinter import ttk


class RemoteDialog:
    def __init__(self, parent, *, preview=False):
        self.preview = preview
        self.busy = False
        self.window = tk.Toplevel(parent)
        self.window.title("Remote connection setup")
        self.window.geometry("650x400")
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        from iphone_detector.dialog_ui import shell, section

        panel, _ = shell(
            self.window,
            "Remote connection",
            "Optional: manage your iPhone when you are away from home.",
            width=700,
            height=560,
            close=self.close,
        )
        section(panel, "Before you start")
        ttk.Label(
            panel,
            text="Install Tailscale on both devices and sign in to the same network.\n"
            "Keep the iPhone on home Wi-Fi, unlocked and connected by USB.\n"
            "Enable Wi-Fi sync in Apple Devices or iTunes before setup.\n"
            "Established tunnels can roam; mobile-only cold starts are not guaranteed.",
            wraplength=600,
        ).pack(anchor="w", pady=12)
        ttk.Label(panel, text="iPhone Tailscale IPv4 address (blank detects a single iPhone)").pack(
            anchor="w"
        )
        self.address = tk.StringVar()
        ttk.Entry(panel, textvariable=self.address).pack(fill="x", pady=4)
        self.status = tk.StringVar(value="This verifies pairing; no apps will be installed.")
        ttk.Label(panel, textvariable=self.status, wraplength=600).pack(anchor="w", pady=10)
        self.events = queue.Queue()
        self.button = ttk.Button(
            panel, text="Verify and save remote connection", command=self.start
        )
        self.button.pack(anchor="w")
        self.window.after(200, self.poll)

    def start(self):
        if self.preview:
            self.status.set(
                "Sample result: pairing verified. Wait for Remote tunnel ready before leaving home Wi-Fi. Nothing was saved."
            )
            return
        address = self.address.get().strip() or None
        self.busy = True
        self.button.configure(state="disabled")
        self.status.set("Verifying pairing and VPN listener. Allow up to 90 seconds…")

        def run():
            try:
                from iphone_detector.remote_setup import configure

                configure(address)
                self.events.put(
                    "Remote pairing saved. Wait for “Remote tunnel ready” before leaving home Wi-Fi."
                )
            except Exception as exc:
                self.events.put(
                    str(exc)
                    if isinstance(exc, (ValueError, RuntimeError))
                    else "Remote setup failed ("
                    + type(exc).__name__
                    + "). Check USB trust, Wi-Fi sync and Tailscale, then retry."
                )

        threading.Thread(target=run, daemon=True, name="remote-setup").start()

    def poll(self):
        if not self.window.winfo_exists():
            return
        try:
            self.status.set(self.events.get_nowait())
            self.busy = False
            self.button.configure(state="normal")
        except queue.Empty:
            pass
        self.window.after(200, self.poll)

    def close(self):
        if self.busy:
            self.status.set(
                "Setup is still running. Please keep this window open until it finishes."
            )
            return
        self.window.destroy()
