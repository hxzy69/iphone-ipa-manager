"""Fresh-profile behavior and local account dialogue boundaries."""

import copy
import queue
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch
import zipfile

from iphone_detector import account_login, auto_refresh, gui, paths, settings
from iphone_detector.account_dialog import AccountDialog
from iphone_detector.setup import preflight


def sample_ipa(folder, name="ExampleApp.ipa"):
    path = folder / name
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("Payload/Example.app/Info.plist", b"example")
    return path


class FreshProfileTests(unittest.TestCase):
    def test_no_implicit_accounts_or_personal_defaults(self):
        self.assertEqual(settings.DEFAULT_ACCOUNTS, [])
        self.assertEqual(settings.get_workflow({}), [])
        self.assertEqual(settings.get_workflow({"automation": {"accounts": []}}), [])

    def test_one_and_two_account_workflows_are_valid(self):
        for count in (1, 2):
            accounts = [
                {"email": f"account{i}@example.com", "ipa": f"Example{i}"} for i in range(count)
            ]
            self.assertEqual(
                len(settings.get_workflow({"automation": {"accounts": accounts}})), count
            )

    def test_duplicate_accounts_are_rejected_case_insensitively(self):
        entries = [
            {"email": "Owner@example.com", "ipa": "First"},
            {"email": "owner@EXAMPLE.com", "ipa": "Second"},
        ]
        with self.assertRaisesRegex(ValueError, "different email"):
            settings.get_workflow({"automation": {"accounts": entries}})

    def test_first_folder_can_be_saved_without_iloader_or_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            saved = settings.update_settings({}, directory, [])
            self.assertEqual(saved["automation"]["accounts"], [])
            self.assertTrue(saved["automation"]["background_mode"])
            self.assertNotIn("launcher", saved)
            self.assertFalse(saved["automation"]["close_gui_after_success"])

    def test_empty_run_is_never_success(self):
        from iphone_detector.background import selected_accounts

        with self.assertRaisesRegex(ValueError, "Add an Apple account"):
            selected_accounts([])
        self.assertFalse(
            auto_refresh.verified_success({"ok": True, "completed_accounts": 0}, expected=0)
        )

    def test_first_account_is_appended_and_marked_for_full_install(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = sample_ipa(folder)
            before = {"automation": {"ipa_directory": directory, "accounts": []}, "unrelated": 42}
            snapshot = copy.deepcopy(before)
            with (
                patch("iphone_detector.config.load_config", return_value=before),
                patch("iphone_detector.config.save_config") as save,
                patch.object(account_login.RunLock, "acquire", return_value=True),
            ):
                stat = source.stat()
                self.assertEqual(
                    account_login.append_account(
                        "Owner@Example.com", source, (stat.st_size, stat.st_mtime_ns)
                    ),
                    1,
                )
            result = save.call_args.args[0]
            self.assertEqual(result["automation"]["accounts"][0]["email"], "owner@example.com")
            self.assertEqual(result["automation"]["pending_ipa_installs"], {"1": "ExampleApp"})
            self.assertEqual(result["unrelated"], snapshot["unrelated"])

    def test_no_fallback_from_user_state_to_checkout_config(self):
        from iphone_detector import config

        with tempfile.TemporaryDirectory() as directory:
            with patch.object(config, "CONFIG_PATH", Path(directory) / "missing.json"):
                self.assertIsNone(config.load_config())

    def test_custom_data_directory_requires_absolute_path(self):
        with patch.dict("os.environ", {"IPA_MANAGER_DATA_DIR": "relative/path"}):
            with self.assertRaisesRegex(ValueError, "absolute"):
                paths.data_directory()

    def test_preflight_uses_saved_files_without_device_access(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            sample_ipa(folder)
            native = folder / "tools/optimized-iloader/iloader-wifi.exe"
            native.parent.mkdir(parents=True)
            native.write_bytes(b"MZ")
            config = {
                "device": {"serial": "example-device"},
                "automation": {
                    "ipa_directory": directory,
                    "accounts": [{"email": "owner@example.com", "ipa": "ExampleApp"}],
                },
            }
            with patch("iphone_detector.setup.ASSET_ROOT", folder):
                result = preflight(config)
            self.assertEqual(result, {"ok": True, "checked_accounts": 1, "device_accessed": False})

    def test_fresh_gui_saves_empty_setup_and_requires_account_before_install(self):
        with tempfile.TemporaryDirectory() as directory:
            root = tk.Tk()
            root.withdraw()
            try:
                with (
                    patch.object(gui, "load_config", return_value={}),
                    patch.object(gui, "save_config") as save,
                    patch.object(gui.messagebox, "showinfo") as info,
                    patch.object(gui.subprocess, "Popen") as process,
                ):
                    app = gui.App(root)
                    app.folder.set(directory)
                    app.start("run")
                    save.assert_called_once()
                    info.assert_called_once()
                    process.assert_not_called()
                    self.assertEqual(app.names, [])
            finally:
                root.destroy()


class AccountDialogueTests(unittest.TestCase):
    def test_no_files_prevents_sign_in_before_window_or_native_process(self):
        with (
            patch("iphone_detector.account_dialog.managed_files", return_value=[]),
            patch("iphone_detector.account_dialog.tk.Toplevel") as window,
        ):
            with self.assertRaisesRegex(ValueError, "Put an IPA"):
                AccountDialog(Mock(), {}, Mock())
            window.assert_not_called()

    def test_verified_completion_enrolls_exact_snapshot_once(self):
        dialog = AccountDialog.__new__(AccountDialog)
        dialog.closed = False
        dialog.events = queue.Queue()
        dialog.events.put({"stage": "complete", "email": "owner@example.com"})
        dialog.source, dialog.signature = Path("ExampleApp.ipa"), (100, 200)
        dialog.window, dialog.on_saved = Mock(), Mock()
        with patch("iphone_detector.account_dialog.append_account", return_value=1) as append:
            dialog.poll()
            dialog.poll()
        append.assert_called_once_with("owner@example.com", dialog.source, (100, 200))
        dialog.on_saved.assert_called_once_with(1)
        dialog.window.destroy.assert_called_once()

    def test_failed_authentication_does_not_enroll_an_account(self):
        dialog = AccountDialog.__new__(AccountDialog)
        dialog.closed = False
        dialog.events = queue.Queue()
        dialog.events.put({"stage": "failed", "message": "Verification failed"})
        dialog.window, dialog.status, dialog.start_button, dialog.picker = (
            Mock(),
            Mock(),
            Mock(),
            Mock(),
        )
        with patch("iphone_detector.account_dialog.append_account") as append:
            dialog.poll()
        append.assert_not_called()
        self.assertFalse(dialog.running)


if __name__ == "__main__":
    unittest.main()
