from fixtures import configured
import copy
import queue
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import tkinter as tk

from iphone_detector.gui_sync import fields, merge
from iphone_detector.gui import App


class GuiSyncTests(unittest.TestCase):
    def test_live_summary_preserves_unsaved_edits_and_reports_save_status(self):
        app, config = self.app()
        app.process = None
        app.external_active = False
        app.stop_button = Mock()
        app.sync_status = Mock()
        app.connection_summary = Mock()
        app.renewal_summary = Mock()
        app.live_events = queue.Queue()
        app.timeout.set("20")
        app.live_events.put(
            (config, [], "No active run.", "Remote tunnel ready.", "Due tomorrow.", None)
        )
        app.sync_live()
        self.assertEqual(app.timeout.get(), "20")
        self.assertIn("Unsaved changes", app.sync_status.set.call_args.args[0])
        app.connection_summary.set.assert_called_with("Remote tunnel ready.")
        app.renewal_summary.set.assert_called_with("Due tomorrow.")
        app.timeout.set(app.sync_baseline["timeout"])
        app.live_events.put(
            (config, [], "No active run.", "Remote tunnel ready.", "Due tomorrow.", None)
        )
        app.sync_live()
        self.assertIn("up to date", app.sync_status.set.call_args.args[0])

    def test_live_summary_does_not_keep_stale_ready_state_after_read_failure(self):
        app, _ = self.app()
        app.live_events = queue.Queue()
        app.sync_status = Mock()
        app.connection_summary = Mock()
        app.renewal_summary = Mock()
        app.live_events.put((None, None, None, None, None, "Unable to read state"))
        app.sync_live()
        self.assertIn("unavailable", app.connection_summary.set.call_args.args[0])
        self.assertIn("could not be verified", app.renewal_summary.set.call_args.args[0])

    def test_successful_save_normalizes_widgets_and_baseline_to_persisted_values(self):
        app, initial = self.app()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "child").mkdir()
            current = copy.deepcopy(initial)
            current["automation"]["ipa_directory"] = str(root)
            app.sync_saved(force=True, latest=current)
            app.folder.set(str(root / "child" / ".."))
            app.names[0].set(" SampleVideo 1.0.IPA ")
            with (
                patch("iphone_detector.gui.load_config", return_value=current),
                patch("iphone_detector.gui.save_config") as saved,
            ):
                self.assertTrue(app.save())
            expected = fields(saved.call_args.args[0])
            self.assertEqual(
                {key: value.get() for key, value in app.sync_variables().items()}, expected
            )
            self.assertEqual(app.sync_baseline, expected)

    def test_clean_fields_follow_saved_changes_while_edits_stay_local(self):
        baseline = {"folder": "old", "timeout": "15", "notify": True}
        local = dict(baseline, timeout="20")
        remote = dict(baseline, folder="new", notify=False)
        values, next_base, conflicts = merge(baseline, local, remote)
        self.assertEqual(values, {"folder": "new", "timeout": "20", "notify": False})
        self.assertEqual(next_base["timeout"], "15")
        self.assertEqual(conflicts, [])

    def test_conflict_persists_across_polls_until_user_accepts_saved_value(self):
        base, local, remote = {"timeout": "15"}, {"timeout": "20"}, {"timeout": "30"}
        for _ in range(3):
            local, base, conflicts = merge(base, local, remote)
            self.assertEqual(conflicts, ["timeout"])
            self.assertEqual(local["timeout"], "20")
        values, base, conflicts = merge(base, remote, remote)
        self.assertEqual(conflicts, [])
        self.assertEqual(base, values)

    def app(self):
        app = App.__new__(App)
        interp = tk.Tcl()
        config = configured({"automation": {"ipa_directory": "old-folder"}})
        saved = fields(config)
        for key, value in saved.items():
            if not key.startswith("ipa"):
                setattr(
                    app,
                    key,
                    tk.BooleanVar(interp, value=value)
                    if isinstance(value, bool)
                    else tk.StringVar(interp, value=value),
                )
        app.names = [tk.StringVar(interp, value=saved["ipa%d" % i]) for i in range(3)]
        app.sync_baseline = saved
        app.sync_conflicts = []
        app.email_labels = [Mock() for _ in range(3)]
        app.device_label = Mock()
        app.root = Mock()
        app.status = tk.StringVar(interp)
        return app, config

    def test_gui_live_sync_updates_all_clean_variables_and_preserves_dirty_ones(self):
        app, config = self.app()
        app.timeout.set("20")
        remote = copy.deepcopy(config)
        remote["automation"].update(
            ipa_directory="new-folder", install_timeout=1800, notify_on_finish=False
        )
        self.assertEqual(app.sync_saved(latest=remote), ["timeout"])
        self.assertEqual(app.timeout.get(), "20")
        self.assertEqual(app.folder.get(), "new-folder")
        self.assertFalse(app.notify_finish.get())
        with patch("iphone_detector.gui.load_config", return_value=remote):
            app.sync_saved(force=True)
        self.assertEqual(app.timeout.get(), "30")
        self.assertEqual(app.sync_conflicts, [])

    def test_save_refuses_conflict_instead_of_overwriting_discord(self):
        app, config = self.app()
        app.timeout.set("20")
        remote = copy.deepcopy(config)
        remote["automation"]["install_timeout"] = 1800
        with (
            patch("iphone_detector.gui.load_config", return_value=remote),
            patch("iphone_detector.gui.save_config") as save,
            patch("iphone_detector.gui.messagebox.showerror") as error,
        ):
            self.assertFalse(app.save())
            save.assert_not_called()
            self.assertIn("changed both", error.call_args.args[1])

    def test_save_merges_dirty_local_timeout_with_remote_folder_and_unexposed_state(self):
        app, config = self.app()
        app.timeout.set("20")
        with tempfile.TemporaryDirectory() as directory:
            exe = Path(directory) / "iloader.exe"
            exe.write_bytes(b"MZ")
            remote = copy.deepcopy(config)
            remote["launcher"] = {"app_path": str(exe)}
            remote["automation"].update(
                ipa_directory=directory, pending_ipa_installs={"2": "KeepMe"}
            )
            remote["device"] = {"serial": "latest-device"}
            with (
                patch("iphone_detector.gui.load_config", return_value=remote),
                patch("iphone_detector.gui.save_config") as save,
            ):
                self.assertTrue(app.save())
            saved = save.call_args.args[0]
            self.assertEqual(saved["automation"]["install_timeout"], 1200)
            self.assertTrue(Path(saved["automation"]["ipa_directory"]).samefile(directory))
            self.assertEqual(saved["automation"]["pending_ipa_installs"], {"2": "KeepMe"})
            self.assertEqual(saved["device"], {"serial": "latest-device"})
