"""COM ownership and notification shortcut preservation on Windows."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from iphone_detector import com_runtime, notify


class ComRuntimeTests(unittest.TestCase):
    def test_disable_absent_task_handles_direct_and_dispatch_wrapped_not_found(self):
        import pywintypes
        from iphone_detector import startup_task

        for error in (
            pywintypes.com_error(-2147024894, "not found", None, None),
            pywintypes.com_error(
                -2147352567, "dispatch", (0, None, None, None, 0, -2147024894), None
            ),
        ):
            with self.subTest(error=error):
                scheduler = Mock()
                scheduler.GetFolder.return_value.DeleteTask.side_effect = error
                with patch("win32com.client.Dispatch", return_value=scheduler):
                    startup_task.configure(False, [], name="fixture-task")

    def test_disable_task_does_not_swallow_wrapped_permission_error(self):
        import pywintypes
        from iphone_detector import startup_task

        scheduler = Mock()
        scheduler.GetFolder.return_value.DeleteTask.side_effect = pywintypes.com_error(
            -2147352567, "dispatch", (0, None, None, None, 0, -2147024891), None
        )
        with patch("win32com.client.Dispatch", return_value=scheduler):
            with self.assertRaises(pywintypes.com_error):
                startup_task.configure(False, [], name="fixture-task")

    def test_native_success_and_nested_initialization_are_balanced(self):
        for status in (0, 1):
            library = Mock()
            library.CoInitializeEx.return_value = status
            with patch.object(com_runtime, "ole32", return_value=library):
                owned = com_runtime.initialize()
                self.assertTrue(owned)
                com_runtime.uninitialize(owned)
            library.CoUninitialize.assert_called_once()

    def test_existing_mta_is_not_uninitialized(self):
        library = Mock()
        library.CoInitializeEx.return_value = -2147417850
        with patch.object(com_runtime, "ole32", return_value=library):
            owned = com_runtime.initialize()
            self.assertFalse(owned)
            com_runtime.uninitialize(owned)
        library.CoUninitialize.assert_not_called()

    def test_initialization_failure_does_not_claim_or_release_an_apartment(self):
        library = Mock()
        library.CoInitializeEx.return_value = -2147467259
        with patch.object(com_runtime, "ole32", return_value=library):
            with self.assertRaises(OSError):
                com_runtime.initialize()
        library.CoUninitialize.assert_not_called()

    def test_native_scope_preserves_a_real_callers_scheduler_proxy(self):
        import win32com.client

        scheduler = win32com.client.Dispatch("Schedule.Service")
        scheduler.Connect()
        folder = scheduler.GetFolder("\\")
        before = folder.GetTasks(0).Count
        owned = com_runtime.initialize()
        com_runtime.uninitialize(owned)
        self.assertEqual(folder.GetTasks(0).Count, before)

    def test_notification_keeps_existing_shortcut_target_and_profile_arguments(self):
        import win32com.client

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "Microsoft/Windows/Start Menu/Programs/iPhone IPA Manager Community.lnk"
            path.parent.mkdir(parents=True)
            shell = win32com.client.Dispatch("WScript.Shell")
            link = shell.CreateShortcut(str(path))
            link.TargetPath = sys.executable
            link.Arguments = subprocess.list2cmdline(["--data-dir", str(root / "private profile")])
            link.Save()
            original = (link.TargetPath, link.Arguments)
            link = None
            with (
                patch.dict("os.environ", {"APPDATA": str(root)}),
                patch.object(
                    notify.subprocess, "run", return_value=Mock(returncode=0, stdout='{"ok":true}')
                ),
            ):
                self.assertTrue(notify.notify("Test", "Test")["ok"])
            saved = shell.CreateShortcut(str(path))
            self.assertEqual((saved.TargetPath, saved.Arguments), original)
