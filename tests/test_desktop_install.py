"""Per-user install, repair rollback and conservative removal boundaries."""

from contextlib import ExitStack
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from iphone_detector import __version__, desktop_install as install
from iphone_detector.storage import write_json


class DesktopInstallTests(unittest.TestCase):
    def test_owned_path_accepts_equivalent_root_spelling(self):
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "child").mkdir()
        alias = self.root / "child" / ".."
        self.assertEqual(
            install.owned_path(alias, "app.exe").resolve(), self.root.resolve() / "app.exe"
        )

    def test_task_removal_is_limited_to_owner_installation_and_profile(self):
        self.stack.close()
        from iphone_detector.startup_task import remove_installation_tasks
        import subprocess

        def task(name, path, profile, elevated=False):
            action = Mock(
                Path=str(path),
                Arguments=subprocess.list2cmdline(["--discord", "--data-dir", str(profile)]),
            )
            definition = Mock()
            definition.Principal.UserId = "test-owner"
            definition.Principal.RunLevel = int(elevated)
            definition.Actions.Count = 1
            definition.Actions.Item.return_value = action
            return Mock(Name=name, Definition=definition)

        folder = Mock()
        tasks = [
            task("iPhone IPA Community Discord", self.root / "app.exe", self.data),
            task("iPhone IPA Community Automatic Refresh", self.root / "app.exe", self.data),
            task("iPhone IPA Community Queue", self.folder / "other/app.exe", self.data),
            task(
                "iPhone IPA Community Remote Tunnel",
                self.root / "app.exe",
                self.folder / "other-profile",
            ),
            task("Unrelated task", self.root / "app.exe", self.data),
        ]
        folder.GetTasks.side_effect = lambda flags: tasks if flags == 1 else tasks[:1]
        scheduler = Mock()
        scheduler.GetFolder.return_value = folder
        with (
            patch("win32com.client.Dispatch", return_value=scheduler),
            patch("iphone_detector.bridge_service.current_sid", return_value="test-owner"),
        ):
            remove_installation_tasks(self.root, self.data)
        folder.GetTasks.assert_called_once_with(1)
        self.assertEqual(
            [call.args for call in folder.DeleteTask.call_args_list],
            [("iPhone IPA Community Discord", 0), ("iPhone IPA Community Automatic Refresh", 0)],
        )

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.root = self.folder / "program"
        self.data = self.folder / "settings"
        self.data.mkdir()
        (self.data / "config.json").write_text('{"keep":true}')
        self.payload = self.folder / "payload"
        self.payload.mkdir()
        (self.payload / "iPhoneIPAManager.exe").write_bytes(b"MZ-test-candidate")
        write_json(
            self.payload / "self-test.json",
            {
                "ok": True,
                "frozen": True,
                "version": __version__,
                "checks": [{"ok": True}],
                "exe_sha256": hashlib.sha256(b"MZ-test-candidate").hexdigest(),
            },
        )
        (self.payload / "LICENSE.txt").write_text("test license")
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(install, "installation_root", return_value=self.root))
        self.stack.enter_context(patch.object(install, "DATA_ROOT", self.data))
        self.integrate = self.stack.enter_context(patch.object(install, "integrate"))
        self.remove_links = self.stack.enter_context(patch.object(install, "remove_integration"))
        self.processes = self.stack.enter_context(
            patch.object(install.psutil, "process_iter", return_value=[])
        )
        self.remove_tasks = self.stack.enter_context(
            patch("iphone_detector.startup_task.remove_installation_tasks")
        )
        self.retarget = self.stack.enter_context(patch("iphone_detector.startup_task.retarget"))

    def test_install_and_uninstall_preserve_unknown_files_and_private_settings(self):
        executable = install.install_payload(self.payload)
        self.assertEqual(executable.read_bytes(), b"MZ-test-candidate")
        self.assertTrue(executable.is_relative_to(self.root.resolve()))
        user_file = self.root / "my-notes.txt"
        user_file.write_text("keep this")
        install.uninstall(self.root)
        self.assertFalse(executable.exists())
        self.assertEqual(user_file.read_text(), "keep this")
        self.assertEqual((self.data / "config.json").read_text(), '{"keep":true}')
        self.remove_tasks.assert_called_once_with(self.root.resolve(), self.data)

    def test_repair_preserves_previous_executable_and_migrates_only_its_tasks(self):
        first = install.install_payload(self.payload)
        second = install.install_payload(self.payload)
        self.assertNotEqual(first, second)
        self.assertTrue(first.exists())
        self.retarget.assert_called_once_with(first, second, self.data)
        self.assertEqual(len(install.manifest(self.root)["files"]), 6)

    def test_failed_integration_rolls_back_and_retains_owned_staging_for_cleanup(self):
        first = install.install_payload(self.payload)
        self.integrate.side_effect = [OSError("shortcut unavailable"), None]
        with self.assertRaises(OSError):
            install.install_payload(self.payload)
        value = install.manifest(self.root)
        self.assertEqual(self.root / value["executable"], first)
        self.assertTrue(first.exists())
        self.assertEqual(len(value["files"]), 6)
        self.assertEqual(self.retarget.call_count, 2)
        install.uninstall(self.root)
        self.assertFalse(self.root.exists())

    def test_first_install_copy_failure_is_owned_and_removable(self):
        with patch.object(install.shutil, "copy2", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                install.install_payload(self.payload)
        self.assertIsNotNone(install.manifest(self.root))
        install.uninstall(self.root)
        self.assertFalse(self.root.exists())

    def test_tampered_payload_never_creates_installation(self):
        (self.payload / "iPhoneIPAManager.exe").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "receipt"):
            install.install_payload(self.payload)
        self.assertFalse(self.root.exists())
        self.integrate.assert_not_called()

    def test_running_app_blocks_install_and_uninstall(self):
        process = Mock(info={"exe": str(self.root / "versions/old/app.exe")})
        self.processes.return_value = [process]
        with self.assertRaisesRegex(ValueError, "Close"):
            install.install_payload(self.payload)
        self.remove_tasks.assert_not_called()
        self.assertFalse(self.root.exists())

    def test_unowned_folder_and_outside_manifest_targets_are_preserved(self):
        self.root.mkdir()
        (self.root / "existing.txt").write_text("keep")
        with self.assertRaisesRegex(ValueError, "ownership"):
            install.install_payload(self.payload)
        outside = self.folder / "outside.txt"
        outside.write_text("private")
        write_json(
            self.root / "installation.json",
            {
                "schema_version": 1,
                "files": ["../outside.txt"],
                "executable": "../outside.txt",
                "data_root": str(self.data),
            },
        )
        with self.assertRaisesRegex(ValueError, "repair"):
            install.uninstall(self.root)
        self.assertEqual(outside.read_text(), "private")
        self.remove_tasks.assert_not_called()

    def test_custom_profile_cannot_be_deleted_with_application_files(self):
        with patch.object(install, "DATA_ROOT", self.root / "private-data"):
            with self.assertRaisesRegex(ValueError, "private settings"):
                install.install_payload(self.payload)

    def test_real_shortcut_and_current_user_registration_are_removed_safely(self):
        import uuid
        import winreg

        key = "Software\\iPhoneIPAManagerTests\\" + uuid.uuid4().hex
        with (
            patch.object(install, "REGISTRY_KEY", key),
            patch.object(install, "shortcut_path", return_value=self.folder / "test.lnk"),
        ):
            # The service is mocked in other tests; exercise the real implementation
            # only against a unique test key and a shortcut in this temporary folder.
            self.stack.close()
            with (
                patch.object(install, "installation_root", return_value=self.root),
                patch.object(install, "DATA_ROOT", self.data),
                patch.object(install.psutil, "process_iter", return_value=[]),
                patch("iphone_detector.startup_task.remove_installation_tasks"),
            ):
                try:
                    executable = install.install_payload(self.payload)
                    self.assertTrue((self.folder / "test.lnk").is_file())
                    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as saved:
                        self.assertEqual(
                            winreg.QueryValueEx(saved, "DisplayVersion")[0], __version__
                        )
                        self.assertIn(
                            str(executable), winreg.QueryValueEx(saved, "UninstallString")[0]
                        )
                    install.uninstall(self.root)
                    self.assertFalse((self.folder / "test.lnk").exists())
                    with self.assertRaises(FileNotFoundError):
                        winreg.OpenKey(winreg.HKEY_CURRENT_USER, key)
                finally:
                    try:
                        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
                    except FileNotFoundError:
                        pass
