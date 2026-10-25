"""Regression checks for larger text, long accounts, and keyboard form navigation."""

import tkinter as tk
from tkinter import ttk
import unittest
import _tkinter

from tools.preview_gui import Preview
from iphone_detector.dialog_ui import reveal_focus, shell


class GuiAccessibilityTests(unittest.TestCase):
    def test_small_screen_keeps_controls_visible_and_overview_reachable(self):
        root = tk.Tk()
        try:
            root.winfo_screenwidth = lambda: 1024
            root.winfo_screenheight = lambda: 768
            root.tk.call("tk", "scaling", 192 / 72)
            app = Preview(root)
            root.update()
            self.assertTrue(app.stop_button.winfo_ismapped())
            bottom = app.stop_button.winfo_rooty() + app.stop_button.winfo_height()
            self.assertLessEqual(bottom, root.winfo_rooty() + root.winfo_height())
            canvas = app.page_canvases[0]
            self.assertGreater(canvas.bbox("all")[3], canvas.winfo_height())
            canvas.yview_moveto(1)
            root.update()
            self.assertEqual(canvas.yview()[1], 1.0)
            self.assertGreater(app.activity.winfo_height(), 24)
        finally:
            root.destroy()

    def test_uninstaller_text_layout_settles_with_large_text(self):
        root = tk.Tk()
        try:
            root.tk.call("tk", "scaling", 144 / 72)
            app = Preview(root)
            root.update()
            window = app.installer(True)
            changes = []
            window.bind("<Configure>", lambda event: changes.append(event.width), add="+")
            for _ in range(1000):
                if not root.tk.dooneevent(_tkinter.DONT_WAIT):
                    break
            self.assertLess(len(changes), 100, "Label wrapping must not oscillate indefinitely")
        finally:
            root.destroy()

    def test_overview_retains_activity_with_large_text(self):
        for scaling in (96 / 72, 144 / 72, 192 / 72):
            with self.subTest(scaling=scaling):
                root = tk.Tk()
                try:
                    root.tk.call("tk", "scaling", scaling)
                    app = Preview(root)
                    root.geometry("820x640")
                    root.update()
                    self.assertGreaterEqual(app.activity.winfo_height(), 24)
                    self.assertLessEqual(root.winfo_width(), root.winfo_screenwidth())
                    self.assertLessEqual(root.winfo_height(), root.winfo_screenheight())
                    self.assertTrue(app.stop_button.winfo_ismapped())
                finally:
                    root.destroy()

    def test_long_email_wraps_without_removing_account_title(self):
        root = tk.Tk()
        try:
            app = Preview(root)
            app.workspace.select(1)
            app.add_account_card(
                3,
                {"email": "long-account-name." * 12 + "@example.com", "ipa": "Long filename " * 20},
            )
            root.update()
            email = app.email_labels[-1]
            self.assertGreater(email.winfo_height(), 20)
            self.assertLessEqual(
                email.winfo_rootx() + email.winfo_width(), root.winfo_rootx() + root.winfo_width()
            )
            app.config["device"] = {
                "name": "A very long phone name " * 20,
                "serial": "sample-device",
            }
            app._refresh_device()
            self.assertLess(len(app.next_step.get()), 160)
            app.workspace.select(2)
            root.update()
            pair = next(
                w for w in app.device_label.master.winfo_children() if isinstance(w, ttk.Button)
            )
            self.assertEqual(pair.winfo_width(), pair.winfo_reqwidth())
        finally:
            root.destroy()

    def test_keyboard_focus_reveals_offscreen_form_field(self):
        root = tk.Tk()
        try:
            dialog = tk.Toplevel(root)
            body, _ = shell(dialog, "Long form", height=420)
            entries = []
            for _ in range(24):
                entry = ttk.Entry(body)
                entry.pack(fill="x", pady=8)
                entries.append(entry)
            root.update()
            canvas = body.master
            reveal_focus(canvas, body, entries[-1])
            root.update()
            self.assertGreater(canvas.yview()[0], 0)
            self.assertGreaterEqual(entries[-1].winfo_rooty(), canvas.winfo_rooty())
            self.assertLessEqual(
                entries[-1].winfo_rooty() + entries[-1].winfo_height(),
                canvas.winfo_rooty() + canvas.winfo_height(),
            )
        finally:
            root.destroy()
