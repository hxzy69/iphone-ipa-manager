import gc
import threading
import time
import tkinter as tk
import unittest
import weakref
from unittest.mock import Mock, patch
from tkinter import font, ttk

from tools.preview_gui import Preview
from iphone_detector.library_dialog import LibraryDialog
from iphone_detector.product_dialogs import (
    AssignmentHistoryDialog,
    RunReviewDialog,
    SetupWizard,
    ProgressDialog,
    AsyncWindow,
)


class ProductDialogTests(unittest.TestCase):
    def test_review_shows_full_default_operation_and_file_without_extra_selection(self):
        self.root.deiconify()
        review = RunReviewDialog(self.app, "install", Mock(), preview=True)
        self.root.update()
        operation = review.review["rows"][0]["operation"]
        tree_font = font.Font(
            font=ttk.Style(self.root).lookup("Treeview", "font") or "TkDefaultFont"
        )
        self.assertGreaterEqual(
            review.list.column("Operation", "width"), tree_font.measure(operation) + 8
        )
        self.assertIn("Video 2.2.ipa", review.detail.get())
        self.assertIn("video@example.com", review.detail.get())

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.app = Preview(self.root)

    def test_all_new_previews_and_actions_are_inert(self):
        with (
            patch("threading.Thread.start", side_effect=AssertionError("Preview worker")),
            patch(
                "iphone_detector.config.load_config",
                side_effect=AssertionError("Read real settings"),
            ),
            patch(
                "iphone_detector.config.save_config",
                side_effect=AssertionError("Saved real settings"),
            ),
        ):
            history = AssignmentHistoryDialog(self.app, preview=True)
            history.undo()
            self.assertIn("no assignment", history.status.get())
            review = RunReviewDialog(self.app, "install", self.fail, preview=True)
            review.start()
            self.assertIn("no job", review.status.get())
            wizard = SetupWizard(self.app, preview=True)
            wizard.open_step()
            self.assertIn("no IPA folder", wizard.status.get())
            for index in range(6):
                wizard.index = index
                wizard.show_step()
                self.assertIn("Sample status", wizard.check.get())
            progress = ProgressDialog(self.app, preview=True)
            self.assertIn("1m 24s", progress.status.get())
            self.assertEqual(len(progress.list.get_children()), 3)

    def test_update_action_selects_only_the_newer_ipa_for_chosen_account(self):
        dialog = LibraryDialog(self.app, preview=True)
        dialog.account.current(0)
        dialog.select()
        self.assertIn("2.1 → 2.2", dialog.update_note.get())
        dialog.use_update()
        self.assertEqual(dialog.selected().path.name, "Video 2.2.ipa")
        self.assertIn("no account assignment", dialog.status.get())

    def test_reload_review_can_recover_after_an_unreadable_ipa(self):
        review = RunReviewDialog(self.app, "install", Mock(), preview=True)
        sample = review.review
        review.preview = False
        operations = []
        review.submit = lambda operation, complete: operations.append((operation, complete))
        with (
            patch("iphone_detector.product_dialogs.load_config", return_value={}),
            patch(
                "iphone_detector.run_review.plan",
                return_value=sample,
            ),
        ):
            review.refresh()
            self.assertIsNone(review.review)
            self.assertEqual(str(review.start_button.cget("state")), "disabled")
            value = operations[0][0]()
        operations[0][1](value)
        self.assertIsNotNone(review.review)

    def test_completion_may_close_window_without_rescheduling_poll(self):
        dialog = AsyncWindow(self.app, "Test", "Read-only test")
        dialog.callbacks["finish"] = lambda value: dialog.window.destroy()
        dialog.events.put((None, "", "finish"))
        dialog.poll()
        self.assertFalse(dialog.window.winfo_exists())

    def test_closing_does_not_leave_tk_callbacks_on_background_thread(self):
        started, release, finished = threading.Event(), threading.Event(), threading.Event()

        def operation():
            started.set()
            release.wait(4)
            finished.set()
            return "done"

        dialog = AsyncWindow(self.app, "Test", "Read-only test")
        dialog.submit(operation, lambda value, owner=dialog: owner.status.set(value))
        self.assertTrue(started.wait(2))
        reference = weakref.ref(dialog)
        dialog.window.destroy()
        del dialog
        gc.collect()
        try:
            self.assertIsNone(reference())
        finally:
            release.set()
            self.assertTrue(finished.wait(2))

    def test_wizard_checks_are_async_and_navigation_waits_for_result(self):
        wizard = SetupWizard(self.app, preview=True)
        wizard.preview = False
        started, release = threading.Event(), threading.Event()

        def inspect():
            started.set()
            release.wait(4)
            return {
                "checks": [
                    {"code": "files", "status": "ok", "detail": "Folder ready", "action": ""}
                ]
            }

        with patch("iphone_detector.setup_flow.inspect", side_effect=inspect):
            try:
                wizard.refresh()
                self.assertTrue(started.wait(2))
                wizard.move(1)
                self.assertEqual(wizard.index, 0)
                release.set()
                until = time.monotonic() + 3
                while wizard.busy and time.monotonic() < until:
                    self.root.update()
                    time.sleep(0.01)
                self.assertIn("Folder ready", wizard.check.get())
            finally:
                release.set()
