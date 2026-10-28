import queue
import gc
import threading
import time
import tkinter as tk
import unittest
import weakref
from unittest.mock import Mock, patch

from iphone_detector import ipa_library
from iphone_detector.library_dialog import LibraryDialog
from tools.preview_gui import Preview


class LibraryDialogTests(unittest.TestCase):
    def test_closing_during_scan_does_not_keep_tk_objects_on_worker(self):
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def scan(config, **kwargs):
            started.set()
            release.wait(4)
            finished.set()
            return []

        with (
            patch("iphone_detector.library_dialog.load_config", return_value=self.app.config),
            patch("iphone_detector.ipa_library.scan", side_effect=scan),
        ):
            try:
                dialog = LibraryDialog(self.app)
                self.assertTrue(started.wait(2))
                closed = dialog.closed
                reference = weakref.ref(dialog)
                dialog.window.destroy()
                del dialog
                gc.collect()
                self.assertTrue(closed.is_set())
                self.assertIsNone(reference())
            finally:
                release.set()
                self.assertTrue(finished.wait(2))

    def test_controls_fit_at_minimum_window_size(self):
        self.root.tk.call("tk", "scaling", 96 / 72)
        self.root.deiconify()
        dialog = LibraryDialog(self.app, preview=True)
        dialog.window.geometry("720x560")
        self.root.update()
        for widget in (dialog.table, dialog.account, dialog.assign_button, dialog.refresh_button):
            with self.subTest(widget=widget):
                self.assertTrue(widget.winfo_ismapped())
                self.assertLessEqual(
                    widget.winfo_rooty() + widget.winfo_height(),
                    dialog.window.winfo_rooty() + dialog.window.winfo_height(),
                )
                self.assertLessEqual(
                    widget.winfo_rootx() + widget.winfo_width(),
                    dialog.window.winfo_rootx() + dialog.window.winfo_width(),
                )

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = Preview(self.root)

    def test_preview_search_selection_and_save_are_inert(self):
        with patch("threading.Thread.start", side_effect=AssertionError("Preview worker")):
            dialog = LibraryDialog(self.app, preview=True)
            dialog.query.set("com.example.video")
            self.assertEqual(len(dialog.table.get_children()), 2)
            dialog.table.selection_set("1")
            dialog.select()
            self.assertIn("Video 2.2.ipa", dialog.details.get())
            self.assertEqual(str(dialog.assign_button.cget("state")), "disabled")
            dialog.account.current(0)
            dialog.select()
            self.assertEqual(str(dialog.assign_button.cget("state")), "normal")
            with patch("iphone_detector.discord_inbox.assign_managed") as save:
                dialog.assign()
                save.assert_not_called()
                self.assertIn("no account assignment", dialog.status.get())
            dialog.query.set("not found")
            self.assertEqual(str(dialog.assign_button.cget("state")), "disabled")

    def test_loading_is_off_main_thread_and_ui_remains_usable(self):
        release = threading.Event()
        started = threading.Event()
        self.addCleanup(release.set)
        main_thread = threading.get_ident()

        def scan(config, **kwargs):
            self.assertNotEqual(main_thread, threading.get_ident())
            started.set()
            release.wait(4)
            return ipa_library.sample_entries()

        with (
            patch("iphone_detector.library_dialog.load_config", return_value=self.app.config),
            patch("iphone_detector.ipa_library.scan", side_effect=scan),
        ):
            dialog = LibraryDialog(self.app)
            self.assertTrue(started.wait(2))
            self.assertTrue(dialog.busy)
            dialog.query.set("video")
            self.root.update()
            self.assertEqual(str(dialog.assign_button.cget("state")), "disabled")
            release.set()
            until = time.monotonic() + 4
            while dialog.busy and time.monotonic() < until:
                self.root.update()
                time.sleep(0.01)
            self.assertFalse(dialog.busy)
            self.assertEqual(len(dialog.visible), 2)

    def test_assignment_uses_selected_signature_and_original_account_identity(self):
        dialog = LibraryDialog(self.app, preview=True)
        dialog.preview = False
        dialog.table.selection_set("3")
        dialog.account.current(1)
        operations = []
        dialog.work = lambda kind, operation: operations.append((kind, operation))
        dialog.assign()
        with patch("iphone_detector.discord_inbox.assign_managed") as save:
            self.assertEqual(operations[0][0], "assign")
            self.assertEqual(operations[0][1](), 2)
            item = ipa_library.sample_entries()[3]
            save.assert_called_once_with(
                item.path, item.signature, 1, expected_email="music@example.com"
            )

    def test_failed_scan_clears_old_entries_and_disables_assignment(self):
        dialog = LibraryDialog(self.app, preview=True)
        dialog.table.selection_set("0")
        dialog.events.put(("scan", None, "Folder unavailable"))
        dialog.poll()
        self.assertFalse(dialog.entries)
        self.assertFalse(dialog.accounts)
        self.assertEqual(str(dialog.assign_button.cget("state")), "disabled")
        self.assertEqual(dialog.status.get(), "Folder unavailable")

    def test_save_success_refreshes_main_form_and_keeps_confirmation(self):
        dialog = LibraryDialog(self.app, preview=True)
        self.app.sync_saved = Mock()
        dialog.events = queue.Queue()
        dialog.events.put(("assign", 2, ""))
        dialog.refresh = Mock()
        dialog.poll()
        self.app.sync_saved.assert_called_once()
        dialog.refresh.assert_called_once()
        dialog.events.put(("scan", (ipa_library.sample_entries(), dialog.accounts), ""))
        dialog.poll()
        self.assertIn("Saved for account 2", dialog.status.get())

    def test_refresh_clears_selection_when_account_identity_changes(self):
        dialog = LibraryDialog(self.app, preview=True)
        dialog.account.current(0)
        dialog.accounts[0]["email"] = "replacement@example.com"
        dialog.populate()
        self.assertEqual(dialog.account.current(), -1)
        self.assertEqual(dialog.account.get(), "Choose an account…")
