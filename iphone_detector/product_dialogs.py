"""Guided setup, assignment recovery, run review and live progress windows."""

from datetime import datetime
from pathlib import Path
import queue
import threading
import uuid
import tkinter as tk
from tkinter import ttk, filedialog

from iphone_detector.config import load_config
from iphone_detector.setup_flow import STEPS
from iphone_detector.dialog_ui import shell, style_window, scrollbar_callback, size_window


class AsyncWindow:
    def __init__(self, app, title, description, *, preview=False):
        self.app, self.preview = app, preview
        self.events = queue.Queue()
        self.busy = False
        self.callbacks = {}
        self.window = tk.Toplevel(app.root)
        self.window.transient(app.root)
        style_window(self.window)
        self.body, footer = shell(
            self.window, title, description, width=930, height=620, scrollable=False
        )
        self.status = tk.StringVar()
        size_window(self.window, 930, 660, minimum=(760, 600))
        status_area = ttk.Frame(footer)
        status_area.pack(side="left", fill="x", expand=True)
        ttk.Label(status_area, textvariable=self.status, wraplength=730).pack(fill="x")
        self.window.after(100, self.poll)
        self.window.bind(
            "<Destroy>",
            lambda event: self.callbacks.clear() if event.widget is self.window else None,
            add="+",
        )

    def submit(self, operation, complete):
        if self.busy:
            return
        self.busy = True
        events = self.events
        identifier = uuid.uuid4().hex
        self.callbacks[identifier] = complete

        def run():
            try:
                events.put((operation(), "", identifier))
            except Exception as exc:
                import logging

                logging.getLogger(__name__).exception("Product dialog request failed")
                events.put(
                    (
                        None,
                        str(exc)
                        if isinstance(exc, (ValueError, RuntimeError))
                        else "Could not complete this action. Check the activity log.",
                        identifier,
                    )
                )

        threading.Thread(target=run, daemon=True).start()

    def poll(self):
        if not self.window.winfo_exists():
            return
        try:
            value, error, identifier = self.events.get_nowait()
        except queue.Empty:
            pass
        else:
            self.busy = False
            complete = self.callbacks.pop(identifier, None)
            if error:
                self.status.set(error)
            elif complete is not None:
                complete(value)
        if self.window.winfo_exists():
            self.window.after(100, self.poll)

    def table(self, columns):
        frame = ttk.Frame(self.body)
        frame.pack(fill="both", expand=True, pady=10)
        table = ttk.Treeview(frame, columns=columns, show="headings", selectmode="browse", height=8)
        for name in columns:
            table.heading(name, text=name)
            table.column(name, width=170, minwidth=70)
        scroll = ttk.Scrollbar(frame, command=table.yview)
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
        table.configure(
            yscrollcommand=scrollbar_callback(scroll, table),
            xscrollcommand=scrollbar_callback(horizontal, table, "bottom"),
        )
        scroll.pack(side="right", fill="y")
        horizontal.pack(side="bottom", fill="x")
        table.pack(fill="both", expand=True)
        return table


class AssignmentHistoryDialog(AsyncWindow):
    def __init__(self, app, *, preview=False):
        super().__init__(
            app,
            "Assignment history",
            "One IPA per account. Restore an earlier assignment if its file is still available and unchanged; this does not install or uninstall an app.",
            preview=preview,
        )
        self.rows = []
        self.list = self.table(("Changed", "Apple account", "Previous IPA", "Replacement IPA"))
        self.detail = tk.StringVar(value="Select a change to see its complete filenames.")
        ttk.Label(self.body, textvariable=self.detail, wraplength=820).pack(fill="x", pady=8)
        self.list.bind("<<TreeviewSelect>>", self.selected)
        actions = ttk.Frame(self.body)
        actions.pack(fill="x")
        ttk.Button(actions, text="Refresh history", command=self.refresh).pack(side="left")
        ttk.Button(actions, text="Restore selected assignment", command=self.undo).pack(
            side="left", padx=8
        )
        self.refresh()

    def refresh(self):
        from iphone_detector.assignment_history import entries

        if self.preview:
            self.show(
                [
                    {
                        "id": "sample",
                        "changed_at": "2026-10-28T12:00:00+00:00",
                        "email": "video@example.com",
                        "previous": {"ipa": "Video 2.1"},
                        "current": {"ipa": "Video 2.2"},
                    }
                ]
            )
            self.status.set("Preview · restoring is disabled; no settings are read or changed.")
        else:
            self.status.set("Reading saved changes…")
            self.submit(lambda: entries(load_config() or {}), self.show)

    def show(self, rows):
        self.rows = list(reversed(rows))
        self.list.delete(*self.list.get_children())
        for index, row in enumerate(self.rows):
            self.list.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    datetime.fromisoformat(row["changed_at"]).astimezone().strftime("%d %b %H:%M"),
                    row["email"],
                    row["previous"]["ipa"],
                    row["current"]["ipa"],
                ),
            )
        self.status.set(
            "%d saved change(s). History begins with changes made by this build." % len(rows)
        )

    def undo(self):
        if self.preview:
            self.status.set("Preview · no assignment has changed.")
            return
        selected = self.list.selection()
        if self.busy or not selected:
            return
        from iphone_detector.assignment_history import undo

        identifier = self.rows[int(selected[0])]["id"]
        self.status.set("Validating the previous IPA before restoring…")

        def done(number):
            self.app.sync_saved()
            self.refresh()
            self.status.set(
                "Restored account %d. Its next install or renewal will use the restored IPA."
                % number
            )

        self.submit(lambda: undo(identifier), done)

    def selected(self, event=None):
        selected = self.list.selection()
        if selected:
            row = self.rows[int(selected[0])]
            self.detail.set(
                "%s\nPrevious: %s.ipa\nReplacement: %s.ipa"
                % (row["email"], row["previous"]["ipa"], row["current"]["ipa"])
            )


class RunReviewDialog(AsyncWindow):
    def __init__(self, app, mode, confirm, *, accounts=None, preview=False, queued=False):
        super().__init__(
            app,
            "Review changes",
            "Check the saved account, app and expected operation before starting. Refresh eligibility is checked on the phone; unsaved edits in the main window are not included.",
            preview=preview,
        )
        self.review = None
        self.mode, self.accounts = mode, accounts
        self.confirm = confirm
        self.list = self.table(("Account", "App", "Version", "Operation"))
        for name, width, stretch in (
            ("Account", 220, True),
            ("App", 150, True),
            ("Version", 80, False),
            ("Operation", 360, True),
        ):
            self.list.column(name, width=width, stretch=stretch)
        self.detail = tk.StringVar(value="Select a row to see the exact IPA filename.")
        ttk.Label(self.body, textvariable=self.detail, wraplength=820).pack(fill="x", pady=8)
        self.list.bind("<<TreeviewSelect>>", self.selected)
        self.start_button = ttk.Button(
            self.body,
            text="Confirm and queue" if queued else "Confirm and start",
            command=self.start,
            state="disabled",
        )
        self.start_button.pack(anchor="e")
        ttk.Button(self.body, text="Reload review", command=self.refresh).pack(anchor="e", pady=6)
        if preview:
            self.show(
                {
                    "mode": mode,
                    "token": "sample",
                    "rows": [
                        {
                            "account": 1,
                            "email": "video@example.com",
                            "app": "Video",
                            "version": "2.2",
                            "ipa": "Video 2.2.ipa",
                            "operation": "Full installation (changed assignment)",
                        }
                    ],
                }
            )
            self.status.set("Preview · confirmation cannot start a job.")
        else:
            self.refresh()

    def refresh(self):
        if self.preview or self.busy:
            return
        from iphone_detector.run_review import plan

        mode, accounts = self.mode, self.accounts
        self.review = None
        self.start_button.configure(state="disabled")
        self.status.set("Reading saved assignments and app metadata…")
        self.submit(lambda: plan(load_config() or {}, mode, accounts), self.show)

    def show(self, review):
        self.review = review
        self.list.delete(*self.list.get_children())
        for index, row in enumerate(review["rows"]):
            self.list.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    "%d · %s" % (row["account"], row["email"]),
                    row["app"],
                    row["version"],
                    row["operation"],
                ),
            )
        self.status.set(
            "%d account(s). Full archive checks still run before device work." % len(review["rows"])
        )
        self.start_button.configure(state="normal")
        if review["rows"]:
            self.list.selection_set("0")
            self.selected()

    def selected(self, event=None):
        selected = self.list.selection()
        if selected and self.review:
            row = self.review["rows"][int(selected[0])]
            self.detail.set(
                "Account %d · %s\n%s %s · IPA: %s\n%s"
                % (
                    row["account"],
                    row["email"],
                    row["app"],
                    row["version"],
                    row["ipa"],
                    row["operation"],
                )
            )

    def start(self):
        if self.preview:
            self.status.set("Preview · no job has been started.")
            return
        if not self.review or self.busy:
            return
        from iphone_detector.run_review import verify

        review = self.review
        self.start_button.configure(state="disabled")

        def done(value):
            self.window.destroy()
            self.confirm(value)

        self.submit(lambda: verify(review, load_config() or {}), done)


class SetupWizard(AsyncWindow):
    def __init__(self, app, *, preview=False):
        super().__init__(
            app,
            "Get started",
            "A guided setup using the same saved settings and tools as the rest of the app. Nothing is enabled automatically.",
            preview=preview,
        )
        self.index = 0
        self.title = tk.StringVar()
        self.description = tk.StringVar()
        self.check = tk.StringVar()
        ttk.Label(self.body, textvariable=self.title, font=("Segoe UI", 18, "bold")).pack(
            anchor="w", pady=15
        )
        ttk.Label(self.body, textvariable=self.description, wraplength=820).pack(fill="x", pady=12)
        ttk.Label(self.body, textvariable=self.check, wraplength=820, style="Muted.TLabel").pack(
            fill="x", pady=20
        )
        actions = ttk.Frame(self.body)
        actions.pack(fill="x", pady=12)
        self.action = ttk.Button(actions, command=self.open_step)
        self.action.pack(side="left")
        ttk.Button(actions, text="Discord setup", command=self.app.open_discord_setup).pack(
            side="left", padx=8
        )
        ttk.Button(actions, text="Recheck", command=self.refresh).pack(side="left", padx=8)
        navigation = ttk.Frame(self.body)
        navigation.pack(side="bottom", fill="x", pady=15)
        self.back = ttk.Button(navigation, text="Back", command=lambda: self.move(-1))
        self.back.pack(side="left")
        self.next = ttk.Button(navigation, text="Next", command=lambda: self.move(1))
        self.next.pack(side="right")
        self.show_step()

    def move(self, change):
        if not self.busy:
            self.index = max(0, min(len(STEPS) - 1, self.index + change))
            self.show_step()

    def show_step(self):
        title, description, _, action = STEPS[self.index]
        self.title.set("%d / %d · %s" % (self.index + 1, len(STEPS), title))
        self.description.set(description)
        self.action.configure(
            text={
                "accounts": "Choose IPA folder",
                "pair": "Find connected iPhone",
                "login": "Add Apple account",
                "check": "Open setup checklist",
                "remote": "Remote setup",
                "renewal": "Automatic renewal",
            }[action]
        )
        self.back.configure(state="disabled" if self.index == 0 else "normal")
        self.next.configure(state="disabled" if self.index == len(STEPS) - 1 else "normal")
        self.refresh()

    def refresh(self):
        if self.preview:
            self.check.set(
                "Sample status · this is a walkthrough preview; no device or settings checks performed."
            )
            return
        from iphone_detector.setup_flow import inspect

        code = STEPS[self.index][2]

        def show(value):
            row = next((item for item in value["checks"] if item["code"] == code), None)
            self.check.set(
                (row["status"].upper() + " · " + row["detail"] + "\n" + row["action"])
                if row
                else "Open this setup tool to continue."
            )
            self.status.set(
                "Read-only status. Recheck after saving changes. Optional steps may be skipped."
            )

        self.submit(inspect, show)

    def open_step(self):
        if self.busy:
            return
        action = STEPS[self.index][3]
        if action == "accounts":
            if self.preview:
                self.status.set("Preview · no IPA folder has changed.")
                return
            selected = filedialog.askdirectory(parent=self.window, title="Choose your IPA folder")
            if not selected:
                return
            from iphone_detector.discord_controls import change_settings

            def done(value):
                self.app.sync_saved()
                self.refresh()

            self.submit(lambda: change_settings(folder=selected), done)
        else:
            {
                "pair": lambda: self.app.start("devices"),
                "login": self.app.open_account_dialog,
                "check": self.app.open_checklist,
                "remote": self.app.open_remote_setup,
                "renewal": self.app.open_renewal,
            }[action]()


class ProgressDialog(AsyncWindow):
    def __init__(self, app, *, preview=False):
        super().__init__(
            app,
            "Live progress",
            "Preparing → Signing in → Signing → Transferring → Installing → Verifying. Profile refreshes can skip stages; no completion time is guessed.",
            preview=preview,
        )
        self.list = self.table(("Account", "Current stage"))
        self.list.column("Account", width=140, stretch=False)
        self.hint = tk.StringVar()
        ttk.Label(self.body, textvariable=self.hint, wraplength=820).pack(fill="x", pady=12)
        ttk.Button(self.body, text="Activity & recovery", command=app.open_dashboard).pack(
            anchor="w"
        )
        self.refresh()

    def refresh(self):
        if self.preview:
            self.show(
                {
                    "state": "Working",
                    "elapsed": "1m 24s elapsed",
                    "accounts": [
                        {"account": 1, "stage": "Completed"},
                        {"account": 2, "stage": "Transferring"},
                        {"account": 3, "stage": "Preparing"},
                    ],
                    "hint": "Sample progress · nothing is running or being installed.",
                }
            )
            return
        from iphone_detector.paths import DATA_ROOT
        from iphone_detector.worker_registry import active_jobs
        from iphone_detector.job_progress import snapshot

        fallback = getattr(self.app, "job", None)

        def read():
            active = active_jobs(DATA_ROOT)
            job = active[0]["job"] if active else fallback
            return (
                snapshot(Path(job))
                if job
                else {
                    "state": "No active run",
                    "elapsed": "",
                    "accounts": [],
                    "hint": "Start a job or open Activity & recovery to inspect earlier outcomes.",
                }
            )

        self.submit(read, self.show)
        self.window.after(1500, self.refresh)

    def show(self, value):
        self.list.delete(*self.list.get_children())
        for row in value["accounts"]:
            self.list.insert("", "end", values=(row["account"], row["stage"]))
        self.status.set(value["state"] + (" · " + value["elapsed"] if value["elapsed"] else ""))
        self.hint.set(value["hint"])
