"""Explicit renewal preferences; opening the window never toggles the schedule."""

import queue
import threading
import tkinter as tk
from tkinter import ttk

from iphone_detector.dialog_ui import shell, section


class RenewalDialog:
    def __init__(self, parent, *, preview=False):
        self.preview = preview
        self.window = tk.Toplevel(parent)
        self.window.transient(parent)
        panel, footer = shell(
            self.window,
            "Automatic renewal",
            "Keep installed apps renewed without a full upload when possible.",
            height=530,
        )
        section(
            panel,
            "How it works",
            "Checks every six days. If the PC is off, renewal is checked when it starts again. The iPhone must be reachable. A failed or expired refresh may require a full reinstall using the assigned IPA.",
        )
        self.enabled = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Reading the saved schedule…")
        self.toggle = ttk.Checkbutton(
            panel, text="Enable automatic renewal every six days", variable=self.enabled
        )
        self.toggle.pack(anchor="w", pady=12)
        section(panel, "Current schedule")
        ttk.Label(panel, textvariable=self.status, wraplength=620).pack(anchor="w", pady=8)
        ttk.Label(
            panel,
            text="Keep your assigned IPA files available. Review failures in Refresh & history. Changes below take effect only after Save.",
            wraplength=620,
            style="Muted.TLabel",
        ).pack(anchor="w", pady=12)
        self.events = queue.Queue()
        self.save_button = ttk.Button(
            footer, text="Save renewal settings", command=self.save, state="disabled"
        )
        self.save_button.pack(side="left")
        if preview:
            self.enabled.set(True)
            self.status.set("Sample schedule: next refresh in 4 days.")
            self.save_button.configure(state="normal")
        else:
            self.work(False)

    def work(self, saving):
        desired = self.enabled.get()
        self.save_button.configure(state="disabled")
        self.toggle.configure(state="disabled")

        def run():
            try:
                from iphone_detector import auto_refresh

                if saving:
                    auto_refresh.configure(desired)
                enabled = bool(auto_refresh.read(auto_refresh.CONFIG).get("enabled"))
                self.events.put((enabled, auto_refresh.describe(), None))
            except (OSError, ValueError, RuntimeError) as exc:
                self.events.put((None, None, str(exc)))

        threading.Thread(target=run, daemon=True).start()
        self.window.after(100, self.receive)

    def receive(self):
        if not self.window.winfo_exists():
            return
        try:
            enabled, status, error = self.events.get_nowait()
        except queue.Empty:
            self.window.after(100, self.receive)
            return
        self.save_button.configure(state="normal")
        self.toggle.configure(state="normal")
        if error:
            self.status.set("Renewal needs attention: " + error)
        else:
            self.enabled.set(enabled)
            self.status.set(status)

    def save(self):
        if self.preview:
            self.status.set("Preview only. Your actual renewal schedule has not changed.")
            return
        self.status.set("Saving renewal settings…")
        self.work(True)
