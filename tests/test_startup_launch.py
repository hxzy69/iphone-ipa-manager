from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

from iphone_detector.startup_task import start_existing


class StartupLaunchTests(unittest.TestCase):
    def setUp(self):
        self.arguments = [
            str(Path.cwd() / "manager.exe"),
            "--data-dir",
            str(Path.cwd() / "profile"),
            "--discord",
        ]
        self.saved = Mock(Enabled=True, State=3)
        self.task = self.saved.Definition
        self.task.Principal.UserId = "owner"
        self.task.Principal.RunLevel = 0
        self.task.Principal.LogonType = 3
        self.task.Actions.Count = 1
        self.action = self.task.Actions.Item.return_value
        self.action.Path = self.arguments[0]
        self.action.Arguments = subprocess.list2cmdline(self.arguments[1:])
        scheduler = Mock()
        scheduler.GetFolder.return_value.GetTask.return_value = self.saved
        self.dispatch = patch("win32com.client.Dispatch", return_value=scheduler)
        self.owner = patch("iphone_detector.bridge_service.current_sid", return_value="owner")
        self.dispatch.start()
        self.owner.start()
        self.addCleanup(self.dispatch.stop)
        self.addCleanup(self.owner.stop)

    def test_matching_task_starts_without_rewriting_definition(self):
        self.assertTrue(start_existing(self.arguments))
        self.saved.Run.assert_called_once_with(None)

    def test_running_task_is_not_started_twice(self):
        self.saved.State = 4
        self.assertTrue(start_existing(self.arguments))
        self.saved.Run.assert_not_called()

    def test_disabled_task_is_not_enabled(self):
        self.saved.Enabled = False
        self.assertFalse(start_existing(self.arguments))
        self.saved.Run.assert_not_called()

    def test_different_profile_and_privileged_tasks_are_rejected(self):
        self.action.Arguments = '--data-dir "C:/other" --discord'
        with self.assertRaises(ValueError):
            start_existing(self.arguments)
        self.action.Arguments = subprocess.list2cmdline(self.arguments[1:])
        self.task.Principal.RunLevel = 1
        with self.assertRaises(ValueError):
            start_existing(self.arguments)
        self.saved.Run.assert_not_called()
