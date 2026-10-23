"""Device-free release acceptance: new profile, CLI startup and live GUI controls."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from fixtures import configured
from iphone_detector import gui, job_queue


class ReleaseAcceptanceTests(unittest.TestCase):
    def test_fresh_profile_queue_runner_exits_without_touching_accounts(self):
        with tempfile.TemporaryDirectory() as folder:
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "ipa_manager.py"),
                    "--data-dir",
                    folder,
                    "--queue-runner",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
                env=dict(os.environ, IPA_MANAGER_DATA_DIR=folder),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((Path(folder) / "config.json").exists())
            self.assertFalse((Path(folder) / "queue-runner.json").exists())
            self.assertFalse((Path(folder) / "automation-runs").exists())

    def test_empty_profile_install_check_fails_without_launching_backend(self):
        import json

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            job = root / "automation-runs/gui-check"
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "ipa_manager.py"),
                    "--data-dir",
                    folder,
                    "--worker",
                    "check",
                    "--job-dir",
                    str(job),
                ],
                capture_output=True,
                timeout=30,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            self.assertEqual(result.returncode, 1)
            self.assertIn("error", json.loads((job / "result.json").read_text()))
            self.assertFalse((job / "backend-job.json").exists())

    def test_dashboard_constructs_with_empty_state_and_can_be_closed(self):
        root = tk.Tk()
        root.withdraw()
        with tempfile.TemporaryDirectory():
            try:
                with (
                    patch.object(gui, "load_config", return_value=configured()),
                    patch(
                        "iphone_detector.dashboard.snapshot",
                        return_value={
                            "accounts": [],
                            "connection": {
                                "route": "No recent installation",
                                "upload_mib_s": None,
                                "explanation": "Offline",
                            },
                            "renewal": "Disabled",
                            "queue": "Empty",
                        },
                    ),
                    patch.object(job_queue, "entries", return_value=[]),
                    patch.object(job_queue, "ensure_started"),
                    patch("iphone_detector.operations.jobs", return_value=[]),
                ):
                    app = gui.App(root)
                    app.open_dashboard()
                    import time

                    deadline = time.monotonic() + 3
                    while not app.management.health.get() and time.monotonic() < deadline:
                        root.update()
                        time.sleep(0.01)
                    self.assertEqual(app.management.accounts.get_children(), ())
                    self.assertIn("Offline", app.management.health.get())
                    app.open_dashboard()
                    app.management.window.destroy()
                    root.update()
            finally:
                root.destroy()
