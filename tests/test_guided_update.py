"""Activation preflight, rollback and immutable download verification boundaries."""

import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from iphone_detector import guided_update as update
from iphone_detector import release_integrity as integrity
from iphone_detector.updates import Release, REPOSITORY
from iphone_detector.storage import write_json


def zipped(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


class GuidedUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for attribute, value in (
            ("DATA_ROOT", self.root),
            ("STATE", self.root / "update-state.json"),
            ("PREVIOUS", self.root / "previous.json"),
        ):
            item = patch.object(update, attribute, value)
            item.start()
            self.addCleanup(item.stop)

    def fixture(self):
        source_name = "iPhoneIPAManager-99.0.0-source.zip"
        windows_name = "iPhoneIPAManager-99.0.0-windows-x64.zip"
        receipt = {
            "version": "99.0.0",
            "ok": True,
            "frozen": True,
            "checks": [{"ok": True}],
            "exe_sha256": hashlib.sha256(b"MZfixture").hexdigest(),
        }
        files = {source_name: zipped({"README.md": b"fixture"})}
        files[windows_name] = zipped(
            {
                "iPhoneIPAManager.exe": b"MZfixture",
                "self-test.json": json.dumps(receipt),
                "source/" + source_name: files[source_name],
            }
        )
        files["SHA256SUMS.txt"] = "".join(
            hashlib.sha256(data).hexdigest() + "  " + name + "\n" for name, data in files.items()
        ).encode()
        return files

    def test_prepare_verifies_all_archives_before_creating_activation_plan(self):
        files = self.fixture()

        def download(url, path, limit):
            path.write_bytes(files[path.name])

        with (
            patch.object(update, "download", side_effect=download),
            patch.object(update.subprocess, "Popen") as spawn,
        ):
            plan = update.prepare(
                Release("99.0.0-preview", REPOSITORY + "/releases/tag/v99.0.0-preview")
            )
        _, executable = update.validate_plan(plan)
        self.assertEqual(executable.read_bytes(), b"MZfixture")
        spawn.assert_not_called()
        executable.write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "changed"):
            update.validate_plan(plan)

    def test_checksum_failure_never_prepares_executable(self):
        files = self.fixture()
        files["iPhoneIPAManager-99.0.0-windows-x64.zip"] = b"corrupt"
        with patch.object(
            update,
            "download",
            side_effect=lambda url, path, limit: path.write_bytes(files[path.name]),
        ):
            with self.assertRaisesRegex(ValueError, "checksum"):
                update.prepare(
                    Release("99.0.0-preview", REPOSITORY + "/releases/tag/v99.0.0-preview")
                )
        self.assertEqual(list(self.root.glob("updates/*/ready.json")), [])

    def test_unofficial_release_is_rejected_before_network_access(self):
        with patch.object(update, "download") as download:
            with self.assertRaises(ValueError):
                update.prepare(Release("99.0.0", "https://example.com/v99.0.0"))
            download.assert_not_called()

    def test_windows_aliases_and_case_collisions_are_rejected(self):
        for files in ({"CON.txt": b"x"}, {"a.": b"x"}, {"file": b"a", "FILE": b"b"}):
            with self.subTest(files=files), zipfile.ZipFile(io.BytesIO(zipped(files))) as archive:
                with self.assertRaises(ValueError):
                    integrity.verify_members(archive)

    def activation(self, failure=None, pending=None, runtime_failure=None):
        old, new = self.root / "old.exe", self.root / "new.exe"
        old.write_bytes(b"MZold")
        new.write_bytes(b"MZnew")
        plan = self.root / "ready.json"
        write_json(
            plan, {"executable": str(new), "sha256": hashlib.sha256(new.read_bytes()).hexdigest()}
        )
        parent = Mock(create_time=lambda: 1.0, exe=lambda: str(old), cmdline=lambda: [])
        with (
            patch.object(update.sys, "executable", str(old)),
            patch.object(update.psutil, "Process", return_value=parent),
            patch.object(update.psutil, "process_iter", return_value=[]),
            patch("iphone_detector.job_queue.pending", return_value=pending or []),
            patch.object(update, "wait_for_exit"),
            patch.object(update, "check_runtime", side_effect=runtime_failure) as check,
            patch.object(update, "configure_tasks", side_effect=failure) as tasks,
            patch.object(update, "launch") as launch,
        ):
            code = update.activate(plan, 123)
        return code, old, new, tasks, launch, check

    def test_successful_activation_preserves_old_executable_and_records_rollback(self):
        code, old, new, tasks, launch, check = self.activation()
        self.assertEqual(code, 0)
        self.assertTrue(old.exists())
        tasks.assert_called_once_with(old, new)
        check.assert_called_once_with(new, None)
        self.assertEqual(json.loads(update.PREVIOUS.read_text())["executable"], str(old))
        self.assertEqual(json.loads(update.STATE.read_text())["phase"], "complete")
        self.assertEqual(launch.call_args.args[0], new)

    def test_partial_task_migration_failure_rolls_back_before_relaunching_old_app(self):
        code, old, new, tasks, launch, _ = self.activation(
            failure=[OSError("simulated migration failure"), None]
        )
        self.assertEqual(code, 1)
        self.assertEqual(tasks.call_args.args, (new, old))
        self.assertEqual(launch.call_args.args[0], old)
        self.assertTrue(launch.call_args.kwargs["gui"])

    def test_pending_queue_blocks_activation_before_shutdown(self):
        code, _, _, tasks, launch, check = self.activation(pending=[{"state": "queued"}])
        self.assertEqual(code, 1)
        tasks.assert_not_called()
        launch.assert_not_called()
        check.assert_not_called()
        self.assertFalse((self.root / "remote-stop").exists())

    def test_failed_runtime_check_preserves_running_components(self):
        code, _, _, tasks, launch, _ = self.activation(
            runtime_failure=ValueError("Failed runtime check")
        )
        self.assertEqual(code, 1)
        tasks.assert_not_called()
        launch.assert_not_called()
        self.assertFalse((self.root / "remote-stop").exists())

    def test_retarget_preserves_periodic_triggers_and_arguments(self):
        from iphone_detector.startup_task import retarget

        old, new = self.root / "old.exe", self.root / "new.exe"
        import subprocess

        arguments = subprocess.list2cmdline(["--data-dir", str(self.root), "--auto-refresh"])
        action = Mock(Path=str(old), Arguments=arguments)
        task = Mock()
        task.Principal.RunLevel = 0
        task.Principal.UserId = "fixture-owner"
        task.Actions.Count = 1
        task.Actions.Item.return_value = action
        trigger = task.Triggers
        saved = Mock()
        saved.Name = "iPhone IPA Community Automatic Refresh"
        saved.Definition = task
        folder = Mock()
        folder.GetTasks.return_value = [saved]
        scheduler = Mock()
        scheduler.GetFolder.return_value = folder
        with (
            patch("win32com.client.Dispatch", return_value=scheduler),
            patch("iphone_detector.bridge_service.current_sid", return_value="fixture-owner"),
        ):
            retarget(old, new, self.root)
        self.assertEqual(action.Path, str(new))
        self.assertEqual(action.Arguments, arguments)
        self.assertIs(task.Triggers, trigger)
        folder.RegisterTaskDefinition.assert_called_once()

    def test_elevated_startup_task_is_not_modified(self):
        from iphone_detector.startup_task import retarget

        saved = Mock()
        saved.Name = "iPhone IPA Community Discord"
        saved.Definition.Principal.RunLevel = 1
        saved.Definition.Principal.UserId = "fixture-owner"
        folder = Mock()
        folder.GetTasks.return_value = [saved]
        scheduler = Mock()
        scheduler.GetFolder.return_value = folder
        with (
            patch("win32com.client.Dispatch", return_value=scheduler),
            patch("iphone_detector.bridge_service.current_sid", return_value="fixture-owner"),
            self.assertRaises(ValueError),
        ):
            retarget(self.root / "old.exe", self.root / "new.exe")
        folder.RegisterTaskDefinition.assert_not_called()

    def test_failed_component_launch_cleans_up_only_owned_new_processes(self):
        process = Mock(pid=123, poll=lambda: None)
        child = Mock()
        with (
            patch.object(
                update.subprocess, "Popen", side_effect=[process, OSError("could not launch")]
            ),
            patch.object(
                update.psutil, "Process", return_value=Mock(children=lambda recursive: [child])
            ),
        ):
            with self.assertRaises(OSError):
                update.launch(
                    self.root / "new.exe", {"discord": True, "remote": True, "queue": False}
                )
        child.terminate.assert_called_once()
        process.terminate.assert_called_once()
