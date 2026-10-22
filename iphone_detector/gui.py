"""Desktop account management, onboarding, and live background-job status."""

import codecs
import ctypes
import json
import os
import subprocess
import sys
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

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
        self.root.title("iPhone IPA Manager")
        icon = ASSET_ROOT / "assets/app.png"
        if icon.is_file():
            self.icon = tk.PhotoImage(file=icon)
            self.root.iconphoto(True, self.icon)
        self.root.geometry("1050x930")
        self.root.minsize(820, 640)
        self.root.configure(bg=BACKGROUND)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._styles()
        self._build()
        from iphone_detector.gui_sync import fields

        self.sync_baseline = fields(self.config)
        self.sync_conflicts = []
        self.last_sync = 0
        self.external_active = False
        self._refresh_device()
        self.root.after(150, self.poll)

    def _styles(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background=PANEL, foreground=TEXT)
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
        canvas = tk.Canvas(self.root, bg=BACKGROUND, highlightthickness=0)
        scroll = ttk.Scrollbar(self.root, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        outer = ttk.Frame(canvas, padding=26)
        window = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="iPhone IPA Manager", font=("Segoe UI", 23, "bold")).pack(
            side="left"
        )
        self.badge = tk.Label(
            header,
            text="READY",
            bg="#203b37",
            fg=ACCENT,
            padx=14,
            pady=6,
            font=("Segoe UI", 9, "bold"),
        )
        self.badge.pack(side="right")
        ttk.Button(header, text="Discord setup", command=self.open_discord_setup).pack(
            side="right", padx=12
        )
        ttk.Label(
            outer,
            text="Account assignments. Background sideloading. Live settings.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(3, 17))

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
        setup = ttk.Frame(outer, style="Card.TFrame", padding=14)
        setup.pack(fill="x", pady=(14, 0))
        ttk.Label(
            setup, text="Getting started", style="Card.TLabel", font=("Segoe UI", 11, "bold")
        ).pack(anchor="w")
        ttk.Label(
            setup,
            text="1. Choose an IPA folder and save settings.   2. Connect and unlock your iPhone; select it below.\n"
            "3. Add an Apple account and choose an IPA.   4. Check setup, then Run now.",
            style="Card.TLabel",
            wraplength=900,
        ).pack(anchor="w", pady=6)
        row = ttk.Frame(setup, style="Card.TFrame")
        row.pack(fill="x")
        self._button(row, "Add Apple account", self.open_account_dialog).pack(side="left")
        ttk.Button(row, text="Setup guide", command=self.open_guide).pack(side="left", padx=8)
        self._button(row, "Remote setup", self.open_remote_setup).pack(side="left")
        self._button(row, "Automatic renewal", self.open_renewal).pack(side="left", padx=8)
        self.names = []
        self.email_labels = []
        self.account_cards = ttk.Frame(outer)
        self.account_cards.pack(fill="x")
        for index, account in enumerate(self.accounts):
            self.add_account_card(index, account)

        options = ttk.Frame(outer)
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

        device = ttk.Frame(outer)
        device.pack(fill="x", pady=(0, 10))
        self.device_label = ttk.Label(device, style="Muted.TLabel")
        self.device_label.pack(side="left")
        self._button(device, "Pair connected iPhone", lambda: self.start("devices")).pack(
            side="right"
        )

        actions = ttk.Frame(outer)
        actions.pack(fill="x")
        self._button(actions, "Save settings", self.save).pack(side="left", padx=(0, 7))
        self._button(actions, "Reload saved", lambda: self.sync_saved(force=True)).pack(
            side="left", padx=(0, 7)
        )
        self._button(actions, "Check setup", lambda: self.start("check")).pack(
            side="left", padx=(0, 7)
        )
        self._button(actions, "Watch USB", lambda: self.start("watch")).pack(
            side="left", padx=(0, 7)
        )
        self._button(actions, "Run now", lambda: self.start("run"), style="Accent.TButton").pack(
            side="left"
        )
        self.stop_button = ttk.Button(
            actions, text="Stop", command=self.stop, style="Stop.TButton", state="disabled"
        )
        self.stop_button.pack(side="right")
        wireless = ttk.Frame(outer)
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
            wireless,
            text="USB pairing first. Uses the installed Wi-Fi service; no administrator prompts.",
            style="Muted.TLabel",
            wraplength=350,
        ).pack(side="left")
        background = ttk.Frame(outer)
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
        ttk.Label(outer, textvariable=self.status, style="Muted.TLabel", wraplength=950).pack(
            anchor="w", pady=(11, 7)
        )
        self.sync_status = tk.StringVar(value="Live sync enabled.")
        ttk.Label(outer, textvariable=self.sync_status, style="Muted.TLabel", wraplength=950).pack(
            anchor="w", pady=(0, 5)
        )
        self.activity = tk.Text(
            outer,
            height=6,
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
        footer = ttk.Frame(outer)
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
            footer, text="Stop requests safe cancellation of the active job.", style="Muted.TLabel"
        ).pack(side="right")

    def add_account_card(self, index, account):
        card = ttk.Frame(self.account_cards, style="Card.TFrame", padding=(15, 11))
        card.pack(fill="x", pady=(10, 0))
        line = ttk.Frame(card, style="Card.TFrame")
        line.pack(fill="x")
        ttk.Label(
            line, text="ACCOUNT %d" % (index + 1), style="Card.TLabel", font=("Segoe UI", 9, "bold")
        ).pack(side="left")
        email_label = ttk.Label(line, text=account["email"], style="Card.TLabel", foreground=MUTED)
        email_label.pack(side="right")
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
            else "No iPhone selected. Connect by USB and select it below."
        )

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
        if time.monotonic() - self.last_sync < 1:
            return
        self.last_sync = time.monotonic()
        try:
            self.sync_saved()
            from iphone_detector import auto_refresh, remote_tunnel
            from iphone_detector.worker_registry import active_jobs

            active = active_jobs(DATA_ROOT)
            detail = "No active run."
            if active:
                from iphone_detector.discord_bot import Controller

                progress = Controller()
                progress.job = active[0]["job"]
                progress.expected = len(active[0]["accounts"])
                progress.state = "Preparing background run."
                progress.accounts = (
                    [number - 1 for number in active[0]["accounts"]]
                    if len(active[0]["accounts"]) < len(self.accounts)
                    else None
                )
                detail = "Active: %s. %s" % (active[0]["mode"], progress.progress())
            from iphone_detector.gui_sync import conflict_names

            prefix = (
                (
                    "Conflicting saved changes: %s. Use Reload saved to accept saved values. "
                    % conflict_names(self.sync_conflicts)
                )
                if self.sync_conflicts
                else "Live sync active; unsaved edits stay local. "
            )
            self.sync_status.set(
                prefix + detail + " " + remote_tunnel.describe() + " " + auto_refresh.describe()
            )
            if self.process is None:
                self.stop_button.configure(state="normal" if active else "disabled")
                if active:
                    self.badge.configure(text="RUNNING", fg=ACCENT, bg="#203b37")
                elif self.external_active:
                    self.badge.configure(text="READY", fg=ACCENT, bg="#203b37")
                self.external_active = bool(active)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.sync_status.set(
                "Live sync unavailable; unsaved edits are preserved. " + str(exc)[:160]
            )

    def open_discord_setup(self):
        from iphone_detector.discord_bot import command

        subprocess.Popen(command(["--discord-setup"]), creationflags=subprocess.CREATE_NO_WINDOW)

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
                    "Account %d added. Check setup, then Run now to install its IPA." % number
                )

            self.account_dialog = AccountDialog(self.root, self.config, saved)
        except (ValueError, OSError, RuntimeError) as exc:
            messagebox.showerror("Account setup", str(exc), parent=self.root)

    def open_renewal(self):
        from iphone_detector import auto_refresh

        enabled = auto_refresh.read(auto_refresh.CONFIG).get("enabled", False)
        if not enabled and not self.accounts:
            messagebox.showinfo(
                "Add an account first",
                "Install an account before enabling renewal.",
                parent=self.root,
            )
            return
        try:
            auto_refresh.configure(not enabled)
            self.status.set(auto_refresh.describe())
        except (ValueError, RuntimeError, OSError) as exc:
            messagebox.showerror("Automatic renewal", str(exc), parent=self.root)

    def open_remote_setup(self):
        from iphone_detector.remote_dialog import RemoteDialog

        self.remote_dialog = RemoteDialog(self.root)

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
        from iphone_detector.ipa_inbox import import_ipa

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
        inbox = tk.StringVar(
            value=self.config.get("automation", {}).get(
                "import_directory", str(Path.home() / "Downloads")
            )
        )
        row = ttk.Frame(dialog, padding=12)
        row.pack(fill="x")
        ttk.Label(row, text="Source folder").pack(anchor="w")
        ttk.Entry(row, textvariable=inbox).pack(fill="x")

        def choose_folder():
            selected = filedialog.askdirectory(parent=dialog, initialdir=inbox.get())
            if selected:
                inbox.set(selected)
                refresh()

        ttk.Button(row, text="Choose source folder", command=choose_folder).pack(anchor="w")
        files = tk.Listbox(dialog, exportselection=False)
        files.pack(fill="both", expand=True, padx=12)
        paths = []

        def refresh():
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

        account = tk.IntVar(value=0)
        for index, entry in enumerate(self.accounts):
            ttk.Radiobutton(
                dialog,
                text="Account %d — %s" % (index + 1, entry["email"]),
                variable=account,
                value=index,
            ).pack(anchor="w", padx=12)

        def assign():
            if not files.curselection():
                messagebox.showinfo(
                    "Select an IPA",
                    "Wait for the file transfer to finish, then select its IPA.",
                    parent=dialog,
                )
                return
            try:
                # Reload persisted settings so other controller changes survive.
                self.config = load_config() or self.config
                target = import_ipa(paths[files.curselection()[0]], self.folder.get())
                previous = self.names[account.get()].get()
                self.names[account.get()].set(target.name)
                self.config.setdefault("automation", {})["import_directory"] = inbox.get()
                if not self.save():
                    self.names[account.get()].set(previous)
                    return
                self.status.set(
                    "IPA imported for account %d. Run sideload to install it." % (account.get() + 1)
                )
                dialog.destroy()
            except (OSError, ValueError, RuntimeError) as exc:
                messagebox.showerror("Import failed", str(exc), parent=dialog)

        ttk.Button(dialog, text="Import and assign to selected account", command=assign).pack(
            pady=10
        )

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

            self.sync_baseline = fields(updated)
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
            with log.open("rb") as handle:
                handle.seek(self.log_offset)
                data = handle.read(128 * 1024)
                self.log_offset = handle.tell()
            if data:
                self.append(self.decoder.decode(data))

    def poll(self):
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
            for device in devices:

                def select(value=device):
                    save_device(value)
                    dialog.destroy()

                ttk.Button(
                    dialog, text="%s · %s" % (device["name"], device["serial"]), command=select
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
            messagebox.showerror("Settings need repair", str(exc), parent=root)
            root.destroy()
            return 1
        root.mainloop()
    finally:
        if mutex:
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            kernel.CloseHandle(mutex)
    return 0
