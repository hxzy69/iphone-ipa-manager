"""Desktop account management, onboarding, and live background-job status."""

import codecs
import ctypes
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from iphone_detector import __version__
from iphone_detector.config import CONFIG_PATH, load_config, save_config
from iphone_detector.paths import ASSET_ROOT, DATA_ROOT, FROZEN
from iphone_detector.settings import get_workflow, update_settings

BACKGROUND = "#101820"
PANEL = "#19252f"
TEXT = "#edf3f7"
MUTED = "#9bafbd"
ACCENT = "#49d8bd"


class App:
    def __init__(self, root):
        self.root = root
        self.process = None
        self.job = None
        self.mode = None
        self.log_offset = 0
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self.closing = False
        self.stop_started = None
        self.config = load_config() or {}
        self.accounts = get_workflow(self.config)
        self.editable = []
        self.root.title(f"iPhone IPA Manager {__version__}")
        icon = ASSET_ROOT / "assets/app.png"
        if icon.is_file():
            self.icon = tk.PhotoImage(file=icon)
            self.root.iconphoto(True, self.icon)
        from iphone_detector.dialog_ui import size_window

        size_window(self.root, 1050, 850, minimum=(820, 640))
        self.root.configure(bg=BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._styles()
        self._build()
        from iphone_detector.gui_sync import fields

        self.sync_baseline = fields(self.config)
        self.sync_conflicts = []
        self.last_sync = 0
        self.live_events = queue.Queue()
        self.live_loading = False
        self.external_active = False
        self._refresh_device()
        from iphone_detector.activation_health import ready

        ready("gui")
        self.root.after(150, self.poll)
        try:
            from iphone_detector.job_queue import ensure_started

            ensure_started()
        except OSError, ValueError:
            self.status.set("Queue needs attention; open Refresh & history.")

    def _styles(self):
        from iphone_detector.dialog_ui import style_window

        style_window(self.root)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=PANEL, foreground=TEXT)
        style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(18, 12), background=PANEL, foreground=MUTED)
        style.map(
            "TNotebook.Tab", background=[("selected", "#2c414f")], foreground=[("selected", TEXT)]
        )
        style.configure("TFrame", background=BACKGROUND)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
        style.configure("Card.TLabel", background=PANEL, foreground=TEXT)
        style.configure("Muted.TLabel", background=BACKGROUND, foreground=MUTED)
        style.configure(
            "TEntry", fieldbackground="#243541", foreground=TEXT, bordercolor="#3a505d", padding=7
        )
        style.map(
            "TEntry",
            fieldbackground=[("disabled", "#1d2a33")],
            foreground=[("disabled", "#8499a6")],
        )
        style.configure(
            "TButton", padding=(13, 8), background="#2c414f", foreground=TEXT, borderwidth=0
        )
        style.map(
            "TButton",
            background=[("active", "#3b5565"), ("disabled", "#1d2a33")],
            foreground=[("disabled", "#728896")],
        )
        style.configure(
            "Accent.TButton", background=ACCENT, foreground="#102620", font=("Segoe UI", 10, "bold")
        )
        style.map("Accent.TButton", background=[("active", "#78e8d2"), ("disabled", "#263d40")])
        style.configure("Stop.TButton", background="#733e43", foreground="#fff1f1")
        style.configure("TCheckbutton", background=BACKGROUND, foreground=MUTED)
        style.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=TEXT)
        style.configure("Treeview.Heading", background="#243541", foreground=TEXT)
        style.map("Treeview", background=[("selected", "#2c414f")], foreground=[("selected", TEXT)])
        style.map("TCheckbutton", background=[("active", BACKGROUND)])

    def _entry(self, parent, variable, **kwargs):
        widget = ttk.Entry(parent, textvariable=variable, **kwargs)
        self.editable.append(widget)
        return widget

    def _button(self, parent, text, command, **kwargs):
        widget = ttk.Button(parent, text=text, command=command, **kwargs)
        self.editable.append(widget)
        return widget

    def _build(self):
        scale = max(1.0, float(self.root.tk.call("tk", "scaling")) / (96 / 72))
        compact = self.root.winfo_screenheight() - 100 < 640 * scale
        shell = ttk.Frame(self.root, padding=(24, 20))
        shell.pack(fill="both", expand=True)
        header = ttk.Frame(shell)
        header.pack(fill="x", pady=(0, 18))
        brand = ttk.Frame(header)
        brand.pack(side="left")
        ttk.Label(brand, text="iPhone IPA Manager", font=("Segoe UI", 23, "bold")).pack(anchor="w")
        ttk.Label(
            brand, text="Your apps. Your accounts. One place to manage them.", style="Muted.TLabel"
        ).pack(anchor="w", pady=(4, 0))
        self.badge = tk.Label(
            header,
            text="READY",
            bg="#203b37",
            fg=ACCENT,
            padx=14,
            pady=7,
            font=("Segoe UI", 9, "bold"),
        )
        self.badge.pack(side="right")
        ttk.Label(header, text=f"v{__version__}", style="Muted.TLabel").pack(side="right", padx=14)

        self.workspace = ttk.Notebook(shell)
        self.workspace.enable_traversal()
        self.workspace.pack(fill="both", expand=True)
        self.page_canvases = []
        pages = {}
        for title in ("Overview", "Accounts & apps", "Connections", "Settings"):
            host = ttk.Frame(self.workspace)
            self.workspace.add(host, text=title)
            if title == "Overview" and not compact:
                page = ttk.Frame(host, padding=(18, 10))
                page.pack(fill="both", expand=True)
                pages[title] = page
                continue
            canvas = tk.Canvas(host, bg=BACKGROUND, highlightthickness=0)
            self.page_canvases.append(canvas)
            scroll = ttk.Scrollbar(host, orient="vertical", command=canvas.yview)
            from iphone_detector.dialog_ui import scrollbar_callback

            canvas.configure(yscrollcommand=scrollbar_callback(scroll, canvas))
            scroll.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            page = ttk.Frame(canvas, padding=(18, 10 if title == "Overview" else 18))
            window = canvas.create_window((0, 0), window=page, anchor="nw")
            page.bind(
                "<Configure>", lambda event, c=canvas: c.configure(scrollregion=c.bbox("all"))
            )
            canvas.bind(
                "<Configure>",
                lambda event, c=canvas, w=window: c.itemconfigure(w, width=event.width),
            )
            from iphone_detector.dialog_ui import reveal_focus

            self.root.bind(
                "<FocusIn>",
                lambda event, c=canvas, p=page: reveal_focus(c, p, event.widget),
                add="+",
            )
            pages[title] = page
        self.root.bind("<MouseWheel>", self._scroll_page, add="+")

        overview = pages["Overview"]
        outer = pages["Accounts & apps"]
        connections = pages["Connections"]
        preferences = pages["Settings"]
        for page, title, description in (
            (
                overview,
                "Keep your apps ready to use",
                "Install replaces apps with your assigned IPAs. Refresh renews existing apps, reinstalling only when needed.",
            ),
            (
                outer,
                "Choose what each account installs",
                "Assignments are shared with Discord and automatic renewal after you save.",
            ),
            (
                connections,
                "Connect your iPhone",
                "Pair once over USB. Configure Wi-Fi and remote access when you need them.",
            ),
            (
                preferences,
                "Make it work your way",
                "Manage renewal, notifications, and transfer preferences.",
            ),
        ):
            ttk.Label(page, text=title, font=("Segoe UI", 17, "bold")).pack(anchor="w")
            ttk.Label(page, text=description, style="Muted.TLabel", wraplength=680).pack(
                anchor="w", pady=(5, 10 if page is overview else 18)
            )
        shortcuts = ttk.Frame(overview)
        shortcuts.pack(fill="x", pady=(0, 10))
        for title, description, command in (
            (
                "Refresh & history",
                "Refresh apps and review queued jobs.",
                self.open_dashboard,
            ),
            (
                "Discord control",
                "Manage your apps through a private bot.",
                self.open_discord_setup,
            ),
            (
                "Help & updates",
                "Check your version and available updates.",
                self.open_updates,
            ),
        ):
            column = len(shortcuts.winfo_children())
            shortcuts.columnconfigure(column, weight=1, uniform="shortcuts")
            card = ttk.Frame(shortcuts, style="Card.TFrame", padding=10)
            card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
            ttk.Button(card, text=title, command=command).pack(fill="x")
            description_label = ttk.Label(
                card, text=description, style="Card.TLabel", wraplength=200
            )
            description_label.pack(fill="x", pady=(6, 0))
            description_label.bind(
                "<Configure>",
                lambda event, label=description_label: label.configure(
                    wraplength=max(100, event.width)
                ),
            )
        account_tools = ttk.Frame(outer)
        account_tools.pack(fill="x", pady=(0, 12))
        self._button(account_tools, "Add Apple account", self.open_account_dialog).pack(side="left")

        folder_row = ttk.Frame(outer)
        folder_row.pack(fill="x")
        ttk.Label(folder_row, text="IPA folder", width=12).pack(side="left")
        automation = self.config.get("automation", {})
        self.folder = tk.StringVar(
            value=automation.get("ipa_directory", str(Path.home() / "Downloads" / "ipas"))
        )
        self._entry(folder_row, self.folder).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._button(folder_row, "Browse folder", self.browse_folder).pack(side="right")
        self._button(folder_row, "Import IPA", self.open_ipa_import).pack(side="right", padx=5)
        health = ttk.Frame(overview)
        health.pack(fill="x", pady=(0, 8))
        self.connection_summary = tk.StringVar(value="Checking remote access…")
        self.renewal_summary = tk.StringVar(value="Checking renewal schedule…")
        for column, (title, variable, command) in enumerate(
            (
                ("Remote access", self.connection_summary, self.open_remote_setup),
                ("Automatic renewal", self.renewal_summary, self.open_renewal),
            )
        ):
            health.columnconfigure(column, weight=1, uniform="health")
            card = ttk.Frame(health, style="Card.TFrame", padding=10)
            card.grid(row=0, column=column, sticky="nsew", padx=(8 if column else 0, 0))
            ttk.Button(card, text=title, command=command).pack(anchor="w")
            label = ttk.Label(card, textvariable=variable, style="Card.TLabel", wraplength=320)
            label.pack(fill="x", pady=(6, 0))
            label.bind(
                "<Configure>",
                lambda event, label=label: label.configure(wraplength=max(100, event.width)),
            )
        self.next_step = tk.StringVar(
            value="Start in Accounts & apps, then pair your iPhone in Connections."
        )
        ttk.Label(overview, textvariable=self.next_step, style="Muted.TLabel", wraplength=700).pack(
            anchor="w", pady=(0, 8)
        )

        setup = ttk.Frame(connections, style="Card.TFrame", padding=10)
        setup.pack(fill="x")
        ttk.Label(
            setup, text="Getting started", style="Card.TLabel", font=("Segoe UI", 11, "bold")
        ).pack(anchor="w")
        ttk.Label(
            setup,
            text="1. Choose an IPA folder in Accounts & apps.   2. Pair your iPhone in Connections.\n"
            "3. Add an Apple account and choose an IPA.   4. Check setup, then Install apps.",
            style="Card.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=6)
        row = ttk.Frame(setup, style="Card.TFrame")
        row.pack(fill="x")
        self._button(row, "Add Apple account", self.open_account_dialog).pack(side="left")
        ttk.Button(row, text="Setup guide", command=self.open_guide).pack(side="left", padx=8)
        ttk.Button(row, text="Setup checklist", command=self.open_checklist).pack(
            side="left", padx=8
        )

        ttk.Button(connections, text="Remote access setup", command=self.open_remote_setup).pack(
            anchor="w", pady=12
        )
        ttk.Button(preferences, text="Automatic renewal settings", command=self.open_renewal).pack(
            anchor="w", pady=(0, 16)
        )
        self.names = []
        self.email_labels = []
        self.account_cards = ttk.Frame(outer)
        self.account_cards.pack(fill="x")
        self.account_empty = ttk.Label(
            self.account_cards,
            text="No accounts yet. Add an Apple account above, then choose its IPA file.",
            style="Muted.TLabel",
            wraplength=650,
        )
        if not self.accounts:
            self.account_empty.pack(anchor="w", pady=20)
        for index, account in enumerate(self.accounts):
            self.add_account_card(index, account)

        options = ttk.Frame(preferences)
        options.pack(fill="x", pady=(0, 10))
        ttk.Label(options, text="Timeout / account").pack(side="left")
        self.timeout = tk.StringVar(value=str(round(automation.get("install_timeout", 900) / 60)))
        self._entry(options, self.timeout, width=4).pack(side="left", padx=(8, 5))
        ttk.Label(options, text="minutes", style="Muted.TLabel").pack(side="left")
        self.exit_after = tk.BooleanVar(value=automation.get("close_gui_after_success", False))
        check = ttk.Checkbutton(
            options, text="Exit app after all configured accounts finish", variable=self.exit_after
        )
        check.pack(side="right")
        self.editable.append(check)

        device = ttk.Frame(connections)
        device.pack(fill="x", pady=(0, 10))
        self.device_label = ttk.Label(device, style="Muted.TLabel")
        self.device_label.pack(side="left", fill="x", expand=True)
        self.device_label.bind(
            "<Configure>",
            lambda event: self.device_label.configure(wraplength=max(120, event.width)),
        )
        self._button(device, "Pair connected iPhone", lambda: self.start("devices")).pack(
            side="right", before=self.device_label
        )

        controls = shell
        if compact:
            controls = ttk.Frame(shell)
            controls.pack(side="bottom", fill="x", before=self.workspace)
        actions = ttk.Frame(controls, padding=(0, 12, 0, 0))
        actions.pack(fill="x")
        self._button(actions, "Save settings", self.save).pack(side="left", padx=(0, 7))
        self._button(actions, "Reload saved", lambda: self.sync_saved(force=True)).pack(
            side="left", padx=(0, 7)
        )
        self._button(actions, "Check setup", lambda: self.start("check")).pack(
            side="left", padx=(0, 7)
        )
        self._button(connections, "Watch for USB connection", lambda: self.start("watch")).pack(
            anchor="w", pady=(8, 0)
        )
        self._button(
            actions, "Install apps", lambda: self.start("run"), style="Accent.TButton"
        ).pack(side="left")
        self.stop_button = ttk.Button(
            actions, text="Stop", command=self.stop, style="Stop.TButton", state="disabled"
        )
        self.stop_button.pack(side="right")
        from iphone_detector.dialog_ui import wrap_actions

        if compact:
            wrap_actions(actions)
        wireless = ttk.Frame(connections)
        wireless.pack(fill="x", pady=(8, 0))
        self._button(wireless, "Check Wi-Fi", lambda: self.start("wifi-check")).pack(
            side="left", padx=(0, 7)
        )
        self._button(wireless, "Run over Wi-Fi", lambda: self.start("wifi-run")).pack(
            side="left", padx=(0, 12)
        )
        self.wifi_fast = tk.BooleanVar(value=automation.get("wifi_archive_transfer", True))
        fast = ttk.Checkbutton(wireless, text="Fast Wi-Fi transfer", variable=self.wifi_fast)
        fast.pack(side="left", padx=(0, 12))
        self.editable.append(fast)
        ttk.Label(
            connections,
            text="USB pairing first. Uses the installed Wi-Fi service; no administrator prompts.",
            style="Muted.TLabel",
            wraplength=650,
        ).pack(anchor="w", pady=12)
        background = ttk.Frame(preferences)
        background.pack(fill="x", pady=(7, 0))
        self.notify_finish = tk.BooleanVar(value=automation.get("notify_on_finish", True))
        for title, variable in [("Windows notification when finished", self.notify_finish)]:
            checkbox = ttk.Checkbutton(background, text=title, variable=variable)
            checkbox.pack(side="left", padx=(0, 18))
            self.editable.append(checkbox)
        self.status = tk.StringVar(
            value="Choose an IPA folder to get started."
            if not self.accounts
            else "Choose a new IPA or edit its filename, then save."
        )
        status_label = ttk.Label(controls, textvariable=self.status, style="Muted.TLabel")
        status_label.pack(fill="x", pady=(11, 7))
        status_label.bind(
            "<Configure>", lambda event: status_label.configure(wraplength=max(200, event.width))
        )
        self.sync_status = tk.StringVar(value="Live sync enabled.")
        sync_label = ttk.Label(controls, textvariable=self.sync_status, style="Muted.TLabel")
        sync_label.pack(fill="x", pady=(0, 5))
        sync_label.bind(
            "<Configure>", lambda event: sync_label.configure(wraplength=max(200, event.width))
        )
        self.activity = tk.Text(
            overview,
            height=2,
            bg="#0b1219",
            fg="#c4d4df",
            relief="flat",
            font=("Consolas", 9),
            padx=10,
            pady=8,
            state="disabled",
            wrap="word",
        )
        self.activity.pack(fill="both", expand=True)
        footer = ttk.Frame(preferences)
        footer.pack(fill="x", pady=(7, 0))
        ttk.Button(
            footer,
            text="Open activity logs",
            command=lambda: self.open_folder(DATA_ROOT / "automation-runs"),
        ).pack(side="left")
        ttk.Button(
            footer,
            text="Open settings folder",
            command=lambda: self.open_folder(CONFIG_PATH.parent),
        ).pack(side="left", padx=7)
        ttk.Label(
            preferences,
            text="Stop requests safe cancellation of the active job.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=12)

    def _scroll_page(self, event):
        """Scroll the page under the pointer without stealing text-widget scrolling."""
        widget = event.widget
        if isinstance(widget, (tk.Text, ttk.Combobox, ttk.Spinbox)):
            return
        while widget is not None:
            if widget in self.page_canvases:
                bounds = widget.bbox("all")
                if bounds and bounds[3] > widget.winfo_height():
                    units = int(event.delta / 120) or (
                        1 if event.delta > 0 else -1 if event.delta < 0 else 0
                    )
                    widget.yview_scroll(-units, "units")
                return
            widget = getattr(widget, "master", None)

    def add_account_card(self, index, account):
        empty = getattr(self, "account_empty", None)
        if empty is not None:
            empty.pack_forget()
        card = ttk.Frame(self.account_cards, style="Card.TFrame", padding=(15, 11))
        card.pack(fill="x", pady=(10, 0))
        line = ttk.Frame(card, style="Card.TFrame")
        line.pack(fill="x")
        ttk.Label(
            line, text="ACCOUNT %d" % (index + 1), style="Card.TLabel", font=("Segoe UI", 9, "bold")
        ).pack(side="left")
        email_label = ttk.Label(line, text=account["email"], style="Card.TLabel", foreground=MUTED)
        email_label.pack(side="left", fill="x", expand=True, padx=(12, 0))
        email_label.bind(
            "<Configure>", lambda event: email_label.configure(wraplength=max(120, event.width))
        )
        self.email_labels.append(email_label)
        fields = ttk.Frame(card, style="Card.TFrame")
        fields.pack(fill="x", pady=(7, 0))
        name = tk.StringVar(value=account["ipa"] + ".ipa")
        self.names.append(name)
        self._entry(fields, name).pack(side="left", fill="x", expand=True, padx=(0, 8))
        self._button(fields, "Choose IPA", lambda i=index: self.browse_ipa(i)).pack(side="right")

    def _refresh_device(self):
        device = self.config.get("device", {})
        self.device_label.configure(
            text=("Selected: %s  ·  %s" % (device.get("name", "iPhone"), device["serial"]))
            if device.get("serial")
            else "No iPhone selected. Connect by USB, then choose Pair connected iPhone."
        )
        if hasattr(self, "next_step"):
            if not self.accounts:
                hint = "Next: add an Apple account in Accounts & apps, then assign an IPA."
            elif not device.get("serial"):
                hint = "Next: open Connections to pair your iPhone over USB."
            else:
                name = str(device.get("name", "iPhone"))
                display_name = name if len(name) <= 50 else name[:47] + "…"
                hint = f"{display_name} selected · {len(self.accounts)} account(s). Check setup before your first install."
            self.next_step.set(hint)

    def sync_variables(self):
        result = {
            key: getattr(self, key)
            for key in (
                "folder",
                "timeout",
                "exit_after",
                "wifi_fast",
                "notify_finish",
            )
        }
        result.update({"ipa%d" % index: value for index, value in enumerate(self.names)})
        return result

    def sync_saved(self, force=False, latest=None):
        from iphone_detector.gui_sync import fields, merge

        latest = (load_config() or {}) if latest is None else latest
        remote = fields(latest)
        workflow = get_workflow(latest)
        if len(workflow) < len(self.names):
            raise ValueError(
                "Accounts were removed externally. Reopen the GUI to reload its account list safely."
            )
        for index in range(len(self.names), len(workflow)):
            self.add_account_card(index, workflow[index])
            self.sync_baseline["ipa%d" % index] = remote["ipa%d" % index]
        variables = self.sync_variables()
        if force:
            values, baseline, conflicts = remote, remote.copy(), []
        else:
            values, baseline, conflicts = merge(
                self.sync_baseline, {key: value.get() for key, value in variables.items()}, remote
            )
        for key, value in values.items():
            if variables[key].get() != value:
                variables[key].set(value)
        self.sync_baseline, self.sync_conflicts = baseline, conflicts
        self.config, self.accounts = latest, get_workflow(latest)
        for label, account in zip(self.email_labels, self.accounts):
            label.configure(text=account["email"])
        self._refresh_device()
        return conflicts

    def sync_live(self):
        try:
            latest, active, detail, connection, renewal, error = self.live_events.get_nowait()
        except queue.Empty:
            if self.live_loading or time.monotonic() - self.last_sync < 1:
                return
            self.last_sync = time.monotonic()
            self.live_loading = True
            events = self.live_events

            def collect():
                try:
                    from iphone_detector import auto_refresh, remote_tunnel
                    from iphone_detector.worker_registry import active_jobs
                    from iphone_detector.discord_bot import Controller

                    latest = load_config() or {}
                    active = active_jobs(DATA_ROOT)
                    detail = "No active run."
                    if active:
                        progress = Controller()
                        progress.job = active[0]["job"]
                        progress.expected = len(active[0]["accounts"])
                        progress.state = "Preparing background run."
                        progress.accounts = (
                            [number - 1 for number in active[0]["accounts"]]
                            if len(active[0]["accounts"]) < len(get_workflow(latest))
                            else None
                        )
                        detail = "Active: %s. %s" % (active[0]["mode"], progress.progress())
                    events.put(
                        (
                            latest,
                            active,
                            detail,
                            remote_tunnel.describe(),
                            auto_refresh.describe(),
                            None,
                        )
                    )
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    events.put((None, None, None, None, None, str(exc)[:160]))

            threading.Thread(target=collect, daemon=True).start()
            return
        self.live_loading = False
        try:
            if error:
                raise ValueError(error)
            self.sync_saved(latest=latest)
            from iphone_detector.gui_sync import conflict_names

            dirty = any(
                variable.get() != self.sync_baseline.get(key)
                for key, variable in self.sync_variables().items()
            )
            prefix = (
                (
                    "Conflicting saved changes: %s. Use Reload saved to accept saved values. "
                    % conflict_names(self.sync_conflicts)
                )
                if self.sync_conflicts
                else "Unsaved changes — choose Save settings to share them with Discord. "
                if dirty
                else "Saved settings are up to date. "
            )
            self.sync_status.set(prefix + detail)
            self.connection_summary.set(connection)
            self.renewal_summary.set(renewal)
            if self.process is None:
                self.stop_button.configure(state="normal" if active else "disabled")
                if active:
                    self.badge.configure(text="RUNNING", fg=ACCENT, bg="#203b37")
                elif self.external_active:
                    self.badge.configure(text="READY", fg=ACCENT, bg="#203b37")
                self.external_active = bool(active)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.connection_summary.set("Status unavailable. Open Connections and check setup.")
            self.renewal_summary.set("Schedule could not be verified. Open renewal settings.")
            self.sync_status.set(
                "Live sync unavailable; unsaved edits are preserved. " + str(exc)[:160]
            )

    def open_checklist(self):
        from iphone_detector.readiness import inspect, format_report

        dialog = tk.Toplevel(self.root)
        dialog.title("Setup and recovery checklist")
        dialog.geometry(
            "%dx%d"
            % (
                min(760, self.root.winfo_screenwidth() - 60),
                min(680, self.root.winfo_screenheight() - 100),
            )
        )
        from iphone_detector.dialog_ui import shell

        frame, footer = shell(
            dialog,
            "Setup & recovery checklist",
            "Read-only checks: identify missing setup before installing apps.",
            height=650,
        )
        text = tk.Text(
            frame,
            height=18,
            wrap="word",
            font=("Segoe UI", 10),
            padx=10,
            pady=10,
            bg=PANEL,
            fg=TEXT,
            relief="flat",
        )
        scroll = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        results = queue.Queue()

        def poll():
            if not dialog.winfo_exists():
                return
            try:
                value = results.get_nowait()
            except queue.Empty:
                dialog.after(100, poll)
                return
            text.configure(state="normal")
            text.delete("1.0", "end")
            text.insert("end", value)
            text.configure(state="disabled")
            refresh.configure(state="normal")

        def load():
            if getattr(self, "preview", False):
                text.configure(state="normal")
                text.delete("1.0", "end")
                text.insert(
                    "end",
                    "SAMPLE CHECKLIST — no device checks performed\n\nReady · IPA folder configured\nReady · Three accounts assigned\nReady · iPhone pairing saved\nReady · Remote tunnel available\n\nIf a check needs attention, fix it before starting an install.",
                )
                text.configure(state="disabled")
                return
            refresh.configure(state="disabled")

            def collect():
                try:
                    results.put(format_report(inspect()))
                except Exception:
                    results.put(
                        "The checklist could not finish. Save a support report from About and updates."
                    )

            threading.Thread(target=collect, daemon=True).start()
            dialog.after(100, poll)

        refresh = ttk.Button(footer, text="Refresh checklist", command=load)
        refresh.pack(anchor="w", pady=8)
        load()

    def open_discord_setup(self):
        from iphone_detector.discord_setup import launch

        existing = getattr(self, "discord_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        try:
            self.discord_dialog = launch(self.root, preview=getattr(self, "preview", False))
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Discord settings need attention", str(exc), parent=self.root)

    def open_guide(self):
        from iphone_detector.paths import ASSET_ROOT

        os.startfile(ASSET_ROOT / "docs" / "START_HERE.html")

    def open_account_dialog(self):
        if not self.save():
            return
        try:
            from iphone_detector.account_dialog import AccountDialog

            def saved(number):
                self.sync_saved(force=True)
                self.status.set(
                    "Account %d added. Check setup, then Install apps to install its IPA." % number
                )

            self.account_dialog = AccountDialog(self.root, self.config, saved)
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror("Account setup", str(exc), parent=self.root)

    def open_renewal(self):
        from iphone_detector.renewal_dialog import RenewalDialog

        self.renewal_dialog = RenewalDialog(self.root, preview=getattr(self, "preview", False))

    def open_remote_setup(self):
        from iphone_detector.remote_dialog import RemoteDialog

        self.remote_dialog = RemoteDialog(self.root, preview=getattr(self, "preview", False))

    def browse_folder(self):
        path = filedialog.askdirectory(
            parent=self.root,
            initialdir=self.folder.get(),
            title="Folder containing the configured IPAs",
        )
        if path:
            self.folder.set(path)

    def open_ipa_import(self):
        """Optionally import a file from another folder into the managed IPA folder."""
        from iphone_detector.ipa_inbox import import_assignment

        if not self.accounts:
            messagebox.showinfo(
                "Add an account first",
                "Copy an IPA to your folder, then add your first Apple account.",
                parent=self.root,
            )
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Import IPA")
        dialog.geometry("700x420")
        dialog.transient(self.root)
        dialog.grab_set()
        from iphone_detector.dialog_ui import shell

        body, footer = shell(
            dialog,
            "Import & assign an IPA",
            "Choose a completed file, then select the account that will manage it. Importing does not install the app.",
            width=760,
            height=610,
        )
        inbox = tk.StringVar(
            value=self.config.get("automation", {}).get(
                "import_directory", str(Path.home() / "Downloads")
            )
        )
        row = ttk.Frame(body, padding=(0, 8))
        row.pack(fill="x")
        ttk.Label(row, text="Source folder").pack(anchor="w")
        ttk.Entry(row, textvariable=inbox).pack(fill="x")

        def choose_folder():
            if getattr(self, "preview", False):
                return
            selected = filedialog.askdirectory(parent=dialog, initialdir=inbox.get())
            if selected:
                inbox.set(selected)
                refresh()

        ttk.Button(row, text="Choose source folder", command=choose_folder).pack(anchor="w")
        files = tk.Listbox(
            body,
            height=7,
            exportselection=False,
            bg=PANEL,
            fg=TEXT,
            selectbackground="#2c414f",
            relief="flat",
        )
        files.pack(fill="both", expand=True, padx=12)
        paths = []

        def refresh():
            if getattr(self, "preview", False):
                if not paths:
                    paths.extend([Path("Sample Video.ipa"), Path("Sample Music.ipa")])
                    for path in paths:
                        files.insert("end", path.name)
                return
            selected = files.get(files.curselection()[0]) if files.curselection() else None
            try:
                found = sorted(
                    (
                        p
                        for p in Path(inbox.get()).glob("*")
                        if p.is_file() and p.suffix.lower() == ".ipa"
                    ),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
            except OSError:
                found = []
            if found != paths:
                paths[:] = found
                files.delete(0, "end")
                for path in paths:
                    files.insert("end", path.name)
                for i, path in enumerate(paths):
                    if path.name == selected:
                        files.selection_set(i)

        ttk.Label(body, text="Assign to account").pack(anchor="w", pady=(12, 4))
        account = ttk.Combobox(
            body,
            state="readonly",
            values=[
                "Account %d — %s" % (index + 1, entry["email"])
                for index, entry in enumerate(self.accounts)
            ],
        )
        account.pack(fill="x")
        account.current(0)

        import_events = queue.Queue()
        import_cancelled = threading.Event()
        importing = [False]

        def close_import():
            import_cancelled.set()
            dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close_import)
        dialog.bind("<Escape>", lambda event: close_import())
        import_status = tk.StringVar(value="Choose a completed IPA to import.")
        ttk.Label(body, textvariable=import_status, wraplength=650).pack(anchor="w", pady=10)
        for child in footer.winfo_children():
            if isinstance(child, ttk.Button):
                child.configure(command=close_import)

        def assign():
            if getattr(self, "preview", False):
                import_status.set("Sample assignment only. No file was copied or settings changed.")
                return
            if importing[0]:
                return
            if not files.curselection():
                messagebox.showinfo(
                    "Select an IPA",
                    "Wait for the file transfer to finish, then select its IPA.",
                    parent=dialog,
                )
                return
            source = paths[files.curselection()[0]]
            chosen = account.current()
            if not self.save():
                return
            folder = self.folder.get()
            chosen_account = self.accounts[chosen].copy()
            source_folder = inbox.get()
            try:
                stat = source.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
            except OSError:
                import_status.set("The source file disappeared. Select a completed IPA again.")
                return
            importing[0] = True
            import_status.set("Copying and verifying IPA…")

            def copy():
                try:
                    target = import_assignment(
                        source,
                        folder,
                        chosen,
                        chosen_account,
                        signature,
                        source_directory=source_folder,
                        cancelled=import_cancelled,
                    )
                    import_events.put((target, None))
                except (OSError, ValueError, RuntimeError) as exc:
                    import_events.put((None, str(exc)))

            threading.Thread(target=copy, daemon=True).start()

            def finish():
                if not dialog.winfo_exists():
                    return
                try:
                    target, error = import_events.get_nowait()
                except queue.Empty:
                    dialog.after(100, finish)
                    return
                importing[0] = False
                if error:
                    import_status.set(error)
                    return
                try:
                    self.sync_saved()
                except OSError, ValueError:
                    import_status.set(
                        "The IPA assignment was saved. Reopen the app to reload settings; the imported file was preserved."
                    )
                    return
                self.status.set(
                    "IPA imported for account %d. Run sideload to install it." % (chosen + 1)
                )
                dialog.destroy()

            dialog.after(100, finish)

        ttk.Button(footer, text="Import and assign", command=assign).pack(side="left")

        def poll_inbox():
            if dialog.winfo_exists():
                refresh()
                dialog.after(3000, poll_inbox)

        poll_inbox()

    def browse_ipa(self, index):
        path = filedialog.askopenfilename(
            parent=self.root,
            initialdir=self.folder.get(),
            title="IPA for account %d" % (index + 1),
            filetypes=[("IPA file", "*.ipa")],
        )
        if path:
            self.names[index].set(Path(path).name)
            self.folder.set(str(Path(path).parent))
            self.status.set(
                "Account %d updated. The IPA folder now matches the selected file." % (index + 1)
            )

    def save(self):
        from iphone_detector.run_lock import RunLock

        guard = RunLock()
        try:
            if not guard.acquire():
                raise RuntimeError(
                    "An installation or refresh is running. Save settings after it finishes."
                )
            latest = load_config() or self.config
            conflicts = self.sync_saved(latest=latest)
            if conflicts:
                from iphone_detector.gui_sync import conflict_names

                raise ValueError(
                    "These fields changed both here and elsewhere: %s. Use Reload saved to accept saved values, or edit them to match before saving."
                    % conflict_names(conflicts)
                )
            updated = update_settings(
                self.config,
                self.folder.get(),
                [name.get() for name in self.names],
                self.timeout.get(),
                self.exit_after.get(),
            )
            updated.setdefault("automation", {})["wifi_archive_transfer"] = self.wifi_fast.get()
            updated["automation"]["background_mode"] = True
            updated["automation"]["notify_on_finish"] = self.notify_finish.get()
            save_config(updated)
            self.config = updated
            self.accounts = get_workflow(updated)
            from iphone_detector.gui_sync import fields

            saved_fields = fields(updated)
            variables = self.sync_variables()
            for key, value in saved_fields.items():
                variables[key].set(value)
            self.sync_baseline = saved_fields
            self.sync_conflicts = []
            self.status.set("Saved. New filenames will be used on the next run.")
            return True
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror("Settings need attention", str(exc), parent=self.root)
            return False
        finally:
            guard.close()

    def append(self, text):
        self.activity.configure(state="normal")
        self.activity.insert("end", text)
        if int(self.activity.index("end-1c").split(".")[0]) > 1200:
            self.activity.delete("1.0", "200.0")
        self.activity.see("end")
        self.activity.configure(state="disabled")

    def _busy(self, busy):
        for widget in self.editable:
            widget.configure(state="disabled" if busy else "normal")
        self.stop_button.configure(state="normal" if busy else "disabled")

    def start(self, mode):
        if self.process is not None:
            return
        if mode != "devices" and not self.save():
            return
        if mode not in ("devices",) and not self.accounts:
            messagebox.showinfo(
                "Add an account first",
                "Add an Apple account and choose its IPA before starting.",
                parent=self.root,
            )
            return
        if mode in ("watch", "wifi-check", "wifi-run") and not self.config.get("device", {}).get(
            "serial"
        ):
            messagebox.showinfo(
                "Pair an iPhone first",
                "Connect the iPhone, then click Pair connected iPhone.",
                parent=self.root,
            )
            return
        self.job = (
            DATA_ROOT / "automation-runs" / ("gui-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
        )
        self.job.mkdir(parents=True)
        command = (
            [sys.executable]
            if FROZEN
            else [sys.executable, str(Path(__file__).resolve().parent.parent / "ipa_manager.py")]
        )
        command += ["--worker", mode, "--job-dir", str(self.job)]
        try:
            self.process = subprocess.Popen(
                command, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        except OSError as exc:
            messagebox.showerror("Could not start", str(exc), parent=self.root)
            return
        self.mode, self.log_offset, self.stop_started = mode, 0, None
        self.decoder = codecs.getincrementaldecoder("utf-8")("replace")
        self._busy(True)
        self.badge.configure(
            text="WAITING" if mode == "watch" else "WORKING", fg=ACCENT, bg="#203b37"
        )
        self.status.set(
            {
                "run": "Running configured accounts. Stop requests safe cleanup.",
                "watch": "Waiting for the paired iPhone. A connected phone starts immediately.",
                "check": "Checking saved settings and IPA archives. No device changes.",
                "wifi-check": "Checking the installed Wi-Fi bridge and device connection without installing apps.",
                "wifi-run": "Running configured accounts over Wi-Fi. Stop requests safe cleanup.",
                "devices": "Scanning connected Apple mobile devices…",
            }[mode]
        )
        if mode in ("run", "watch", "wifi-run", "wifi-check"):
            self.stop_button.configure(text="Stop")
            self.status.set(
                "Background mode: you can use your PC normally. Stop cancels the job."
                + (
                    " Wi-Fi requires the separately installed bridge service."
                    if mode.startswith("wifi")
                    else ""
                )
            )
        else:
            self.stop_button.configure(text="Stop")
        self.append("\n── %s ──\n" % self.status.get())

    def stop(self):
        if self.process is None:
            from iphone_detector.worker_registry import stop_active

            count = stop_active(DATA_ROOT)
            self.status.set("Safe cancellation requested for %d background run(s)." % count)
            return
        if self.process is not None and self.stop_started is None:
            (self.job / "stop").touch()
            self.stop_started = time.monotonic()
            self.status.set("Stopping safely… waiting for the current device operation to finish.")
            self.badge.configure(text="STOPPING", fg="#ffcf88", bg="#413424")
            self.stop_button.configure(state="disabled")

    def _read_log(self):
        log = self.job / "activity.log"
        if log.exists():
            if log.stat().st_size < self.log_offset:
                self.log_offset = 0
                self.decoder.reset()
            with log.open("rb") as handle:
                handle.seek(self.log_offset)
                data = handle.read(128 * 1024)
                self.log_offset = handle.tell()
            if data:
                self.append(self.decoder.decode(data))

    def poll(self):
        from iphone_detector.guided_update import STATE
        from iphone_detector.job_history import read

        update = read(STATE)
        if (
            update.get("phase") == "close_gui"
            and type(update.get("at")) in (int, float)
            and time.time() - update.get("at", 0) < 120
            and self.process is None
        ):
            self.root.destroy()
            return
        self.sync_live()
        if self.process is not None:
            self._read_log()
            code = self.process.poll()
            if code is None and self.stop_started and time.monotonic() - self.stop_started > 45:
                self.process.terminate()
                self.append(
                    "Worker did not respond in 45 seconds; it was stopped. Inspect the device before retrying.\n"
                )
            elif code is not None:
                self._read_log()
                mode, job = self.mode, self.job
                self.process = None
                self._busy(False)
                result_path = job / "result.json"
                result = (
                    json.loads(result_path.read_text(encoding="utf-8"))
                    if result_path.exists()
                    else {}
                )
                if self.closing:
                    self.root.destroy()
                    return
                if mode == "devices" and code == 0:
                    self.choose_device(result.get("devices", []))
                elif code == 0:
                    self.badge.configure(
                        text="PASSED" if mode in ("check", "wifi-check") else "COMPLETE",
                        fg=ACCENT,
                        bg="#203b37",
                    )
                    self.status.set(
                        "Wi-Fi check passed. Apple service restored. Ready for Run over Wi-Fi."
                        if mode == "wifi-check"
                        else "Setup check passed."
                        if mode == "check"
                        else "All requested accounts completed."
                    )
                    if mode in ("run", "watch", "wifi-run") and self.exit_after.get():
                        self.root.after(900, self.root.destroy)
                        return
                elif code == 130 or self.stop_started is not None:
                    self.badge.configure(text="STOPPED", fg="#ffcf88", bg="#413424")
                    self.status.set("Stopped. You can edit settings or start again.")
                else:
                    self.badge.configure(text="NEEDS ATTENTION", fg="#ffb4ba", bg="#472a32")
                    self.status.set(
                        result.get(
                            "error", "The worker stopped unexpectedly. See the activity log."
                        )
                    )
                self.stop_started = None
        self.root.after(150, self.poll)

    def open_dashboard(self):
        from iphone_detector.management_dialog import ManagementDialog

        previous = getattr(self, "management", None)
        if previous is not None and previous.window.winfo_exists():
            previous.window.lift()
        else:
            self.management = ManagementDialog(self, preview=getattr(self, "preview", False))

    def open_updates(self):
        from iphone_detector.updates import UpdateError, check_updates

        existing = getattr(self, "updates_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            return
        dialog = self.updates_dialog = tk.Toplevel(self.root)
        dialog.title("About and updates")
        dialog.transient(self.root)
        dialog.configure(bg=BACKGROUND)
        from iphone_detector.dialog_ui import shell, section

        frame, _ = shell(
            dialog,
            "About & updates",
            "Review the version, install verified updates, or collect a support report.",
            width=700,
            height=730,
        )
        ttk.Label(
            frame, text=f"iPhone IPA Manager {__version__}", font=("Segoe UI", 16, "bold")
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text="Open source · GPL-3.0-or-later\nBuilt on iloader; remote refresh research informed by JAS.",
            wraplength=520,
        ).pack(anchor="w", pady=12)
        from iphone_detector.guided_update import STATE
        from iphone_detector.job_history import read

        previous_update = {} if getattr(self, "preview", False) else read(STATE)
        status = tk.StringVar(
            value=previous_update.get(
                "message", "Check the official GitHub repository for a newer release."
            )
        )
        ttk.Label(frame, textvariable=status, wraplength=520).pack(anchor="w", pady=8)
        ttk.Label(
            frame,
            text="Checks contact GitHub only when requested. No account settings or logs are sent.\n\n"
            "Download verifies the release before activation. Activation requires idle workers and no waiting queue. "
            "It closes the old GUI, restarts background components and migrates existing startup tasks. "
            "Saved settings and the previous executable stay in place. A restarted tunnel may need home Wi-Fi.",
            style="Muted.TLabel",
            wraplength=520,
        ).pack(anchor="w", pady=12)
        results = queue.Queue()
        section(frame, "1. Find a release")
        release_button = ttk.Button(frame, text="Open release and downloads", state="disabled")
        release_button.pack(anchor="w", pady=8)

        def worker():
            try:
                results.put((check_updates(), None))
            except UpdateError as exc:
                results.put((None, str(exc)))
            except Exception:
                results.put((None, "The update check could not finish. Try again later."))

        def receive():
            if not dialog.winfo_exists():
                return
            try:
                release, error = results.get_nowait()
            except queue.Empty:
                self.root.after(100, receive)
                return
            check.configure(state="normal")
            status.set(error or release.summary())
            if release:
                import webbrowser

                release_button.configure(
                    state="normal", command=lambda: webbrowser.open(release.url)
                )
                if release.available and FROZEN:
                    prepare_button.configure(state="normal", command=lambda: prepare(release))

        def start_check():
            if getattr(self, "preview", False):
                status.set(
                    "Sample result: you are viewing the design preview. No update check was sent."
                )
                return
            check.configure(state="disabled")
            release_button.configure(state="disabled")
            status.set("Checking GitHub…")
            threading.Thread(target=worker, daemon=True).start()
            self.root.after(100, receive)

        check = ttk.Button(frame, text="Check for updates", command=start_check)
        check.pack(anchor="w", pady=8)
        section(frame, "2. Download and verify", "Available after a newer release is found.")
        prepare_button = ttk.Button(frame, text="Download and verify update", state="disabled")
        prepare_button.pack(anchor="w", pady=8)
        section(
            frame,
            "3. Activate when idle",
            "Save your work first. Background components will restart.",
        )
        activate_button = ttk.Button(frame, text="Activate prepared update", state="disabled")
        activate_button.pack(anchor="w", pady=8)
        prepared = queue.Queue()

        def prepare(release):
            prepare_button.configure(state="disabled")
            status.set("Downloading and verifying the Windows release and matching source…")

            def run():
                from iphone_detector.guided_update import prepare

                try:
                    prepared.put((prepare(release), None))
                except Exception:
                    prepared.put(
                        (
                            None,
                            "Download or verification failed. No running processes were changed.",
                        )
                    )

            threading.Thread(target=run, daemon=True).start()
            self.root.after(100, receive_prepared)

        def receive_prepared():
            if not dialog.winfo_exists():
                return
            try:
                plan, error = prepared.get_nowait()
            except queue.Empty:
                self.root.after(100, receive_prepared)
                return
            prepare_button.configure(state="normal")
            status.set(
                error
                or "Release verified. Activation closes this GUI and restarts background components. The tunnel may need home Wi-Fi."
            )
            if plan:
                activate_button.configure(state="normal", command=lambda: activate(plan))

        def activate(plan):
            from iphone_detector.guided_update import start

            try:
                start(plan)
                status.set("Waiting for active-work checks and background shutdown…")
                activate_button.configure(state="disabled")
                self.root.after(300, activation_status)
            except (OSError, ValueError) as exc:
                status.set(str(exc))

        def activation_status():
            if not dialog.winfo_exists():
                return
            state = read(STATE)
            if state.get("phase") in ("failed", "repair"):
                status.set(
                    state.get(
                        "message",
                        "Activation needs attention; the previous executable is preserved.",
                    )
                )
                activate_button.configure(state="normal")
                return
            if state.get("phase") == "complete":
                status.set("Update activated.")
                return
            self.root.after(300, activation_status)

        from iphone_detector.guided_update import PREVIOUS

        if not getattr(self, "preview", False) and PREVIOUS.is_file() and FROZEN:
            ttk.Button(
                frame, text="Roll back to previous executable", command=lambda: activate(PREVIOUS)
            ).pack(anchor="w", pady=8)
        ttk.Button(frame, text="Save support report", command=self.export_support_report).pack(
            anchor="w", pady=8
        )

    def export_support_report(self):
        from iphone_detector.diagnostics import report

        path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save support report (no credentials or raw logs)",
            initialfile="ipa-manager-support.json",
            defaultextension=".json",
            filetypes=[("JSON report", "*.json")],
        )
        if not path:
            return
        try:
            Path(path).write_text(json.dumps(report(), indent=2) + "\n", encoding="utf-8")
        except OSError:
            messagebox.showerror(
                "Support report",
                "Could not save the report. Choose a writable folder.",
                parent=self.root,
            )
            return
        self.status.set(
            "Support report saved. It contains health flags, with no accounts, credentials, paths or raw logs."
        )

    def choose_device(self, devices):
        if not devices:
            self.badge.configure(text="NO PHONE", fg="#ffcf88", bg="#413424")
            self.status.set("No iPhone found. Connect it with a data cable and scan again.")
            return

        def save_device(device):
            from iphone_detector.run_lock import RunLock

            guard = RunLock()
            try:
                if not guard.acquire():
                    raise RuntimeError("A run is active. Pair after it finishes.")
                latest = load_config() or {}
                latest["device"] = dict(device)
                save_config(latest)
                self.sync_saved(latest=latest)
            except (OSError, ValueError, RuntimeError) as exc:
                messagebox.showerror("Could not save pairing", str(exc), parent=self.root)
                return
            finally:
                guard.close()
            self.status.set(
                "iPhone selected. Approve Trust on the phone and complete Apple USB pairing before installing."
            )
            self.badge.configure(text="SELECTED", fg=ACCENT, bg="#203b37")

        if len(devices) == 1:
            save_device(devices[0])
        else:
            dialog = tk.Toplevel(self.root)
            dialog.title("Choose your iPhone")
            dialog.transient(self.root)
            dialog.grab_set()
            from iphone_detector.dialog_ui import shell

            body, _ = shell(
                dialog,
                "Choose your iPhone",
                "Select the phone to use for installs and renewal. Approve Trust on the phone if prompted.",
                width=700,
                height=450,
            )
            for device in devices:

                def select(value=device):
                    if not getattr(self, "preview", False):
                        save_device(value)
                    dialog.destroy()

                ttk.Button(
                    body, text="%s · %s" % (device["name"], device["serial"]), command=select
                ).pack(fill="x", padx=15, pady=8)

    def close(self):
        if self.process is not None:
            self.closing = True
            self.stop()
        else:
            self.root.destroy()

    def open_folder(self, path):
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(path)


def launch():
    root = tk.Tk()
    # Keep two GUI instances from starting competing automation workers.
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    mutex = kernel.CreateMutexW(None, False, "Local\\iPhoneIPAManagerCommunityGUI")
    if ctypes.get_last_error() == 183:
        root.withdraw()
        messagebox.showinfo(
            "iPhone IPA Manager", "The app is already open. Use the existing window."
        )
        root.destroy()
        return 0
    try:
        try:
            App(root)
        except ValueError as exc:
            root.withdraw()
            from iphone_detector.maintenance import quarantine_invalid_config

            if messagebox.askyesno(
                "Settings need repair",
                str(exc)
                + "\n\nPreserve the unreadable settings in a local backup and open a fresh setup? Device pairing and saved credentials will be kept.",
                parent=root,
            ):
                try:
                    quarantine_invalid_config()
                    root.deiconify()
                    App(root)
                except (OSError, ValueError) as repair_error:
                    messagebox.showerror("Repair unavailable", str(repair_error), parent=root)
                    root.destroy()
                    return 1
            else:
                root.destroy()
                return 1
        root.mainloop()
    finally:
        if mutex:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)
    return 0
