"""Local Apple sign-in with a private subprocess and Tk-safe verification prompts."""

import asyncio
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

from iphone_detector.account_login import LoginSession, append_account, email_address
from iphone_detector.discord_inbox import managed_files


class AccountDialog:
    def __init__(self, parent, config, on_saved, *, preview=False):
        self.preview = preview
        self.on_saved = on_saved
        self.files = (
            [Path("Sample Video.ipa"), Path("Sample Music.ipa")]
            if preview
            else managed_files(config)
        )
        if not self.files:
            raise ValueError("Put an IPA in your saved IPA folder before adding an account.")
        self.events = queue.Queue()
        self.session = None
        self.loop = None
        self.closed = False
        self.running = False
        self.challenge = None
        self.window = tk.Toplevel(parent)
        self.window.title("Add Apple account")
        self.window.geometry("600x640")
        self.window.transient(parent)
        self.window.grab_set()
        self.window.protocol("WM_DELETE_WINDOW", self.cancel)
        from iphone_detector.dialog_ui import shell

        panel, _ = shell(
            self.window,
            "Add an Apple account",
            "1. Sign in   →   2. Verify with Apple   →   3. Save assignment",
            width=640,
            height=740,
            close=self.cancel,
        )
        ttk.Label(
            panel,
            text="Choose the app this account will manage. Signing in does not install anything. "
            "After verification, your password is stored in Windows Credential Manager.",
            wraplength=550,
        ).pack(anchor="w", pady=(8, 12))
        self.email = tk.StringVar()
        self.password = tk.StringVar()
        self.file = tk.StringVar(value=self.files[0].name)
        for label, variable, mask in [
            ("Apple account email", self.email, ""),
            ("Password", self.password, "•"),
        ]:
            ttk.Label(panel, text=label).pack(anchor="w", pady=(8, 3))
            ttk.Entry(panel, textvariable=variable, show=mask).pack(fill="x")
        ttk.Label(panel, text="IPA to assign (nothing is installed during sign-in)").pack(
            anchor="w", pady=(10, 3)
        )
        self.picker = ttk.Combobox(
            panel, values=[p.name for p in self.files], textvariable=self.file, state="readonly"
        )
        self.picker.pack(fill="x")
        self.start_button = ttk.Button(panel, text="Sign in with Apple", command=self.start)
        self.start_button.pack(anchor="w", pady=12)
        self.status = tk.StringVar(
            value="Your Apple account must meet Apple developer requirements."
        )
        ttk.Label(panel, textvariable=self.status, wraplength=540).pack(anchor="w", pady=8)
        self.verification = ttk.Frame(panel)
        self.verification.pack(fill="x")
        ttk.Button(panel, text="Cancel", command=self.cancel).pack(
            side="bottom", anchor="e", pady=8
        )
        self.window.after(100, self.poll)

    def start(self):
        if getattr(self, "preview", False):
            from types import SimpleNamespace

            self.session = SimpleNamespace(challenge=1)
            self.show_challenge({"numbers": [{"id": 1, "last_digits": "42"}]})
            self.status.set(
                "Sample verification screen. No sign-in request was sent; enter any six digits to try it."
            )
            return
        if self.running:
            return
        try:
            email = email_address(self.email.get())
            password = self.password.get()
            if not password or len(password) > 4096:
                raise ValueError("Enter your Apple account password (up to 4096 characters).")
            self.source = next(p for p in self.files if p.name == self.file.get())
            stat = self.source.stat()
            self.signature = (stat.st_size, stat.st_mtime_ns)
        except (ValueError, OSError, StopIteration) as exc:
            messagebox.showerror(
                "Check sign-in details", str(exc) or "Choose an available IPA.", parent=self.window
            )
            return
        self.running = True
        self.start_button.configure(state="disabled")
        self.picker.configure(state="disabled")
        self.password.set("")
        self.status.set("Connecting to Apple…")

        def run():
            async def authenticate():
                self.loop = asyncio.get_running_loop()
                self.session = LoginSession()
                try:
                    if self.closed:
                        return
                    await self.session.start(email, password)
                    async for event in self.session.events():
                        # Completion is delivered only after releasing the installation lock.
                        if event.get("stage") == "complete":
                            await self.session.close()
                        self.events.put(event)
                finally:
                    await self.session.close()

            try:
                asyncio.run(asyncio.wait_for(authenticate(), timeout=610))
            except Exception:
                self.events.put(
                    {
                        "stage": "failed",
                        "message": "Sign-in ended. Check your credentials, connection and Apple verification method, then try again.",
                    }
                )

        threading.Thread(target=run, daemon=True, name="local-apple-sign-in").start()

    def respond(self, value):
        if getattr(self, "preview", False):
            self.status.set("Sample verification complete. No account or credentials were saved.")
            return
        if self.session is None or self.loop is None or self.challenge is None:
            return
        number, self.challenge = self.challenge, None
        self.status.set("Waiting for Apple…")
        for child in self.verification.winfo_children():
            child.destroy()
        future = asyncio.run_coroutine_threadsafe(self.session.respond(number, value), self.loop)

        def completed(result):
            try:
                result.result()
            except Exception:
                self.events.put(
                    {
                        "stage": "failed",
                        "message": "Verification expired. Cancel and start a new sign-in.",
                    }
                )

        future.add_done_callback(completed)

    def show_challenge(self, event):
        self.challenge = self.session.challenge
        self.status.set(
            "Apple verification required. Use the latest code or choose a delivery method."
            + (" The previous code was rejected." if event.get("retry") else "")
        )
        for child in self.verification.winfo_children():
            child.destroy()
        code = tk.StringVar()
        ttk.Label(self.verification, text="Six-digit verification code").pack(anchor="w")
        ttk.Entry(self.verification, textvariable=code, width=12).pack(anchor="w", pady=5)

        def submit():
            value = code.get().strip()
            if len(value) == 6 and value.isascii() and value.isdecimal():
                self.respond({"action": "code", "code": value})
            else:
                self.status.set("Enter the six digits in the latest Apple verification code.")

        ttk.Button(self.verification, text="Verify code", command=submit).pack(anchor="w")
        row = ttk.Frame(self.verification)
        row.pack(fill="x", pady=8)
        ttk.Button(
            row, text="Send to trusted devices", command=lambda: self.respond({"action": "devices"})
        ).pack(side="left")
        ttk.Button(
            row, text="Resend code", command=lambda: self.respond({"action": "resend"})
        ).pack(side="left", padx=6)
        for number in event.get("numbers", [])[:25]:
            ttk.Button(
                self.verification,
                text="Send SMS to number ending " + str(number["last_digits"])[-2:],
                command=lambda n=number: self.respond({"action": "sms", "number": n["id"]}),
            ).pack(anchor="w", pady=2)

    def poll(self):
        if self.closed:
            return
        try:
            while True:
                event = self.events.get_nowait()
                stage = event.get("stage")
                if stage == "challenge":
                    self.show_challenge(event)
                elif stage == "developer_session":
                    self.status.set("Checking Apple developer access…")
                elif stage == "failed":
                    self.status.set(event.get("message", "Sign-in failed. Try again."))
                    self.running = False
                    self.start_button.configure(state="normal")
                    self.picker.configure(state="readonly")
                elif stage == "complete":
                    try:
                        number = append_account(event["email"], self.source, self.signature)
                    except (ValueError, RuntimeError, OSError) as exc:
                        self.status.set("Signed in, but assignment needs attention: " + str(exc))
                        self.running = False
                        self.start_button.configure(state="normal")
                        self.picker.configure(state="readonly")
                        continue
                    self.closed = True
                    self.window.destroy()
                    self.on_saved(number)
                    return
        except queue.Empty:
            pass
        self.window.after(100, self.poll)

    def cancel(self):
        self.closed = True
        if self.session and self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(self.session.close(), self.loop)
        self.window.destroy()
