"""Live account overview and explicit queue/recovery controls."""

import json
import queue as queues
import threading
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from iphone_detector.paths import DATA_ROOT


class ManagementDialog:
    def __init__(self, app):
        self.app = app
        self.events = queues.Queue()
        self.loading = False
        self.window = tk.Toplevel(app.root)
        self.window.title("Dashboard and queue")
        self.window.geometry("1100x730")
        self.window.transient(app.root)
        panel = ttk.Frame(self.window, padding=18)
        panel.pack(fill="both", expand=True)
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
        self.account = ttk.Combobox(actions, state="readonly", width=32)
        self.account.pack(side="left")
        ttk.Button(
            actions, text="Queue network install", command=lambda: self.enqueue("install")
        ).pack(side="left", padx=6)
        ttk.Button(
            actions, text="Queue refresh (all)", command=lambda: self.enqueue("refresh")
        ).pack(side="left")
        self.queue = self.table(panel, ("Request", "Operation", "Accounts", "State"), 5)
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=8)
        ttk.Button(
            actions, text="Cancel waiting request", command=lambda: self.change("cancel")
        ).pack(side="left")
        ttk.Button(
            actions, text="Dismiss failure and resume queue", command=lambda: self.change("dismiss")
        ).pack(side="left", padx=8)
        from iphone_detector.job_history import read

        self.startup = tk.BooleanVar(
            value=read(DATA_ROOT / "queue-settings.json").get("startup", True)
        )
        ttk.Checkbutton(
            actions,
            text="Resume at Windows sign-in",
            variable=self.startup,
            command=lambda: self.perform(self.change_startup),
        ).pack(side="left")
        ttk.Label(
            panel,
            text="Recent runs — retry performs full installs for accounts without a matching verified completion.",
        ).pack(anchor="w")
        self.history = self.table(panel, ("Run", "Outcome", "Verified accounts"), 4)
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=8)
        ttk.Button(actions, text="Retry unfinished accounts", command=self.retry).pack(side="left")
        ttk.Button(actions, text="Export configuration backup", command=self.export).pack(
            side="left", padx=8
        )
        ttk.Button(actions, text="Restore backup…", command=self.restore).pack(side="left")
        self.poll()

    def table(self, parent, columns, height):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=5)
        tree = ttk.Treeview(frame, columns=columns, show="headings", height=height)
        for column in columns:
            tree.heading(column, text=column)
            tree.column(column, width=150, minwidth=70)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
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
            from iphone_detector import dashboard, job_queue, operations
            from iphone_detector.job_history import read

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
                                        "Completed"
                                        if read(job / "result.json").get("ok")
                                        else "Needs attention / active",
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
                            e["state"],
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
        try:
            result = operation()
            self.app.status.set(str(result))
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror("Dashboard", str(exc), parent=self.window)

    def enqueue(self, mode):
        from iphone_detector import job_queue

        index = self.account.current()
        self.perform(
            lambda: (
                "Queued "
                + job_queue.enqueue(mode, [index - 1] if mode == "install" and index > 0 else None)[
                    "id"
                ]
            )
        )

    def change_startup(self):
        from iphone_detector.job_queue import configure_startup

        configure_startup(self.startup.get())
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

        def operation():
            job = next(path for path in operations.jobs(DATA_ROOT) if path.name == selected[0])
            accounts = operations.retry_plan(job, load_config() or {})
            if messagebox.askyesno(
                "Retry unfinished accounts",
                "Full-install accounts "
                + ", ".join(str(n + 1) for n in accounts)
                + "? Inspect the phone first if the previous outcome was uncertain. Verified completed accounts will be skipped.",
                parent=self.window,
            ):
                return "Queued retry " + job_queue.enqueue("install", accounts)["id"]
            return "Retry cancelled."

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

        def operation():
            value = preview(path)
            if messagebox.askyesno(
                "Restore configuration",
                f"Replace assignments with {len(value['accounts'])} accounts? Credentials, pairing and the IPA folder stay local. Imported accounts still require saved Apple sign-in and available IPAs.",
                parent=self.window,
            ):
                count = restore(value)
                self.app.sync_saved(force=True)
                return f"Restored {count} account assignments. Check setup before running."
            return "Restore cancelled."

        self.perform(operation)
