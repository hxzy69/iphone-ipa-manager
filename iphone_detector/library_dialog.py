"""Search and assign managed IPAs without opening a Windows file picker."""

import queue
import threading
import tkinter as tk
from tkinter import ttk

from iphone_detector import ipa_library
from iphone_detector.config import load_config
from iphone_detector.dialog_ui import shell, style_window, scrollbar_callback, size_window
from iphone_detector.settings import get_workflow


class LibraryDialog:
    def __init__(self, app, *, preview=False):
        self.app = app
        self.preview = preview
        self.entries = []
        self.visible = []
        self.accounts = []
        self.busy = False
        self.saved_notice = ""
        self.closed = threading.Event()
        self.events = queue.Queue()
        self.window = tk.Toplevel(app.root)
        self.window.transient(app.root)
        style_window(self.window)
        body, footer = shell(
            self.window,
            "IPA Library",
            "Find an app by its name, version, bundle ID or filename. Assign it once for installs, refresh and automatic renewal.",
            width=980,
            height=660,
            scrollable=False,
        )
        size_window(self.window, 980, 660, minimum=(720, 560))
        self.window.bind(
            "<Destroy>",
            self.close,
            add="+",
        )
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        self.query = tk.StringVar()
        self.status = tk.StringVar(value="Loading your IPA folder…")
        self.details = tk.StringVar(value="Select an app to see its file and bundle ID.")
        search = ttk.Frame(body)
        search.grid(row=0, column=0, sticky="ew", pady=(0, 12))
        ttk.Label(search, text="Search").pack(side="left", padx=(0, 10))
        entry = ttk.Entry(search, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True)
        self.refresh_button = ttk.Button(search, text="Refresh", command=self.refresh)
        self.refresh_button.pack(side="left", padx=(10, 0))
        ttk.Label(
            body,
            text="Uses your saved IPA folder. After changing the folder in Accounts & apps, save settings and refresh here.",
            style="Muted.TLabel",
            wraplength=880,
        ).grid(row=1, column=0, sticky="ew", pady=(0, 10))
        table = ttk.Frame(body)
        table.grid(row=2, column=0, sticky="nsew")
        self.table = ttk.Treeview(
            table,
            columns=("app", "version", "size", "accounts", "status"),
            show="headings",
            selectmode="browse",
            height=10,
        )
        for key, label, width in (
            ("app", "App", 240),
            ("version", "Version", 100),
            ("size", "Size", 90),
            ("accounts", "Accounts", 110),
            ("status", "Metadata", 130),
        ):
            self.table.heading(key, text=label)
            self.table.column(key, width=width, minwidth=65, stretch=key in ("app", "status"))
        vertical = ttk.Scrollbar(table, orient="vertical", command=self.table.yview)
        horizontal = ttk.Scrollbar(table, orient="horizontal", command=self.table.xview)
        self.table.configure(
            yscrollcommand=scrollbar_callback(vertical, self.table),
            xscrollcommand=scrollbar_callback(horizontal, self.table, "bottom"),
        )
        vertical.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        self.table.pack(fill="both", expand=True)
        ttk.Label(body, textvariable=self.details, style="Muted.TLabel", wraplength=880).grid(
            row=3, column=0, sticky="ew", pady=(12, 10)
        )
        assign = ttk.Frame(body)
        assign.grid(row=4, column=0, sticky="ew")
        ttk.Label(assign, text="Assign to").pack(side="left", padx=(0, 10))
        self.account = ttk.Combobox(assign, state="readonly", width=42)
        self.account.pack(side="left", fill="x", expand=True)
        self.assign_button = ttk.Button(assign, text="Save assignment", command=self.assign)
        self.assign_button.pack(side="left", padx=(10, 0))
        ttk.Label(
            body,
            text="Saving replaces that account’s IPA assignment. It checks the full archive and does not start an installation.",
            style="Muted.TLabel",
            wraplength=880,
        ).grid(row=5, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(footer, textvariable=self.status, wraplength=780).pack(
            side="left", fill="x", expand=True, padx=(0, 12)
        )
        self.table.bind("<<TreeviewSelect>>", self.select)
        self.account.bind("<<ComboboxSelected>>", self.select)
        self.query_trace = self.query.trace_add("write", lambda *_: self.filter())
        entry.focus_set()
        self.refresh()
        self.window.after(100, self.poll)

    def close(self, event):
        if event.widget is self.window:
            self.closed.set()
            self.query.trace_remove("write", self.query_trace)

    def work(self, kind, operation):
        self.busy = True
        self.refresh_button.configure(state="disabled")
        self.assign_button.configure(state="disabled")
        self.account.configure(state="disabled")
        events = self.events

        def run():
            try:
                events.put((kind, operation(), ""))
            except Exception as exc:
                import logging

                logging.getLogger(__name__).exception("IPA library %s failed", kind)
                message = (
                    str(exc)
                    if isinstance(exc, (ValueError, RuntimeError))
                    else "Could not complete this action. Check the activity log and refresh the library."
                )
                for source, replacement in (
                    ("Open /inbox or /assign again.", "Refresh the library and choose again."),
                    ("Open /assign again.", "Refresh the library and choose again."),
                    ("Try /assign after it finishes.", "Try again after it finishes."),
                    (
                        "Open /inbox after the file transfer finishes.",
                        "Refresh the library after the file transfer finishes.",
                    ),
                ):
                    message = message.replace(source, replacement)
                events.put((kind, None, message))

        threading.Thread(target=run, daemon=True).start()

    def refresh(self):
        if self.busy:
            return
        if self.preview:
            self.entries = ipa_library.sample_entries()
            self.accounts = get_workflow(self.app.config)
            self.populate()
            self.status.set("Preview · sample apps only. Nothing will be saved.")
            return
        self.status.set("Reading app metadata…")
        stop = self.closed

        def read():
            config = load_config() or {}
            ipa_library.clear_cache()
            return ipa_library.scan(config, stop=stop), get_workflow(config)

        self.work("scan", read)

    def populate(self):
        previous = self.account.get()
        values = [
            "Account %d · %s" % (i + 1, item["email"]) for i, item in enumerate(self.accounts)
        ]
        self.account.configure(values=values)
        # Never silently default a new assignment to account 1 or retain a slot
        # whose Apple account changed while this window was open.
        self.account.set(previous if previous in values else "Choose an account…")
        self.filter()

    def filter(self):
        selected = self.selected()
        self.table.delete(*self.table.get_children())
        self.visible = [item for item in self.entries if item.matches(self.query.get())]
        for index, item in enumerate(self.visible):
            identifier = str(index)
            self.table.insert(
                "",
                "end",
                iid=identifier,
                values=(
                    item.name,
                    item.version or "—",
                    "%.1f MB" % item.size_mb,
                    ", ".join(map(str, item.accounts)) or "Unassigned",
                    "Unreadable" if item.error else "Read",
                ),
            )
            if selected and selected.path == item.path:
                self.table.selection_set(identifier)
        self.select()

    def selected(self):
        selection = self.table.selection()
        if selection:
            index = int(selection[0])
            if index < len(self.visible):
                return self.visible[index]
        return None

    def select(self, event=None):
        item = self.selected()
        if item:
            self.details.set(
                "File: %s\n%s" % (item.path.name, item.error or "Bundle ID: " + item.bundle_id)
            )
        else:
            self.details.set(
                "Select an app to see its file and bundle ID."
                if self.visible
                else "No matching IPAs. Clear the search or copy an IPA into your configured folder, then refresh."
            )
        self.assign_button.configure(
            state="normal"
            if item and not item.error and self.account.current() >= 0 and not self.busy
            else "disabled"
        )

    def assign(self):
        if self.preview:
            self.status.set("Preview · no account assignment has changed.")
            return
        item = self.selected()
        index = self.account.current()
        if self.busy or not item or item.error or not 0 <= index < len(self.accounts):
            return
        email = self.accounts[index]["email"]
        self.status.set("Checking the complete IPA before saving…")

        def save():
            from iphone_detector.discord_inbox import assign_managed

            assign_managed(item.path, item.signature, index, expected_email=email)
            return index + 1

        self.work("assign", save)

    def poll(self):
        try:
            kind, result, error = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            self.refresh_button.configure(state="normal")
            self.account.configure(state="readonly")
            if error:
                self.status.set(error)
                if kind == "scan":
                    self.entries = []
                    self.accounts = []
                    self.populate()
            elif kind == "scan":
                self.entries, self.accounts = result
                self.populate()
                self.status.set(
                    self.saved_notice
                    or "%d IPA files. Metadata is for browsing; full checks run when you save."
                    % len(self.entries)
                )
                self.saved_notice = ""
            else:
                self.app.sync_saved()
                self.saved_notice = (
                    "Saved for account %d. Installs, refresh and automatic renewal will use this IPA."
                    % result
                )
                self.refresh()
            self.select()
        self.window.after(100, self.poll)
