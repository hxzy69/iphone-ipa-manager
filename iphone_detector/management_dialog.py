"""Live account overview and explicit queue/recovery controls."""

import json
import queue as queues
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from iphone_detector.paths import DATA_ROOT


class ManagementDialog:
    def __init__(self, app, *, preview=False):
        self.app = app
        self.events = queues.Queue()
        self.action_events = queues.Queue()
        self.loading = False
        self.window = tk.Toplevel(app.root)
        self.window.title("Dashboard and queue")
        self.window.geometry(
            f"{min(1100, app.root.winfo_screenwidth() - 60)}x{min(730, app.root.winfo_screenheight() - 100)}"
        )
        self.window.transient(app.root)
        from iphone_detector.dialog_ui import shell, section, wrap_actions

        panel, _ = shell(
            self.window,
            "Activity & app management",
            "Review installed profiles, queue work, and recover incomplete runs.",
            width=1050,
            height=740,
        )
        self.tabs = ttk.Notebook(panel)
        self.tabs.pack(fill="both", expand=True)
        pages = {}
        for title in ("Accounts", "Queue", "History & recovery"):
            page = ttk.Frame(self.tabs, padding=12)
            self.tabs.add(page, text=title)
            pages[title] = page
        self.tabs.enable_traversal()
        panel = pages["Accounts"]
        section(
            panel,
            "App status",
            "Choose an account for a full install. Refresh renews all assigned accounts and may reinstall only when needed.",
        )
        self.health = tk.StringVar()
        ttk.Label(panel, textvariable=self.health, wraplength=1050).pack(anchor="w", pady=8)
        self.accounts = self.table(
            panel,
            ("Account", "Apple account", "IPA", "Last verified", "Operation", "Profile expiry"),
            6,
        )
        ttk.Label(
            panel,
            text="Expiry is shown only from verified profile readback; Unknown is not a promise of validity.",
        ).pack(anchor="w")
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=10)
        action_rows = [actions]
        self.account = ttk.Combobox(actions, state="readonly", width=32)
        self.account.pack(side="left")
        ttk.Button(
            actions, text="Queue network install", command=lambda: self.enqueue("install")
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="Queue refresh (all)", command=lambda: self.enqueue("refresh")
        ).pack(side="left")
        panel = pages["Queue"]
        section(
            panel,
            "Waiting and active requests",
            "Waiting jobs run when the phone is reachable. Inspect an uncertain result before retrying.",
        )
        self.queue = self.table(panel, ("Request", "Operation", "Accounts", "State"), 5)
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=8)
        action_rows.append(actions)
        ttk.Button(
            actions, text="Cancel waiting request", command=lambda: self.change("cancel")
        ).pack(side="left")
        ttk.Button(
            actions, text="Dismiss failure and resume queue", command=lambda: self.change("dismiss")
        ).pack(side="left", padx=8)
        from iphone_detector.job_history import read

        self.startup = tk.BooleanVar(
            value=True if preview else read(DATA_ROOT / "queue-settings.json").get("startup", True)
        )
        ttk.Checkbutton(
            actions,
            text="Resume at Windows sign-in",
            variable=self.startup,
            command=self.toggle_startup,
        ).pack(side="left")
        panel = pages["History & recovery"]
        ttk.Label(
            panel,
            text="Recent runs — retry performs full installs for accounts without a matching verified completion.",
        ).pack(anchor="w")
        self.history = self.table(panel, ("Run", "Outcome", "Verified accounts"), 4)
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=8)
        action_rows.append(actions)
        ttk.Button(actions, text="Retry unfinished accounts", command=self.retry).pack(side="left")
        ttk.Button(actions, text="Export configuration backup", command=self.export).pack(
            side="left", padx=8
        )
        ttk.Button(actions, text="Restore backup…", command=self.restore).pack(side="left")
        ttk.Button(actions, text="Clean old logs", command=self.cleanup).pack(side="left", padx=8)
        for row in action_rows:
            wrap_actions(row)
        if preview:
            from iphone_detector.dialog_ui import preview_notice

            self.health.set(
                "Sample iPhone · Remote tunnel ready\nSample renewal: next refresh in 4 days."
            )
            self.rows(
                self.accounts,
                [
                    (
                        str(i),
                        (
                            i,
                            a["email"],
                            a["ipa"],
                            "Sample: today",
                            "Profile refresh",
                            "Sample: in 6 days",
                        ),
                    )
                    for i, a in enumerate(app.accounts, 1)
                ],
            )
            self.account.configure(values=["All accounts"] + [a["email"] for a in app.accounts])
            self.account.current(0)
            self.rows(
                self.queue, [("sample", ("Sample request", "Refresh", "All", "Waiting for device"))]
            )
            self.rows(self.history, [("sample", ("Sample run", "Complete", "1, 2, 3"))])

            def protect(parent):
                for child in parent.winfo_children():
                    if (
                        isinstance(child, (ttk.Button, ttk.Checkbutton))
                        and child.cget("text") != "Close"
                    ):
                        child.configure(command=lambda: preview_notice(self.window))
                    protect(child)

            protect(self.window)
        else:
            self.poll()

    def table(self, parent, columns, height):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=5)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=150, minwidth=70)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        from iphone_detector.dialog_ui import scrollbar_callback

        tree.configure(yscrollcommand=scrollbar_callback(scroll, tree))
        scroll.pack(side="right", fill="y")
        horizontal = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(xscrollcommand=scrollbar_callback(horizontal, tree, "bottom"))
        horizontal.pack(side="bottom", fill="x")
        tree.pack(side="left", fill="both", expand=True)
        return tree

    def rows(self, tree, values):
        selected = tree.selection()
        tree.delete(*tree.get_children())
        for identifier, row in values:
            tree.insert("", "end", iid=identifier, values=row)
        if selected and tree.exists(selected[0]):
            tree.selection_set(selected[0])

    def poll(self):
        if not self.window.winfo_exists():
            return
        try:
            result, error = self.action_events.get_nowait()
            if error:
                messagebox.showerror("Dashboard", error, parent=self.window)
            else:
                self.app.status.set(str(result))
                self.app.sync_saved()
        except queues.Empty:
            pass
        try:
            from iphone_detector import dashboard, job_queue, operations
            from iphone_detector.job_history import describe

            try:
                value, queued, history, error = self.events.get_nowait()
                self.loading = False
                if error:
                    raise ValueError(error)
            except queues.Empty:
                if not self.loading:
                    self.loading = True

                    def collect():
                        try:
                            value = dashboard.snapshot()
                            queued = job_queue.entries()[-30:]
                            history = [
                                (
                                    job.name,
                                    (
                                        job.name,
                                        describe(job)["status"],
                                        ", ".join(
                                            p.stem.removeprefix("completed-")
                                            for p in job.glob("completed-*.json")
                                        ),
                                    ),
                                )
                                for job in operations.jobs(DATA_ROOT, 10)
                            ]
                            self.events.put((value, queued, history, None))
                        except Exception:
                            self.events.put(
                                (
                                    None,
                                    None,
                                    None,
                                    "Saved workflow or queue state could not be read. Inspect the local settings.",
                                )
                            )

                    threading.Thread(target=collect, daemon=True).start()
                self.app.root.after(100, self.poll)
                return
            connection = value["connection"]
            speed = (
                f" · {connection['upload_mib_s']} MiB/s average upload"
                if connection["upload_mib_s"] is not None
                else ""
            )
            self.health.set(
                connection["route"]
                + speed
                + "\n"
                + connection["explanation"]
                + "\n"
                + value["renewal"]
                + "\n"
                + value["queue"]
            )
            self.rows(
                self.accounts,
                [
                    (
                        str(a["number"]),
                        (
                            a["number"],
                            a["email"],
                            a["ipa"],
                            a["last_verified"],
                            a["operation"] if a["file_exists"] else "Missing IPA",
                            ("Expired: " if a["profile_expired"] else "") + a["profile_expiry"],
                        ),
                    )
                    for a in value["accounts"]
                ],
            )
            options = ["All accounts"] + [
                f"{a['number']} — {a['email']}" for a in value["accounts"]
            ]
            self.account.configure(values=options)
            if self.account.get() not in options:
                self.account.current(0)
            self.rows(
                self.queue,
                [
                    (
                        e["id"],
                        (
                            e["id"],
                            e["mode"],
                            ", ".join(str(a["account"]) for a in e["selection"]["accounts"]),
                            e["state"]
                            + (
                                " · " + job_queue.failure_label(e)
                                if job_queue.failure_label(e)
                                else ""
                            ),
                        ),
                    )
                    for e in queued
                ],
            )
            self.rows(
                self.history,
                history,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.health.set("Dashboard needs attention: " + str(exc)[:200])
        self.app.root.after(3000, self.poll)

    def perform(self, operation):
        def run():
            try:
                self.action_events.put((operation(), None))
            except (OSError, ValueError, RuntimeError) as exc:
                self.action_events.put((None, str(exc)))

        threading.Thread(target=run, daemon=True).start()

    def enqueue(self, mode):
        from iphone_detector import job_queue

        index = self.account.current()
        label = self.account.get()
        if mode == "install" and index < 0:
            messagebox.showerror("Dashboard", "Choose an account first.", parent=self.window)
            return

        def operation():
            selected = [index - 1] if mode == "install" and index > 0 else None
            options = {}
            if selected is not None:
                prefix = f"{index} — "
                if not label.startswith(prefix):
                    raise ValueError("Refresh the dashboard and choose the account again.")
                options["expected_email"] = label[len(prefix) :]
            return "Queued " + job_queue.enqueue(mode, selected, **options)["id"]

        self.perform(operation)

    def toggle_startup(self):
        self.startup_value = self.startup.get()
        self.perform(self.change_startup)

    def change_startup(self):
        from iphone_detector.job_queue import configure_startup

        configure_startup(self.startup_value)
        return "Queue startup preference saved."

    def change(self, action):
        from iphone_detector import job_queue

        selected = self.queue.selection()
        if selected:
            self.perform(lambda: "Request " + job_queue.change(selected[0], action)["state"])

    def retry(self):
        from iphone_detector import job_queue, operations
        from iphone_detector.config import load_config

        selected = self.history.selection()
        if not selected:
            return
        if not messagebox.askyesno(
            "Retry unfinished accounts",
            "Queue full installs for unfinished accounts in this run? Inspect the phone first if its previous outcome was uncertain. Matching verified completions will be skipped.",
            parent=self.window,
        ):
            return

        def operation():
            job = next(
                (path for path in operations.jobs(DATA_ROOT) if path.name == selected[0]), None
            )
            if job is None:
                raise ValueError(
                    "This run is no longer available. Refresh the history and try again."
                )
            accounts = operations.retry_plan(job, load_config() or {})
            return "Queued retry " + job_queue.enqueue("install", accounts)["id"]

        self.perform(operation)

    def export(self):
        from iphone_detector.config_backup import export

        path = filedialog.asksaveasfilename(
            parent=self.window, initialfile="ipa-manager-settings.json", defaultextension=".json"
        )
        if path:
            self.perform(
                lambda: (
                    Path(path).write_text(json.dumps(export(), indent=2), encoding="utf-8")
                    and "Backup saved. It includes account emails and assignments; keep it private."
                )
            )

    def restore(self):
        from iphone_detector.config_backup import preview, restore

        path = filedialog.askopenfilename(
            parent=self.window, filetypes=[("Configuration backup", "*.json")]
        )
        if not path:
            return
        try:
            value = preview(path)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Backup", str(exc), parent=self.window)
            return
        if messagebox.askyesno(
            "Restore configuration",
            f"Replace assignments with {len(value['accounts'])} accounts? Credentials, pairing and the IPA folder stay local. Imported accounts still require saved Apple sign-in and available IPAs.",
            parent=self.window,
        ):
            self.perform(
                lambda: (
                    f"Restored {restore(value)} account assignments. Check setup before running."
                )
            )

    def cleanup(self):
        from iphone_detector.maintenance import clean

        if messagebox.askyesno(
            "Clean old logs",
            "Remove logs older than 30 days from successful, inactive runs and terminal queue/delivered report records older than 90 days? IPA files, credentials, unresolved runs and completion receipts are preserved.",
            parent=self.window,
        ):
            self.perform(lambda: f"Removed {clean()} old files. Recovery receipts were preserved.")
