"""All preview screens use production layouts without running external actions."""

import tkinter as tk
import unittest
from unittest.mock import patch
from tkinter import ttk

from tools.preview_gui import Preview
from iphone_detector.account_dialog import AccountDialog
from iphone_detector.remote_dialog import RemoteDialog
from iphone_detector.renewal_dialog import RenewalDialog


class DialogPreviewTests(unittest.TestCase):
    def test_scrollbar_only_appears_when_accounts_outgrow_the_page(self):
        root = tk.Tk()
        try:
            root.tk.call("tk", "scaling", 96 / 72)
            app = Preview(root)
            app.workspace.select(1)
            root.update()
            page = app.workspace.nametowidget(app.workspace.tabs()[1])
            scroll = next(w for w in page.winfo_children() if isinstance(w, ttk.Scrollbar))
            # Exercise both fitting and overflowing content independently of runner resolution.
            for card in app.account_cards.winfo_children():
                card.destroy()
            app.account_cards.configure(height=1)
            root.update()
            self.assertFalse(scroll.winfo_ismapped())
            for index in range(3, 20):
                app.add_account_card(index, {"email": "sample@example.com", "ipa": "Sample"})
            root.update()
            self.assertTrue(scroll.winfo_ismapped())
        finally:
            root.destroy()

    def test_install_preview_shows_progress_without_installing(self):
        from iphone_detector.desktop_install import setup_window

        root = tk.Tk()
        try:
            with patch("iphone_detector.desktop_install.install_payload") as install:
                window = setup_window(payload="sample", parent=root, preview=True)

                def widgets(parent):
                    for child in parent.winfo_children():
                        yield child
                        yield from widgets(child)

                progress = next(w for w in widgets(window) if isinstance(w, ttk.Progressbar))
                action = next(
                    w
                    for w in widgets(window)
                    if isinstance(w, ttk.Button) and w.cget("text") == "Install / repair"
                )
                action.invoke()
                root.update()
                self.assertTrue(progress.winfo_ismapped())
                self.assertEqual(str(progress.cget("mode")), "determinate")
                install.assert_not_called()
        finally:
            root.destroy()

    def test_every_screen_opens_without_workers_or_configuration_writes(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with (
                patch(
                    "threading.Thread.start", side_effect=AssertionError("Preview started a worker")
                ),
                patch("subprocess.Popen", side_effect=AssertionError("Preview started a process")),
                patch(
                    "iphone_detector.config.save_config",
                    side_effect=AssertionError("Preview wrote config"),
                ),
                patch(
                    "iphone_detector.auto_refresh.configure",
                    side_effect=AssertionError("Preview changed renewal"),
                ),
            ):
                app = Preview(root)
                callbacks = [
                    app.open_account_dialog,
                    app.open_remote_setup,
                    app.open_renewal,
                    app.open_discord_setup,
                    app.open_dashboard,
                    app.open_ipa_import,
                    app.open_library,
                    app.open_setup_wizard,
                    app.open_progress,
                    app.open_run_review,
                    app.open_checklist,
                    app.open_updates,
                    lambda: app.start("devices"),
                    lambda: app.installer(False),
                    lambda: app.installer(True),
                ]
                for callback in callbacks:
                    with self.subTest(screen=callback):
                        callback()
                        root.update_idletasks()
                        windows = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)]
                        self.assertTrue(windows)
                        for window in windows:
                            window.destroy()
        finally:
            root.destroy()

    def test_sample_sign_in_remote_and_renewal_actions_are_inert(self):
        root = tk.Tk()
        root.withdraw()
        try:
            with (
                patch("threading.Thread.start", side_effect=AssertionError("Unexpected worker")),
                patch(
                    "iphone_detector.account_dialog.LoginSession",
                    side_effect=AssertionError("Unexpected sign-in"),
                ),
                patch(
                    "iphone_detector.auto_refresh.configure",
                    side_effect=AssertionError("Unexpected schedule change"),
                ),
            ):
                account = AccountDialog(
                    root, {}, lambda number: self.fail("Saved account"), preview=True
                )
                account.start()
                self.assertIn("Sample verification", account.status.get())
                account.respond({"action": "code", "code": "123456"})
                self.assertIn("No account", account.status.get())
                account.cancel()
                remote = RemoteDialog(root, preview=True)
                remote.start()
                self.assertIn("Nothing was saved", remote.status.get())
                renewal = RenewalDialog(root, preview=True)
                renewal.save()
                self.assertIn("has not changed", renewal.status.get())
        finally:
            root.destroy()
