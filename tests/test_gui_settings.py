"""Verify real Tk field bindings persist future filenames without starting automation."""

from fixtures import configured
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from iphone_detector import config, gui
from iphone_detector.settings import get_workflow


class GuiSettingsTests(unittest.TestCase):
    def test_update_check_keeps_tk_responsive_and_does_not_change_workers(self):
        import threading
        import time
        from tkinter import ttk
        from iphone_detector.updates import Release, REPOSITORY

        root = tk.Tk()
        root.withdraw()
        ready = threading.Event()
        thread_ids = []
        main_thread = threading.get_ident()

        def slow_check():
            thread_ids.append(threading.get_ident())
            ready.wait(2)
            return Release("99.0.0", REPOSITORY + "/releases/tag/v99.0.0")

        def widgets(widget):
            for child in widget.winfo_children():
                yield child
                yield from widgets(child)

        try:
            with (
                patch.object(gui, "load_config", return_value=configured()),
                patch("iphone_detector.updates.check_updates", side_effect=slow_check),
                patch.object(gui.subprocess, "Popen") as spawn,
                patch("webbrowser.open") as browse,
            ):
                app = gui.App(root)
                app.open_updates()
                dialog = app.updates_dialog
                app.open_updates()
                self.assertIs(dialog, app.updates_dialog)
                buttons = {w.cget("text"): w for w in widgets(dialog) if isinstance(w, ttk.Button)}
                buttons["Check for updates"].invoke()
                root.update()
                self.assertTrue(buttons["Check for updates"].instate(["disabled"]))
                ready.set()
                deadline = time.monotonic() + 3
                while (
                    buttons["Check for updates"].instate(["disabled"])
                    and time.monotonic() < deadline
                ):
                    root.update()
                    time.sleep(0.01)
                self.assertTrue(buttons["Check for updates"].instate(["!disabled"]))
                self.assertNotEqual(thread_ids, [main_thread])
                self.assertEqual(len(thread_ids), 1)
                buttons["Open release and downloads"].invoke()
                browse.assert_called_once_with(REPOSITORY + "/releases/tag/v99.0.0")
                spawn.assert_not_called()
                self.assertIsNone(app.process)
        finally:
            ready.set()
            root.destroy()

    def test_gui_jobs_use_the_same_queue_as_the_remote_daemon(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            try:
                with (
                    patch.object(gui, "DATA_ROOT", Path(directory)),
                    patch.object(gui, "load_config", return_value=configured()),
                    patch.object(gui.App, "save", return_value=True),
                    patch.object(gui.subprocess, "Popen"),
                ):
                    app = gui.App(root)
                    app.start("check")
                    self.assertEqual(app.job.parent, Path(directory) / "automation-runs")
                    self.assertTrue(app.job.name.startswith("gui-"))
            finally:
                root.destroy()

    def test_gui_cannot_save_during_installation(self):
        with tempfile.TemporaryDirectory():
            root = tk.Tk()
            root.withdraw()
            try:
                with (
                    patch.object(gui, "load_config", return_value=configured()),
                    patch("iphone_detector.run_lock.RunLock.acquire", return_value=False),
                    patch.object(gui, "save_config") as save,
                    patch.object(gui.messagebox, "showerror") as error,
                ):
                    app = gui.App(root)
                    self.assertFalse(app.save())
                    save.assert_not_called()
                    error.assert_called_once()
            finally:
                root.destroy()

    def test_unedited_gui_preserves_new_discord_assignment(self):
        import copy

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            executable = folder / "iloader.exe"
            executable.write_bytes(b"test")
            initial = configured(
                {
                    "launcher": {"app_path": str(executable)},
                    "automation": {"ipa_directory": str(folder)},
                }
            )
            latest = copy.deepcopy(initial)
            latest["automation"]["accounts"] = [
                {"email": entry["email"], "ipa": entry["ipa"]} for entry in get_workflow(initial)
            ]
            latest["automation"]["accounts"][1]["ipa"] = "New Discord IPA"
            latest["automation"]["pending_ipa_installs"] = {"2": "New Discord IPA"}
            root = tk.Tk()
            root.withdraw()
            try:
                with (
                    patch.object(gui, "load_config", side_effect=[initial, latest]),
                    patch.object(config, "CONFIG_PATH", folder / "config.json"),
                ):
                    app = gui.App(root)
                    self.assertTrue(app.save())
                    self.assertEqual(
                        get_workflow(config.load_config())[1]["ipa"], "New Discord IPA"
                    )
                    self.assertEqual(app.names[1].get(), "New Discord IPA.ipa")
            finally:
                root.destroy()

    def test_filename_field_survives_save_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            executable = folder / "iloader.exe"
            executable.write_bytes(b"test")
            initial = configured(
                {
                    "launcher": {"app_path": str(executable)},
                    "automation": {"ipa_directory": str(folder)},
                }
            )
            root = tk.Tk()
            root.withdraw()
            try:
                with (
                    patch.object(gui, "load_config", return_value=initial),
                    patch.object(config, "CONFIG_PATH", folder / "config.json"),
                ):
                    app = gui.App(root)
                    app.names[2].set("TwitchUnblock-9.9.9.ipa")
                    self.assertTrue(app.save())
                    saved = config.load_config()
                    self.assertEqual(get_workflow(saved)[2]["ipa"], "TwitchUnblock-9.9.9")
                    self.assertEqual(get_workflow(saved)[0]["ipa"], app.accounts[0]["ipa"])
                    self.assertFalse(app.process)
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
