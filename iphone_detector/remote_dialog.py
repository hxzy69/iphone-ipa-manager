"""Optional remote setup, with network work outside the Tk event loop."""

import queue
import threading
import tkinter as tk
from tkinter import ttk


class RemoteDialog:
    def __init__(self, parent):
        self.window = tk.Toplevel(parent)
        self.window.title("Remote connection setup")
        self.window.geometry("650x400")
        self.window.transient(parent)
        panel = ttk.Frame(self.window, padding=22)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Optional remote connection", font=("Segoe UI", 17, "bold")).pack(
            anchor="w"
        )
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
        address = self.address.get().strip() or None
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
            self.button.configure(state="normal")
        except queue.Empty:
            pass
        self.window.after(200, self.poll)
